package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/url"
	"os"
	"os/signal"
	"runtime/debug"
	"syscall"
	"time"
)

const maxInputBytes = 32 * 1024 * 1024
const maxRecordBytes = 1024 * 1024

func validateBaseURL(raw string) error {
	u, err := url.Parse(raw)
	if err != nil {
		return fmt.Errorf("parse base URL: %w", err)
	}
	ip := net.ParseIP(u.Hostname())
	if u.Scheme != "http" || u.User != nil || u.RawQuery != "" || u.Fragment != "" ||
		(u.Hostname() != "localhost" && (ip == nil || !ip.IsLoopback())) {
		return errors.New("this isolated experiment requires an HTTP loopback base URL without credentials/query/fragment")
	}
	return nil
}

func run(ctx context.Context, args []string) error {
	flags := flag.NewFlagSet("synthesis-driver", flag.ContinueOnError)
	inputPath := flags.String("input", "-", "Input JSONL path or - for stdin")
	outputPath := flags.String("output", "", "New JSONL result path (existing evidence is never overwritten)")
	baseURL := flags.String("base-url", "", "Local OpenAI-compatible base URL, including /v1")
	modelName := flags.String("model", "", "Actual served model alias")
	captureOnly := flags.Bool("capture-only", false, "Capture actual synthesizer requests without HTTP or model inference")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 || *outputPath == "" || *modelName == "" {
		return errors.New("--output and --model are required; no positional arguments")
	}
	if !*captureOnly {
		if err := validateBaseURL(*baseURL); err != nil {
			return err
		}
	}
	var reader io.Reader = os.Stdin
	if *inputPath != "-" {
		file, err := os.Open(*inputPath)
		if err != nil {
			return fmt.Errorf("open input: %w", err)
		}
		defer file.Close()
		reader = file
	}
	inputBytes, err := io.ReadAll(io.LimitReader(reader, maxInputBytes+1))
	if err != nil {
		return fmt.Errorf("read input: %w", err)
	}
	if len(inputBytes) > maxInputBytes {
		return errors.New("input exceeds 32 MiB")
	}
	// Validate the complete frozen batch before any HTTP call.
	inputs := []inputRecord{}
	seen := map[string]bool{}
	scanner := bufio.NewScanner(bytes.NewReader(inputBytes))
	scanner.Buffer(make([]byte, 65536), maxRecordBytes)
	line := 0
	for scanner.Scan() {
		line++
		if len(bytes.TrimSpace(scanner.Bytes())) == 0 {
			continue
		}
		input, err := decodeInput(scanner.Bytes())
		if err != nil {
			return fmt.Errorf("input line %d: %w", line, err)
		}
		if seen[input.ID] {
			return fmt.Errorf("input line %d duplicates id %q", line, input.ID)
		}
		seen[input.ID] = true
		inputs = append(inputs, input)
	}
	if err := scanner.Err(); err != nil {
		return fmt.Errorf("scan bounded input: %w", err)
	}
	if len(inputs) == 0 {
		return errors.New("input contains no records")
	}
	output, err := os.OpenFile(*outputPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return fmt.Errorf("create new result: %w", err)
	}
	defer output.Close()
	provenancePath := *outputPath + ".provenance.json"
	provenance, err := os.OpenFile(provenancePath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return fmt.Errorf("create new provenance: %w", err)
	}
	defer provenance.Close()
	inputHash := sha256.Sum256(inputBytes)
	build, _ := debug.ReadBuildInfo()
	metadata := map[string]any{
		"kind": "actual-semstreams-answer-synthesis-evaluation", "semstreams_version": semstreamsVersion,
		"input_path": *inputPath, "input_sha256": hex.EncodeToString(inputHash[:]), "records": len(inputs),
		"base_url": *baseURL, "model": *modelName, "capture_only": *captureOnly,
		"build": build, "started_at": time.Now().UTC().Format(time.RFC3339Nano),
		"protocol": map[string]any{
			"synthesizer":  "graphquery.NewLLMAnswerSynthesizer, timeout=0 selects production15s",
			"client":       "graph/llm.NewOpenAIClient, SDK default, maxRetries=3 gives at most4 attempts under same synthesis deadline",
			"settings":     "Actual builder controls max_tokens=500, temperature=0.3. No added schema, cache, seed or thinking override.",
			"wire_capture": "Typed ChatRequest/ChatResponse plus net/http/httptrace events. No raw HTTP request/response bodies or per-attempt status bodies captured; requests_written is WroteRequest count, not a guarantee of server acceptance.",
			"capture_only": "Runs the actual prompt builder through a recording llm.Client; skips HTTP and omits dummy synthesis outcome. Not an inference result.",
			"input_scope":  "Caller-supplied captured CommunitySummary values. Driver does not retrieve, mutate, rank, label or invent summaries.",
		},
	}
	if err := json.NewEncoder(provenance).Encode(metadata); err != nil {
		return fmt.Errorf("write provenance: %w", err)
	}
	logger := slog.New(slog.NewJSONHandler(os.Stderr, nil))
	recorder, err := newRecordedClient(*baseURL, *modelName, *captureOnly, logger)
	if err != nil {
		return err
	}
	defer recorder.Close()
	encoder := json.NewEncoder(output)
	for _, input := range inputs {
		if err := ctx.Err(); err != nil {
			return fmt.Errorf("batch interrupted before %s: %w", input.ID, err)
		}
		result := execute(ctx, input, recorder, logger)
		if err := encoder.Encode(result); err != nil {
			return fmt.Errorf("write result %s: %w", input.ID, err)
		}
		if err := output.Sync(); err != nil {
			return fmt.Errorf("persist result %s: %w", input.ID, err)
		}
		if err := ctx.Err(); err != nil {
			return fmt.Errorf("batch interrupted during %s (result preserved): %w", input.ID, err)
		}
		fmt.Fprintf(os.Stderr, "%s: %s, %.1fms, %d synthesis call(s)\n", result.ID, result.Mode, result.DurationMS, len(result.Calls))
	}
	return nil
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := run(ctx, os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "synthesis driver:", err)
		os.Exit(1)
	}
}
