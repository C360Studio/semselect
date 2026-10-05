package guard

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const valid = `{"state":"charge","questions":{"route":{"type":"choice","instructions":"Classify","criteria":{"z":"last","a":null}}}}`

func TestRejectAmbiguousJSON(t *testing.T) {
	for _, body := range []string{
		strings.Replace(valid, `"state":"charge"`, `"state":"`+strings.Repeat("x", 9000)+`","STATE":"x"`, 1),
		strings.Replace(valid, `"type":"choice"`, `"type":"score","TYPE":"choice"`, 1),
		strings.Replace(valid, `"z":"last"`, `"z":"last","z":"different"`, 1),
		`{"state":"x","questions":{"q":{"type":"noul","instructions":"yes?","criteria":{"true":null}}}}`,
	} {
		if err := validate([]byte(body), "test"); err == nil {
			t.Errorf("accepted ambiguous or invalid JSON: %.160s", body)
		}
	}
}

type roundTrip func(*http.Request) (*http.Response, error)

func (f roundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func response(code int, body string) *http.Response {
	return &http.Response{StatusCode: code, Header: http.Header{"Content-Type": []string{"application/json"}}, Body: io.NopCloser(strings.NewReader(body))}
}
func setup(t *testing.T, rt roundTrip, timeout time.Duration) *Guard {
	t.Helper()
	g, err := New(Config{Upstream: "http://runtime", Model: "test", Timeout: timeout, Client: &http.Client{Transport: rt}, Logger: slog.New(slog.NewTextHandler(io.Discard, nil))})
	if err != nil {
		t.Fatal(err)
	}
	return g
}
func call(g *Guard, method, path, body string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	g.ServeHTTP(w, r)
	return w
}

func TestBoundaryAndPassthrough(t *testing.T) {
	native := `{"answers":{"route":{"type":"choice","choice":"z","probabilities":{"z":0.63,"a":0.37},"confidence":0.26}}}`
	var calls atomic.Int32
	g := setup(t, func(r *http.Request) (*http.Response, error) {
		calls.Add(1)
		body, _ := io.ReadAll(r.Body)
		if string(body) != valid {
			t.Error("candidate order or raw request was changed")
		}
		return response(200, native), nil
	}, time.Second)
	w := call(g, "POST", "/v1/systemone", valid)
	if w.Code != 200 || w.Body.String() != native {
		t.Fatalf("passthrough: %d %s", w.Code, w.Body.String())
	}
	for _, tt := range []struct {
		body   string
		status int
	}{
		{strings.Repeat("x", MaxBody+1), 413},
		{valid + `{}`, 400},
		{strings.Replace(valid, `"charge"`, `"`+strings.Repeat("x", 8193)+`"`, 1), 400},
		{strings.Replace(valid, `"choice"`, `"invented"`, 1), 400},
		{strings.Replace(valid, `"state"`, `"model":"wrong","state"`, 1), 400},
		{strings.Replace(valid, `"z":"last","a":null`, `"z":"last"`, 1), 400},
		{strings.Replace(valid, `"state"`, `"images":[],"state"`, 1), 400},
	} {
		if w := call(g, "POST", "/v1/systemone", tt.body); w.Code != tt.status {
			t.Errorf("want%d got%d", tt.status, w.Code)
		}
	}
	if calls.Load() != 1 {
		t.Fatal("invalid request reached inference")
	}
	for _, body := range []string{`{"state":"x","questions":{"q":{"type":"score","instructions":"Rate","criteria":["low","high"]}}}`, `{"state":"x","questions":{"q":{"type":"noul","instructions":"True?","criteria":{"true":"yes","false":"no"}}}}`} {
		if err := validate([]byte(body), "test"); err != nil {
			t.Fatal(err)
		}
	}
}

func TestBusyAndCancellation(t *testing.T) {
	entered := make(chan struct{})
	canceled := make(chan struct{})
	g := setup(t, func(r *http.Request) (*http.Response, error) {
		close(entered)
		<-r.Context().Done()
		close(canceled)
		return nil, r.Context().Err()
	}, time.Minute)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	r := httptest.NewRequest("POST", "/v1/systemone", strings.NewReader(valid)).WithContext(ctx)
	r.Header.Set("Content-Type", "application/json")
	done := make(chan struct{})
	go func() { g.ServeHTTP(httptest.NewRecorder(), r); close(done) }()
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("upstream not entered")
	}
	if w := call(g, "POST", "/v1/systemone", valid); w.Code != 429 || w.Header().Get("Retry-After") != "1" {
		t.Fatal("busy not rejected")
	}
	cancel()
	select {
	case <-canceled:
	case <-time.After(2 * time.Second):
		t.Fatal("cancellation not propagated")
	}
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("handler did not finish")
	}
	if len(g.busy) != 0 {
		t.Fatal("slot leaked")
	}
}

func TestDeadlineAndReadiness(t *testing.T) {
	g := setup(t, func(r *http.Request) (*http.Response, error) { <-r.Context().Done(); return nil, r.Context().Err() }, 10*time.Millisecond)
	if w := call(g, "POST", "/v1/systemone", valid); w.Code != 504 {
		t.Fatalf("deadline: %d", w.Code)
	}
	g = setup(t, func(r *http.Request) (*http.Response, error) { return response(503, `{"error":"loading"}`), nil }, time.Second)
	if w := call(g, "GET", "/health", ""); w.Code != 200 {
		t.Fatal("liveness")
	}
	if w := call(g, "GET", "/ready", ""); w.Code != 503 {
		t.Fatal("readiness claimed success")
	}
	if w := call(g, "GET", "/completion", ""); w.Code != 404 {
		t.Fatal("unbounded native route exposed")
	}
}

func TestBackendErrorsAndBoundedResponse(t *testing.T) {
	for _, tt := range []struct {
		code int
		body string
		want int
	}{{400, `{"error":"context exceeded"}`, 400}, {302, "", 502}, {200, strings.Repeat("x", maxResponse+1), 502}} {
		t.Run(fmt.Sprint(tt.code, tt.want), func(t *testing.T) {
			g := setup(t, func(*http.Request) (*http.Response, error) { return response(tt.code, tt.body), nil }, time.Second)
			if w := call(g, "POST", "/v1/systemone", valid); w.Code != tt.want {
				t.Errorf("got%d want%d", w.Code, tt.want)
			}
		})
	}
}
