// Package main is an isolated evaluation adapter around the pinned SemStreams synthesizer.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http/httptrace"
	"sync"
	"time"

	"github.com/c360studio/semstreams/graph/llm"
	graphquery "github.com/c360studio/semstreams/processor/graph-query"
)

const semstreamsVersion = "v1.0.0-beta.160"

type inputRecord struct {
	ID            string                        `json:"id"`
	Query         string                        `json:"query"`
	Summaries     []graphquery.CommunitySummary `json:"summaries"`
	TotalEntities int                           `json:"total_entities"`
}

type message struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type traceEvent struct {
	Name        string  `json:"name"`
	ElapsedMS   float64 `json:"elapsed_ms"`
	Destination string  `json:"destination,omitempty"`
	Reused      bool    `json:"reused,omitempty"`
	Error       string  `json:"error,omitempty"`
}

type callRecord struct {
	// Request is the actual value supplied by the pinned synthesizer, not a rebuilt prompt.
	Request             llm.ChatRequest   `json:"llm_request"`
	Messages            []message         `json:"messages"`
	DeadlineRemainingMS float64           `json:"deadline_remaining_ms"`
	Response            *llm.ChatResponse `json:"llm_response,omitempty"`
	Error               string            `json:"error,omitempty"`
	DurationMS          float64           `json:"duration_ms"`
	WireTrace           []traceEvent      `json:"wire_trace"`
	RequestsWritten     int               `json:"requests_written"`
}

type outputRecord struct {
	ID            string                       `json:"id"`
	Mode          string                       `json:"mode"`
	Input         inputRecord                  `json:"input"`
	Calls         []callRecord                 `json:"calls"`
	Outcome       *graphquery.SynthesisOutcome `json:"synthesis_outcome,omitempty"`
	Error         string                       `json:"error,omitempty"`
	DurationMS    float64                      `json:"duration_ms"`
	StartedAt     string                       `json:"started_at"`
	SemstreamsPin string                       `json:"semstreams_version"`
}

// recordingClient wraps the real client without changing prompt, settings, deadline or retry behavior.
// A nil delegate is explicitly capture-only: the actual builder is exercised but no HTTP call occurs.
type recordingClient struct {
	delegate llm.Client
	model    string
	calls    []callRecord
}

func (r *recordingClient) Model() string { return r.model }
func (r *recordingClient) Close() error {
	if r.delegate != nil {
		return r.delegate.Close()
	}
	return nil
}

func (r *recordingClient) ChatCompletion(ctx context.Context, req llm.ChatRequest) (*llm.ChatResponse, error) {
	started := time.Now()
	call := callRecord{Request: req, Messages: []message{}, WireTrace: []traceEvent{}}
	if req.Temperature != nil {
		value := *req.Temperature
		call.Request.Temperature = &value
	}
	if req.SystemPrompt != "" {
		call.Messages = append(call.Messages, message{Role: "system", Content: req.SystemPrompt})
	}
	call.Messages = append(call.Messages, message{Role: "user", Content: req.UserPrompt})
	if deadline, ok := ctx.Deadline(); ok {
		call.DeadlineRemainingMS = float64(time.Until(deadline)) / float64(time.Millisecond)
	}
	if r.delegate == nil {
		// Only the intercepted request is meaningful in capture mode; execute omits this dummy outcome.
		call.DurationMS = float64(time.Since(started)) / float64(time.Millisecond)
		r.calls = append(r.calls, call)
		return &llm.ChatResponse{Model: r.model}, nil
	}
	var mu sync.Mutex
	events := []traceEvent{}
	appendEvent := func(event traceEvent) {
		event.ElapsedMS = float64(time.Since(started)) / float64(time.Millisecond)
		mu.Lock()
		events = append(events, event)
		mu.Unlock()
	}
	trace := &httptrace.ClientTrace{
		GetConn:      func(hostPort string) { appendEvent(traceEvent{Name: "get_connection", Destination: hostPort}) },
		GotConn:      func(info httptrace.GotConnInfo) { appendEvent(traceEvent{Name: "got_connection", Reused: info.Reused}) },
		WroteHeaders: func() { appendEvent(traceEvent{Name: "wrote_headers"}) },
		WroteRequest: func(info httptrace.WroteRequestInfo) {
			event := traceEvent{Name: "wrote_request"}
			if info.Err != nil {
				event.Error = info.Err.Error()
			}
			appendEvent(event)
		},
		GotFirstResponseByte: func() { appendEvent(traceEvent{Name: "first_response_byte"}) },
	}
	response, err := r.delegate.ChatCompletion(httptrace.WithClientTrace(ctx, trace), req)
	call.DurationMS = float64(time.Since(started)) / float64(time.Millisecond)
	if response != nil {
		copy := *response
		call.Response = &copy
	}
	if err != nil {
		call.Error = err.Error()
	}
	mu.Lock()
	call.WireTrace = append(call.WireTrace, events...)
	mu.Unlock()
	for _, event := range call.WireTrace {
		if event.Name == "wrote_request" {
			call.RequestsWritten++
		}
	}
	r.calls = append(r.calls, call)
	return response, err
}

func newRecordedClient(baseURL, modelName string, captureOnly bool, logger *slog.Logger) (*recordingClient, error) {
	client := &recordingClient{model: modelName, calls: []callRecord{}}
	if captureOnly {
		return client, nil
	}
	// Match graph-query's default initAnswerSynthesizer wiring: its resolved default
	// timeout is applied to both the HTTP client and synthesis context. Zero retries
	// selects the actual client's default of 3 retries (4 attempts total).
	delegate, err := llm.NewOpenAIClient(llm.OpenAIConfig{
		BaseURL: baseURL, Model: modelName, Logger: logger, Timeout: graphquery.DefaultAnswerSynthesisTimeout,
	})
	if err != nil {
		return nil, fmt.Errorf("create actual SemStreams OpenAI client: %w", err)
	}
	client.delegate = delegate
	return client, nil
}

func execute(ctx context.Context, input inputRecord, recorder *recordingClient, logger *slog.Logger) outputRecord {
	recorder.calls = []callRecord{}
	result := outputRecord{ID: input.ID, Mode: "inference", Input: input,
		SemstreamsPin: semstreamsVersion, StartedAt: time.Now().UTC().Format(time.RFC3339Nano)}
	started := time.Now()
	// This is the production exported implementation, including its prompt builder,
	// selected-cluster limit, representative formatting, timeout and template fallback.
	synthesizer := graphquery.NewLLMAnswerSynthesizer(recorder, recorder.Model(), logger, 0)
	outcome, err := synthesizer.Synthesize(ctx, input.Query, input.Summaries, input.TotalEntities)
	result.DurationMS = float64(time.Since(started)) / float64(time.Millisecond)
	result.Calls = recorder.calls
	if recorder.delegate == nil {
		result.Mode = "capture_only"
	} else {
		result.Outcome = &outcome
	}
	if err != nil {
		result.Error = err.Error()
	}
	return result
}

func decodeInput(raw []byte) (inputRecord, error) {
	var input inputRecord
	// Decode into the actual exported CommunitySummary type, rejecting accidental labels or metadata.
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil {
		return input, fmt.Errorf("decode input object: %w", err)
	}
	for _, key := range []string{"id", "query", "summaries", "total_entities"} {
		if _, ok := fields[key]; !ok {
			return input, fmt.Errorf("missing input field %q", key)
		}
	}
	if len(fields) != 4 {
		return input, fmt.Errorf("input requires exactly id, query, summaries and total_entities; gold labels are not input")
	}
	if err := json.Unmarshal(raw, &input); err != nil {
		return input, fmt.Errorf("decode actual CommunitySummary input: %w", err)
	}
	if input.ID == "" || input.Query == "" || input.TotalEntities < 0 {
		return input, fmt.Errorf("id/query must be nonempty and total_entities nonnegative")
	}
	if string(fields["summaries"]) == "null" {
		return input, fmt.Errorf("summaries must be an array, not null")
	}
	if string(fields["total_entities"]) == "null" {
		return input, fmt.Errorf("total_entities must be an integer, not null")
	}
	return input, nil
}
