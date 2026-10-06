// query-routing-driver exercises the pinned SemStreams classifiers without an LLM.
package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math"
	"os"
	"os/signal"
	"runtime/debug"
	"strings"
	"syscall"
	"time"

	"github.com/c360studio/semstreams/graph/query"
)

const (
	semstreamsVersion = "v1.0.0-beta.160"
	maxInputBytes     = 1024 * 1024
	maxTextBytes      = 8192
	maxOutputBytes    = 32 * 1024 * 1024
)

type queryInput struct {
	ID   string `json:"id"`
	Text string `json:"text"`
}

type batchInput struct {
	Arm       string                `json:"arm"`
	Threshold *float64              `json:"threshold"`
	Examples  *query.DomainExamples `json:"examples"`
	Queries   []queryInput          `json:"queries"`
}

func decodeInput(data []byte) (batchInput, error) {
	var input batchInput
	if len(data) > maxInputBytes {
		return input, errors.New("input exceeds 1 MiB")
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil {
		return input, fmt.Errorf("decode batch: %w", err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return input, errors.New("input must contain exactly one JSON object")
	}
	if input.Arm != "keyword" && input.Arm != "keyword_bm25" {
		return input, errors.New("arm must be keyword or keyword_bm25")
	}
	if input.Threshold == nil || math.IsNaN(*input.Threshold) || math.IsInf(*input.Threshold, 0) || *input.Threshold < 0 || *input.Threshold > 1 {
		return input, errors.New("threshold must be a finite number in [0, 1]")
	}
	if input.Examples == nil || !validText(input.Examples.Domain, 128) || !validText(input.Examples.Version, 128) {
		return input, errors.New("examples must include nonempty domain and version (at most 128 bytes each)")
	}
	if len(input.Examples.Examples) > 256 || (input.Arm == "keyword_bm25" && len(input.Examples.Examples) == 0) {
		return input, errors.New("at most 256 examples; keyword_bm25 requires at least one")
	}
	for i, ex := range input.Examples.Examples {
		if !validText(ex.Query, maxTextBytes) || !validText(ex.Intent, 128) {
			return input, fmt.Errorf("example %d requires nonempty query (at most 8192 bytes) and intent (at most 128 bytes)", i)
		}
		options, err := json.Marshal(ex.Options)
		if err != nil || len(options) > maxTextBytes {
			return input, fmt.Errorf("example %d options must serialize within 8192 bytes", i)
		}
	}
	if len(input.Queries) == 0 || len(input.Queries) > 1024 {
		return input, errors.New("queries must contain 1 to 1024 records")
	}
	seen := make(map[string]bool, len(input.Queries))
	for i, q := range input.Queries {
		if !validText(q.ID, 128) || !validText(q.Text, maxTextBytes) {
			return input, fmt.Errorf("query %d requires nonempty id (at most 128 bytes) and text (at most 8192 bytes)", i)
		}
		if seen[q.ID] {
			return input, fmt.Errorf("duplicate query id %q", q.ID)
		}
		seen[q.ID] = true
	}
	return input, nil
}

func validText(value string, limit int) bool {
	return strings.TrimSpace(value) != "" && len(value) <= limit
}

type classifier interface {
	ClassifyQuery(context.Context, string) *query.ClassificationResult
}

type classifierFactory func(batchInput) classifier

func actualClassifier(input batchInput) classifier {
	keyword := query.NewKeywordClassifier()
	var statistical *query.EmbeddingClassifier
	if input.Arm == "keyword_bm25" {
		statistical = query.NewEmbeddingClassifier([]*query.DomainExamples{input.Examples}, *input.Threshold)
	}
	return query.NewClassifierChain(keyword, statistical)
}

type outputRecord struct {
	Kind            string                      `json:"kind"`
	ID              string                      `json:"id"`
	Query           string                      `json:"query"`
	Arm             string                      `json:"arm"`
	InputSHA256     string                      `json:"input_sha256"`
	Status          string                      `json:"status"`
	Classification  *query.ClassificationResult `json:"classification"`
	Error           string                      `json:"error,omitempty"`
	StartedAt       time.Time                   `json:"started_at"`
	SetupDurationMS float64                     `json:"setup_duration_ms"`
	DurationMS      float64                     `json:"duration_ms"`
}

// runWithFactory isolates the dependency boundary for cancellation tests; normal execution
// always constructs the imported classifier once and visits queries in supplied order.
func runWithFactory(parent context.Context, args []string, factory classifierFactory) error {
	flags := flag.NewFlagSet("query-routing-driver", flag.ContinueOnError)
	inputPath := flags.String("input", "", "Regular file containing one JSON batch")
	outputPath := flags.String("output", "", "New JSONL result path; existing files are never overwritten")
	timeout := flags.Duration("timeout", 30*time.Second, "Batch deadline, greater than zero and at most 5m")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 || *inputPath == "" || *outputPath == "" || *timeout <= 0 || *timeout > 5*time.Minute {
		return errors.New("--input and --output required; --timeout must be in (0,5m]; no positional arguments")
	}
	ctx, cancel := context.WithTimeout(parent, *timeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return fmt.Errorf("batch canceled before input: %w", err)
	}
	// A FIFO must not block the open before we can reject non-regular input.
	// O_NONBLOCK has no effect on regular files on the supported Darwin/Linux hosts.
	file, err := os.OpenFile(*inputPath, os.O_RDONLY|syscall.O_NONBLOCK, 0)
	if err != nil {
		return fmt.Errorf("open input: %w", err)
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return fmt.Errorf("stat input: %w", err)
	}
	if !info.Mode().IsRegular() {
		return errors.New("input must be a regular file")
	}
	data, err := io.ReadAll(io.LimitReader(file, maxInputBytes+1))
	if err != nil {
		return fmt.Errorf("read input: %w", err)
	}
	input, err := decodeInput(data)
	if err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return fmt.Errorf("batch canceled after validation: %w", err)
	}
	output, err := os.OpenFile(*outputPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return fmt.Errorf("create new result: %w", err)
	}
	defer output.Close()
	totalBytes := 0
	persist := func(record any) error {
		encoded, err := json.Marshal(record)
		if err != nil {
			return fmt.Errorf("encode result: %w", err)
		}
		encoded = append(encoded, '\n')
		if totalBytes+len(encoded) > maxOutputBytes {
			return errors.New("output exceeds 32 MiB; prior records preserved")
		}
		if _, err := output.Write(encoded); err != nil {
			return fmt.Errorf("write result: %w", err)
		}
		totalBytes += len(encoded)
		if err := output.Sync(); err != nil {
			return fmt.Errorf("persist result: %w", err)
		}
		return nil
	}
	inputHash := sha256.Sum256(data)
	inputSHA := hex.EncodeToString(inputHash[:])
	build, _ := debug.ReadBuildInfo()
	if err := ctx.Err(); err != nil {
		return fmt.Errorf("batch canceled before classifier construction: %w", err)
	}
	setupStarted := time.Now()
	classify := factory(input)
	setupDurationMS := float64(time.Since(setupStarted)) / float64(time.Millisecond)
	if err := persist(map[string]any{
		"kind": "provenance", "semstreams_version": semstreamsVersion,
		"input_sha256": inputSHA, "arm": input.Arm, "threshold": *input.Threshold,
		"examples": len(input.Examples.Examples), "queries": len(input.Queries),
		"build": build, "started_at": setupStarted.UTC(), "timeout_ms": timeout.Milliseconds(),
		"setup_duration_ms": setupDurationMS,
		"timezone":          time.Local.String(),
		"protocol":          "Fresh classifier; sequential supplied query order; native ClassificationResult; no LLM, neural vector upgrade, InferStrategy or option normalization. setup_duration_ms times the constructor once per batch; duration_ms times one classification, excluding construction and result persistence. Process wall time must be measured by the caller. Time-dependent keyword options use upstream wall clock and timezone.",
	}); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return fmt.Errorf("batch canceled during classifier construction (timing preserved): %w", err)
	}
	for _, q := range input.Queries {
		if err := ctx.Err(); err != nil {
			return fmt.Errorf("batch interrupted before %s: %w", q.ID, err)
		}
		started := time.Now()
		result := classify.ClassifyQuery(ctx, q.Text)
		row := outputRecord{
			Kind: "classification", ID: q.ID, Query: q.Text, Arm: input.Arm,
			InputSHA256: inputSHA, Classification: result, Status: "ok",
			SetupDurationMS: setupDurationMS,
			StartedAt:       started.UTC(), DurationMS: float64(time.Since(started)) / float64(time.Millisecond),
		}
		if err := ctx.Err(); err != nil {
			row.Status, row.Error = "canceled", err.Error()
		} else if result == nil {
			row.Status, row.Error = "error", "classifier returned nil without context cancellation"
		}
		if err := persist(row); err != nil {
			return fmt.Errorf("result %s: %w", q.ID, err)
		}
		if err := ctx.Err(); err != nil {
			return fmt.Errorf("batch interrupted during %s (result preserved): %w", q.ID, err)
		}
		if row.Status != "ok" {
			return fmt.Errorf("classify %s: %s", q.ID, row.Error)
		}
	}
	return nil
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := runWithFactory(ctx, os.Args[1:], actualClassifier); err != nil {
		fmt.Fprintln(os.Stderr, "query routing driver:", err)
		os.Exit(1)
	}
}
