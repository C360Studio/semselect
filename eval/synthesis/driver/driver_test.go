package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	graphquery "github.com/c360studio/semstreams/processor/graph-query"
)

func testLogger() *slog.Logger { return slog.New(slog.NewTextHandler(io.Discard, nil)) }

func fixtureInput(t *testing.T) inputRecord {
	t.Helper()
	raw, err := os.ReadFile("testdata/input.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	input, err := decodeInput(raw)
	if err != nil {
		t.Fatal(err)
	}
	return input
}

func golden(t *testing.T, name string) string {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	return strings.TrimSuffix(string(data), "\n")
}

func TestActualBuilderMatchesGoldenAndPreservesProductionSettings(t *testing.T) {
	recorder, err := newRecordedClient("", "capture-model", true, testLogger())
	if err != nil {
		t.Fatal(err)
	}
	result := execute(context.Background(), fixtureInput(t), recorder, testLogger())
	if result.Mode != "capture_only" || result.Outcome != nil || len(result.Calls) != 1 {
		t.Fatalf("capture must not masquerade as inference: %+v", result)
	}
	call := result.Calls[0]
	if call.Request.UserPrompt != golden(t, "user_prompt.txt") {
		t.Fatalf("user prompt differs:\n%s", call.Request.UserPrompt)
	}
	if call.Request.SystemPrompt != golden(t, "system_prompt.txt") {
		t.Fatal("system prompt differs")
	}
	if call.Request.MaxTokens != 500 || call.Request.Temperature == nil || *call.Request.Temperature != 0.3 {
		t.Fatalf("production settings changed: %+v", call.Request)
	}
	if call.DeadlineRemainingMS < 14000 || call.DeadlineRemainingMS > 15000 {
		t.Fatalf("deadline=%vms", call.DeadlineRemainingMS)
	}
	if call.RequestsWritten != 0 || len(call.WireTrace) != 0 || call.Response != nil {
		t.Fatal("capture mode performed or claimed HTTP")
	}
	if strings.Contains(call.Request.UserPrompt, "MUST-NOT-APPEAR") || strings.Contains(call.Request.UserPrompt, "keyword-must-not-appear") {
		t.Fatal("actual five-cluster/five-keyword limits lost")
	}
	if len(call.Messages) != 2 || call.Messages[0].Role != "system" || call.Messages[1].Content != call.Request.UserPrompt {
		t.Fatal("message view differs from captured request")
	}
}

const fakeResponse = `{"id":"test","object":"chat.completion","created":0,"model":"served-model","choices":[{"index":0,"message":{"role":"assistant","content":"Known facts; missing detail."},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}`

func TestActualOpenAIClientSerializesCapturedRequestAndRetriesFourAttempts(t *testing.T) {
	var mu sync.Mutex
	bodies := [][]byte{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		body, err := io.ReadAll(req.Body)
		if err != nil {
			t.Error(err)
		}
		_ = req.Body.Close()
		mu.Lock()
		bodies = append(bodies, body)
		n := len(bodies)
		mu.Unlock()
		if req.URL.Path != "/v1/chat/completions" {
			t.Errorf("path %s", req.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		if n <= 3 {
			w.WriteHeader(http.StatusServiceUnavailable)
			_, _ = io.WriteString(w, `{"error":{"message":"offline retry test","type":"test","code":"busy"}}`)
			return
		}
		_, _ = io.WriteString(w, fakeResponse)
	}))
	defer server.Close()
	recorder, err := newRecordedClient(server.URL+"/v1", "served-model", false, testLogger())
	if err != nil {
		t.Fatal(err)
	}
	defer recorder.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	result := execute(ctx, fixtureInput(t), recorder, testLogger())
	if result.Error != "" || result.Outcome == nil || result.Outcome.Degraded || result.Outcome.Answer != "Known facts; missing detail." {
		t.Fatalf("actual synthesis failed: %+v", result)
	}
	mu.Lock()
	captured := append([][]byte(nil), bodies...)
	mu.Unlock()
	if len(captured) != 4 || result.Calls[0].RequestsWritten != 4 {
		t.Fatalf("attempts=%d traces=%d", len(captured), result.Calls[0].RequestsWritten)
	}
	for _, body := range captured {
		var wire struct {
			Model       string    `json:"model"`
			Messages    []message `json:"messages"`
			MaxTokens   int       `json:"max_tokens"`
			Temperature float64   `json:"temperature"`
		}
		if err := json.Unmarshal(body, &wire); err != nil {
			t.Fatal(err)
		}
		if wire.Model != "served-model" || wire.MaxTokens != 500 || math.Abs(wire.Temperature-0.3) > 1e-6 {
			t.Fatalf("wire settings: %s", body)
		}
		if len(wire.Messages) != 2 || wire.Messages[0].Content != golden(t, "system_prompt.txt") || wire.Messages[1].Content != golden(t, "user_prompt.txt") {
			t.Fatalf("actual SDK wire prompt differs: %s", body)
		}
		var fields map[string]any
		_ = json.Unmarshal(body, &fields)
		for _, forbidden := range []string{"response_format", "chat_template_kwargs", "cache_prompt", "seed", "reasoning_effort"} {
			if _, exists := fields[forbidden]; exists {
				t.Fatalf("unexpected override %s", forbidden)
			}
		}
	}
	if result.Calls[0].Response == nil || result.Calls[0].Response.TotalTokens != 15 || result.Calls[0].Response.FinishReason != "stop" {
		t.Fatal("actual response evidence missing")
	}
}

func TestActualClientFailureIsPreservedAlongsideTemplateFallback(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[]}`)
	}))
	defer server.Close()
	recorder, err := newRecordedClient(server.URL+"/v1", "served-model", false, testLogger())
	if err != nil {
		t.Fatal(err)
	}
	defer recorder.Close()
	result := execute(context.Background(), fixtureInput(t), recorder, testLogger())
	if result.Outcome == nil || !result.Outcome.Degraded || result.Outcome.Reason != graphquery.ReasonAnswerSynthesisError || result.Outcome.Answer == "" {
		t.Fatalf("fallback lost: %+v", result)
	}
	if result.Calls[0].Error == "" || result.Calls[0].RequestsWritten != 1 {
		t.Fatalf("underlying failure lost: %+v", result.Calls)
	}
}

func TestCancelledContextPreservesActualCancelledFallback(t *testing.T) {
	recorder, err := newRecordedClient("http://127.0.0.1:1/v1", "served-model", false, testLogger())
	if err != nil {
		t.Fatal(err)
	}
	defer recorder.Close()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	result := execute(ctx, fixtureInput(t), recorder, testLogger())
	if result.Outcome == nil || !result.Outcome.Degraded || result.Outcome.Reason != graphquery.ReasonAnswerSynthesisCancelled {
		t.Fatalf("cancellation classification: %+v", result)
	}
	if result.Calls[0].Error == "" {
		t.Fatal("cancelled underlying client error lost")
	}
}

func TestNoSummariesMakesNoModelCall(t *testing.T) {
	recorder, _ := newRecordedClient("", "capture-model", true, testLogger())
	input := fixtureInput(t)
	input.Summaries = []graphquery.CommunitySummary{}
	result := execute(context.Background(), input, recorder, testLogger())
	if len(result.Calls) != 0 || result.Outcome != nil {
		t.Fatalf("empty summaries invented synthesis: %+v", result)
	}
}

func TestDecodeRejectsGoldMissingFieldsAndInvalidCounts(t *testing.T) {
	for _, raw := range []string{
		`{"id":"a","query":"q","summaries":[],"total_entities":0,"gold":"allow"}`,
		`{"id":"a","query":"q","summaries":[]}`,
		`{"id":"a","query":"q","summaries":null,"total_entities":0}`,
		`{"id":"a","query":"q","summaries":[],"total_entities":-1}`,
	} {
		if _, err := decodeInput([]byte(raw)); err == nil {
			t.Fatalf("accepted %s", raw)
		}
	}
}

func TestCaptureCLIProvenanceAndNoOverwrite(t *testing.T) {
	dir := t.TempDir()
	output := filepath.Join(dir, "capture.jsonl")
	args := []string{"--input", "testdata/input.jsonl", "--output", output, "--model", "capture-model", "--capture-only"}
	if err := run(context.Background(), args); err != nil {
		t.Fatal(err)
	}
	var result outputRecord
	data, err := os.ReadFile(output)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, &result); err != nil {
		t.Fatal(err)
	}
	if result.Mode != "capture_only" || result.Calls[0].Request.MaxTokens != 500 {
		t.Fatalf("wrong capture: %+v", result)
	}
	var provenance map[string]any
	meta, err := os.ReadFile(output + ".provenance.json")
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(meta, &provenance); err != nil {
		t.Fatal(err)
	}
	if provenance["capture_only"] != true || provenance["semstreams_version"] != semstreamsVersion || len(provenance["input_sha256"].(string)) != 64 {
		t.Fatal("provenance incomplete")
	}
	if err := run(context.Background(), args); err == nil {
		t.Fatal("existing evidence overwritten")
	}
	after, _ := os.ReadFile(output)
	if string(after) != string(data) {
		t.Fatal("result changed")
	}
}

func TestCLICancellationDuringFinalRowPreservesRowAndReturnsFailure(t *testing.T) {
	requestStarted := make(chan struct{})
	releaseHandler := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(_ http.ResponseWriter, req *http.Request) {
		_, _ = io.Copy(io.Discard, req.Body)
		close(requestStarted)
		select {
		case <-req.Context().Done():
		case <-releaseHandler:
		}
	}))
	defer server.Close()
	defer close(releaseHandler)
	output := filepath.Join(t.TempDir(), "cancelled.jsonl")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		done <- run(ctx, []string{"--input", "testdata/input.jsonl", "--output", output,
			"--model", "served-model", "--base-url", server.URL + "/v1"})
	}()
	select {
	case <-requestStarted:
		cancel()
	case <-time.After(2 * time.Second):
		t.Fatal("synthesis request did not start")
	}
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("cancellation during the final row must fail the batch, got %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("cancelled batch did not return")
	}
	data, err := os.ReadFile(output)
	if err != nil {
		t.Fatal(err)
	}
	var result outputRecord
	if err := json.Unmarshal(data, &result); err != nil {
		t.Fatalf("cancelled row was not preserved: %v", err)
	}
	if result.Outcome == nil || result.Outcome.Reason != graphquery.ReasonAnswerSynthesisCancelled {
		t.Fatalf("cancelled production fallback was not preserved: %+v", result)
	}
}
