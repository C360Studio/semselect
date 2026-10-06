package main

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/c360studio/semstreams/graph/query"
)

func fixture() batchInput {
	threshold := 0.2
	return batchInput{
		Arm: "keyword", Threshold: &threshold,
		Examples: &query.DomainExamples{Domain: "synthetic-development", Version: "1", Examples: []query.Example{
			{Query: "equipment roster", Intent: "path", Options: map[string]any{"path_intent": true, "path_start_node": "pump-17"}},
		}},
		Queries: []queryInput{{ID: "development-1", Text: "devices connected to sensor-007"}},
	}
}

func TestActualKeywordValuesAndFallback(t *testing.T) {
	chain := actualClassifier(fixture())
	got := chain.ClassifyQuery(context.Background(), "devices connected to sensor-007")
	want := &query.ClassificationResult{Tier: 0, Intent: "", Confidence: 1,
		Options: map[string]any{"path_intent": true, "path_start_node": "sensor-007"}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("native path result = %#v, want %#v", got, want)
	}
	got = chain.ClassifyQuery(context.Background(), "equipment roster")
	if got.Tier != 0 || got.Intent != "" || got.Confidence != 1 || len(got.Options) != 0 {
		t.Fatalf("native fallback was reinterpreted: %#v", got)
	}
}

func TestActualBM25CopiesOptionsAndKeywordTakesPrecedence(t *testing.T) {
	input := fixture()
	input.Arm = "keyword_bm25"
	chain := actualClassifier(input)
	got := chain.ClassifyQuery(context.Background(), "equipment roster pump-99")
	if got.Tier != 1 || got.Intent != "path" || got.Confidence < *input.Threshold || !reflect.DeepEqual(got.Options, input.Examples.Examples[0].Options) {
		t.Fatalf("expected BM25 to copy example options rather than extract pump-99: %#v", got)
	}
	got = chain.ClassifyQuery(context.Background(), "equipment roster connected to sensor-007")
	if got.Tier != 0 || got.Intent != "" || got.Confidence != 1 || got.Options["path_start_node"] != "sensor-007" {
		t.Fatalf("keyword did not short-circuit BM25: %#v", got)
	}
}

func TestDecodeRejectsMalformedAndUnknownFields(t *testing.T) {
	data, _ := json.Marshal(fixture())
	var original map[string]any
	if err := json.Unmarshal(data, &original); err != nil {
		t.Fatal(err)
	}
	cases := []struct{ name, data string }{
		{"invalid", "{"}, {"second object", string(data) + "{}"},
		{"null", "null"}, {"too large", strings.Repeat(" ", maxInputBytes+1)},
		{"unknown gold", strings.Replace(string(data), `"arm":`, `"gold":"path","arm":`, 1)},
		{"unknown query field", strings.Replace(string(data), `"text":`, `"expected":"path","text":`, 1)},
		{"unknown example field", strings.Replace(string(data), `"domain":`, `"training_gold":true,"domain":`, 1)},
		{"missing threshold", strings.Replace(string(data), `"threshold":0.2,`, "", 1)},
		{"nonfinite threshold", strings.Replace(string(data), `"threshold":0.2`, `"threshold":1e999`, 1)},
		{"negative threshold", strings.Replace(string(data), `"threshold":0.2`, `"threshold":-1`, 1)},
		{"high threshold", strings.Replace(string(data), `"threshold":0.2`, `"threshold":1.1`, 1)},
		{"bad arm", strings.Replace(string(data), `"arm":"keyword"`, `"arm":"llm"`, 1)},
		{"empty text", strings.Replace(string(data), `"devices connected to sensor-007"`, `" "`, 1)},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := decodeInput([]byte(tc.data)); err == nil {
				t.Fatal("invalid input accepted")
			}
		})
	}
	input := fixture()
	input.Queries = append(input.Queries, input.Queries[0])
	data, _ = json.Marshal(input)
	if _, err := decodeInput(data); err == nil || !strings.Contains(err.Error(), "duplicate") {
		t.Fatalf("duplicate IDs: %v", err)
	}
	input = fixture()
	input.Arm, input.Examples.Examples = "keyword_bm25", nil
	data, _ = json.Marshal(input)
	if _, err := decodeInput(data); err == nil {
		t.Fatal("empty BM25 examples accepted")
	}
}

func batchFiles(t *testing.T, input batchInput) ([]string, string, []byte) {
	t.Helper()
	dir := t.TempDir()
	inPath, outPath := filepath.Join(dir, "input.json"), filepath.Join(dir, "results.jsonl")
	data, err := json.Marshal(input)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(inPath, data, 0600); err != nil {
		t.Fatal(err)
	}
	return []string{"--input", inPath, "--output", outPath}, outPath, data
}

func readRows(t *testing.T, path string) []map[string]any {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var rows []map[string]any
	scanner := bufio.NewScanner(strings.NewReader(string(data)))
	for scanner.Scan() {
		var row map[string]any
		if err := json.Unmarshal(scanner.Bytes(), &row); err != nil {
			t.Fatal(err)
		}
		rows = append(rows, row)
	}
	if err := scanner.Err(); err != nil {
		t.Fatal(err)
	}
	return rows
}

func TestBatchPreservesOrderProvenanceAndNoOverwrite(t *testing.T) {
	input := fixture()
	input.Queries = append(input.Queries, queryInput{ID: "development-2", Text: "equipment roster"})
	args, out, data := batchFiles(t, input)
	if err := runWithFactory(context.Background(), args, actualClassifier); err != nil {
		t.Fatal(err)
	}
	rows := readRows(t, out)
	if len(rows) != 3 || rows[0]["kind"] != "provenance" || rows[0]["semstreams_version"] != semstreamsVersion {
		t.Fatalf("bad provenance/rows: %#v", rows)
	}
	setupMS, ok := rows[0]["setup_duration_ms"].(float64)
	if !ok || setupMS < 0 {
		t.Fatalf("missing constructor timing: %#v", rows[0]["setup_duration_ms"])
	}
	hash := sha256.Sum256(data)
	for i := range rows {
		if rows[i]["input_sha256"] != hex.EncodeToString(hash[:]) {
			t.Fatalf("row %d wrong input hash", i)
		}
	}
	for i, q := range input.Queries {
		if rows[i+1]["id"] != q.ID || rows[i+1]["query"] != q.Text || rows[i+1]["status"] != "ok" {
			t.Fatalf("query order/value changed: %#v", rows[i+1])
		}
		if rows[i+1]["setup_duration_ms"] != setupMS {
			t.Fatalf("query result does not reference the single constructor timing: %#v", rows[i+1])
		}
	}
	first, _ := os.ReadFile(out)
	if err := runWithFactory(context.Background(), args, actualClassifier); !errors.Is(err, os.ErrExist) {
		t.Fatalf("overwrite attempt: %v", err)
	}
	second, _ := os.ReadFile(out)
	if string(first) != string(second) {
		t.Fatal("overwrite modified evidence")
	}
}

type classifyFunc func(context.Context, string) *query.ClassificationResult

func (f classifyFunc) ClassifyQuery(ctx context.Context, text string) *query.ClassificationResult {
	return f(ctx, text)
}

func TestPreCanceledContextDoesNotCreateOutput(t *testing.T) {
	args, out, _ := batchFiles(t, fixture())
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := runWithFactory(ctx, args, func(batchInput) classifier {
		t.Fatal("classifier constructed after cancellation")
		return nil
	}); !errors.Is(err, context.Canceled) {
		t.Fatalf("pre-cancel error: %v", err)
	}
	if _, err := os.Stat(out); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("unexpected output: %v", err)
	}
}

func TestCancellationDuringFinalRowPreservesResultAndFails(t *testing.T) {
	args, out, _ := batchFiles(t, fixture())
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	entered, release := make(chan struct{}), make(chan struct{})
	done := make(chan error, 1)
	go func() {
		done <- runWithFactory(ctx, args, func(batchInput) classifier {
			return classifyFunc(func(context.Context, string) *query.ClassificationResult {
				close(entered)
				<-release
				return &query.ClassificationResult{Tier: 0, Options: map[string]any{"path_intent": true}, Confidence: 1}
			})
		})
	}()
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		close(release)
		t.Fatal("classifier never entered")
	}
	cancel()
	close(release)
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("final-row cancellation reported success: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("driver did not finish")
	}
	rows := readRows(t, out)
	if len(rows) != 2 || rows[1]["status"] != "canceled" || rows[1]["classification"] == nil {
		t.Fatalf("partial final result missing: %#v", rows)
	}
}

func TestCancellationDuringSetupPreservesTimingAndFails(t *testing.T) {
	args, out, _ := batchFiles(t, fixture())
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	entered, release := make(chan struct{}), make(chan struct{})
	done := make(chan error, 1)
	queryCalled := false
	go func() {
		done <- runWithFactory(ctx, args, func(batchInput) classifier {
			close(entered)
			<-release
			return classifyFunc(func(context.Context, string) *query.ClassificationResult {
				queryCalled = true
				return nil
			})
		})
	}()
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		close(release)
		t.Fatal("constructor never entered")
	}
	cancel()
	close(release)
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("constructor cancellation reported success: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("driver did not finish")
	}
	if queryCalled {
		t.Fatal("query classified after cancellation during setup")
	}
	rows := readRows(t, out)
	if len(rows) != 1 || rows[0]["kind"] != "provenance" {
		t.Fatalf("setup evidence missing or unexpected query rows: %#v", rows)
	}
	if setupMS, ok := rows[0]["setup_duration_ms"].(float64); !ok || setupMS < 0 {
		t.Fatalf("constructor timing not preserved: %#v", rows[0]["setup_duration_ms"])
	}
}

func TestDeadlineAtBoundary(t *testing.T) {
	args, out, _ := batchFiles(t, fixture())
	ctx, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer cancel()
	if err := runWithFactory(ctx, args, actualClassifier); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("deadline ignored: %v", err)
	}
	if _, err := os.Stat(out); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("unexpected output: %v", err)
	}
}

func TestInvalidBatchDoesNotConstructClassifierOrCreateOutput(t *testing.T) {
	input := fixture()
	input.Queries[0].Text = " "
	args, out, _ := batchFiles(t, input)
	if err := runWithFactory(context.Background(), args, func(batchInput) classifier {
		t.Fatal("classifier constructed for invalid batch")
		return nil
	}); err == nil {
		t.Fatal("invalid batch accepted")
	}
	if _, err := os.Stat(out); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("unexpected output: %v", err)
	}
}

func TestNonRegularInputDoesNotBlockOnFIFO(t *testing.T) {
	dir := t.TempDir()
	fifo := filepath.Join(dir, "input.fifo")
	if err := syscall.Mkfifo(fifo, 0600); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() {
		done <- runWithFactory(context.Background(), []string{"--input", fifo, "--output", filepath.Join(dir, "result.jsonl"), "--timeout", "10ms"}, actualClassifier)
	}()
	select {
	case err := <-done:
		if err == nil || !strings.Contains(err.Error(), "regular file") {
			t.Fatalf("FIFO validation: %v", err)
		}
	case <-time.After(time.Second):
		// Release an erroneous blocking read-open, so the regression never leaves
		// a stuck goroutine behind. O_RDWR avoids waiting if it has not opened yet.
		unblock, err := os.OpenFile(fifo, os.O_RDWR|syscall.O_NONBLOCK, 0600)
		if err != nil {
			t.Fatal(err)
		}
		defer unblock.Close()
		select {
		case <-done:
		case <-time.After(time.Second):
			t.Fatal("driver stayed blocked after FIFO release")
		}
		t.Fatal("FIFO read-open blocked beyond batch timeout")
	}
}
