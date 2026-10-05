// Package guard bounds the native llama.cpp System One API. It does not score models.
package guard

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"sync/atomic"
	"time"
)

const MaxBody = 32 << 10
const maxResponse = 1 << 20

type Config struct {
	Upstream string
	Model    string
	Timeout  time.Duration
	Client   *http.Client
	Logger   *slog.Logger
}

type Guard struct {
	config     Config
	busy       chan struct{}
	requests   atomic.Uint64
	errors     atomic.Uint64
	durationNS atomic.Uint64
}

type Question struct {
	Type         string          `json:"type"`
	Instructions string          `json:"instructions"`
	Criteria     json.RawMessage `json:"criteria,omitempty"`
}

type Request struct {
	Model     string              `json:"model,omitempty"`
	State     string              `json:"state"`
	Questions map[string]Question `json:"questions"`
}

func New(c Config) (*Guard, error) {
	u, err := url.Parse(c.Upstream)
	if err != nil || u.Scheme != "http" || u.Host == "" || u.User != nil || (u.Path != "" && u.Path != "/") || u.RawQuery != "" || u.Fragment != "" {
		return nil, errors.New("upstream must be an http origin without credentials, path, query or fragment")
	}
	if c.Model == "" || c.Timeout <= 0 {
		return nil, errors.New("model and positive timeout are required")
	}
	if c.Client == nil {
		c.Client = &http.Client{}
	}
	// Never follow an upstream redirect out of the configured runtime.
	client := *c.Client
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	c.Client = &client
	if c.Logger == nil {
		c.Logger = slog.Default()
	}
	c.Upstream = strings.TrimRight(c.Upstream, "/")
	return &Guard{config: c, busy: make(chan struct{}, 1)}, nil
}

func decode(data []byte, dst any) error {
	d := json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	if err := d.Decode(dst); err != nil {
		return err
	}
	if err := d.Decode(new(any)); err != io.EOF {
		return errors.New("expected exactly one JSON value")
	}
	return nil
}

func validate(data []byte, model string) error {
	if err := checkJSON(data); err != nil {
		return err
	}
	var r Request
	if err := decode(data, &r); err != nil {
		return fmt.Errorf("invalid request: %w", err)
	}
	if r.Model != "" && r.Model != model {
		return fmt.Errorf("model must be %q", model)
	}
	if len(r.State) == 0 || len(r.State) > 8192 {
		return errors.New("state must be a string of 1..8192 UTF-8 bytes")
	}
	if len(r.Questions) < 1 || len(r.Questions) > 4 {
		return errors.New("questions must contain 1..4 entries")
	}
	for id, q := range r.Questions {
		if len(id) == 0 || len(id) > 64 {
			return errors.New("question ids must be 1..64 bytes")
		}
		if len(q.Instructions) == 0 || len(q.Instructions) > 1024 {
			return errors.New("instructions must be a string of 1..1024 bytes")
		}
		switch q.Type {
		case "choice":
			var options map[string]*string
			if json.Unmarshal(q.Criteria, &options) != nil || len(options) < 2 || len(options) > 16 {
				return errors.New("choice criteria must contain 2..16 named options with string or null descriptions")
			}
			for k, v := range options {
				if len(k) == 0 || len(k) > 128 || (v != nil && len(*v) > 512) {
					return errors.New("choice names must be 1..128 bytes; descriptions at most 512 bytes")
				}
			}
		case "score":
			var levels []*string
			if json.Unmarshal(q.Criteria, &levels) != nil || len(levels) < 2 || len(levels) > 10 {
				return errors.New("score criteria must be 2..10 ordered strings")
			}
			for _, v := range levels {
				if v == nil || len(*v) == 0 || len(*v) > 512 {
					return errors.New("score levels must be nonempty strings of at most 512 bytes")
				}
			}
		case "noul":
			if len(q.Criteria) > 0 {
				var criteria map[string]*string
				if json.Unmarshal(q.Criteria, &criteria) != nil || criteria == nil {
					return errors.New("noul criteria must be an object")
				}
				for k, v := range criteria {
					if (k != "true" && k != "false") || v == nil || len(*v) > 512 {
						return errors.New("noul criteria permits only true/false strings of at most 512 bytes")
					}
				}
			}
		default:
			return errors.New("type must be choice, score or noul")
		}
	}
	return nil
}

func replyError(w http.ResponseWriter, status int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"error": map[string]any{"code": status, "message": message}})
}

func (g *Guard) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet {
		switch r.URL.Path {
		case "/health":
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"status":"ok"}`)
			return
		case "/ready":
			g.forward(w, r, "/health", nil, 2*time.Second)
			return
		case "/v1/models":
			g.forward(w, r, "/v1/models", nil, 2*time.Second)
			return
		case "/metrics/runtime":
			g.forward(w, r, "/metrics", nil, 2*time.Second)
			return
		case "/metrics":
			w.Header().Set("Content-Type", "text/plain; version=0.0.4")
			fmt.Fprintf(w, "# TYPE semselect_requests_total counter\nsemselect_requests_total %d\n# TYPE semselect_errors_total counter\nsemselect_errors_total %d\n# TYPE semselect_request_duration_seconds summary\nsemselect_request_duration_seconds_sum %g\nsemselect_request_duration_seconds_count %d\n# TYPE semselect_inflight gauge\nsemselect_inflight %d\n", g.requests.Load(), g.errors.Load(), float64(g.durationNS.Load())/1e9, g.requests.Load(), len(g.busy))
			return
		}
	}
	if r.URL.Path != "/v1/systemone" {
		replyError(w, 404, "endpoint not available")
		return
	}
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", "POST")
		replyError(w, 405, "use POST")
		return
	}
	start := time.Now()
	g.requests.Add(1)
	status := http.StatusOK
	defer func() {
		elapsed := time.Since(start)
		g.durationNS.Add(uint64(elapsed))
		if status >= 400 {
			g.errors.Add(1)
		}
		g.config.Logger.Info("decision_request", "status", status, "duration_ms", elapsed.Milliseconds())
	}()
	if ct := r.Header.Get("Content-Type"); ct != "application/json" && !strings.HasPrefix(ct, "application/json;") {
		status = 415
		replyError(w, status, "use application/json")
		return
	}
	data, err := io.ReadAll(http.MaxBytesReader(w, r.Body, MaxBody))
	if err != nil {
		var limit *http.MaxBytesError
		status = 400
		if errors.As(err, &limit) {
			status = 413
		}
		replyError(w, status, "request body unreadable or exceeds 32768 bytes")
		return
	}
	if err = validate(data, g.config.Model); err != nil {
		status = 400
		replyError(w, status, err.Error())
		return
	}
	select {
	case g.busy <- struct{}{}:
		defer func() { <-g.busy }()
	default:
		status = 429
		w.Header().Set("Retry-After", "1")
		replyError(w, status, "runtime busy; retry with backoff")
		return
	}
	status = g.forward(w, r, "/v1/systemone", data, g.config.Timeout)
}

func (g *Guard) forward(w http.ResponseWriter, incoming *http.Request, path string, body []byte, timeout time.Duration) int {
	ctx, cancel := context.WithTimeout(incoming.Context(), timeout)
	defer cancel()
	r, err := http.NewRequestWithContext(ctx, incoming.Method, g.config.Upstream+path, bytes.NewReader(body))
	if err != nil {
		replyError(w, 502, "cannot construct runtime request")
		return 502
	}
	if body != nil {
		r.Header.Set("Content-Type", "application/json")
	}
	resp, err := g.config.Client.Do(r)
	if err != nil {
		code := 503
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			code = 504
		}
		replyError(w, code, "runtime unavailable or request deadline exceeded")
		return code
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, maxResponse+1))
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		replyError(w, 504, "runtime response deadline exceeded")
		return 504
	}
	if err != nil || len(data) > maxResponse {
		replyError(w, 502, "invalid or oversized runtime response")
		return 502
	}
	if resp.StatusCode >= 300 && resp.StatusCode < 400 {
		replyError(w, 502, "unexpected runtime redirect")
		return 502
	}
	w.Header().Set("Content-Type", resp.Header.Get("Content-Type"))
	w.WriteHeader(resp.StatusCode)
	_, _ = w.Write(data)
	return resp.StatusCode
}
