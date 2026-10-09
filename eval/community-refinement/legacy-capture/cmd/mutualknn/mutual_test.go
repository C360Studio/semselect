package main

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"sync"
	"testing"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

func TestDirectedNeighborsMatchesLegacyFiltering(t *testing.T) {
	similar := []neighbor{
		{EntityID: "b", Similarity: 0.90},
		{EntityID: "self", Similarity: 0.99}, // self-edge dropped
		{EntityID: "c", Similarity: 0.75},    // threshold is inclusive
		{EntityID: "b", Similarity: 0.80},    // duplicate collapses (set semantics)
		{EntityID: "d", Similarity: 0.7499},  // below threshold
		{EntityID: "e", Similarity: 0.76},
	}
	got := directedNeighbors("self", similar, 0.75, 8)
	want := []neighbor{{"b", 0.90}, {"c", 0.75}, {"e", 0.76}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("directedNeighbors = %v, want %v (reply order preserved)", got, want)
	}
}

func TestDirectedNeighborsCapsAtK(t *testing.T) {
	similar := []neighbor{{"a", 0.9}, {"b", 0.9}, {"c", 0.9}, {"d", 0.9}}
	got := directedNeighbors("x", similar, 0.75, 2)
	if want := []neighbor{{"a", 0.9}, {"b", 0.9}}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestComputeMutualKeepsOnlyReciprocatedEdges(t *testing.T) {
	directed := map[string]map[string]bool{
		"a": {"b": true, "c": true},
		"b": {"a": true},
		"c": {"d": true}, // c does not point back at a
		"d": {"c": true},
		"e": {},
	}
	mutual := computeMutual(directed)
	want := map[string]map[string]bool{
		"a": {"b": true},
		"b": {"a": true},
		"c": {"d": true},
		"d": {"c": true},
	}
	if !reflect.DeepEqual(mutual, want) {
		t.Fatalf("computeMutual = %v, want %v", mutual, want)
	}
	pairs := mutualPairs(mutual)
	if wantPairs := []unorderedPair{{"a", "b"}, {"c", "d"}}; !reflect.DeepEqual(pairs, wantPairs) {
		t.Fatalf("mutualPairs = %v, want %v (each pair once, sorted)", pairs, wantPairs)
	}
}

func TestParseEntityID(t *testing.T) {
	p := parseEntityID("c360.semsource.code.sensorhub-core.java-class.org.example.Foo")
	if !p.Valid || p.System != "sensorhub-core" || p.Type != "java-class" {
		t.Fatalf("parseEntityID = %+v", p)
	}
	for _, bad := range []string{"a.b.c.d.e", "a.b..d.e.f", ""} {
		if parseEntityID(bad).Valid {
			t.Errorf("parseEntityID(%q) reported valid", bad)
		}
	}
}

func TestSimilarityHistogramBoundaries(t *testing.T) {
	bins := similarityHistogram([]float64{0.75, 0.7999, 0.80, 0.85, 0.95, 1.0, 0.5}, 0.75)
	gotRanges := make([]string, len(bins))
	gotCounts := make([]int, len(bins))
	for i, b := range bins {
		gotRanges[i], gotCounts[i] = b.Range, b.Count
	}
	wantRanges := []string{"[0.75,0.80)", "[0.80,0.85)", "[0.85,0.90)", "[0.90,0.95)", "[0.95,1.00]"}
	if !reflect.DeepEqual(gotRanges, wantRanges) {
		t.Fatalf("ranges = %v, want %v", gotRanges, wantRanges)
	}
	if want := []int{2, 1, 1, 0, 2}; !reflect.DeepEqual(gotCounts, want) {
		t.Fatalf("counts = %v, want %v (0.5 is below range and skipped)", gotCounts, want)
	}
}

func TestSummarizeLatencyNearestRank(t *testing.T) {
	var ds []time.Duration
	for i := 1; i <= 100; i++ {
		ds = append(ds, time.Duration(i)*time.Millisecond)
	}
	got := summarizeLatency(ds)
	if got.Count != 100 || got.P50MS != 50 || got.P95MS != 95 || got.MaxMS != 100 {
		t.Fatalf("summarizeLatency = %+v", got)
	}
}

func TestExplicitEdgeEitherDirection(t *testing.T) {
	out := map[string]map[string]bool{"a": {"b": true}, "b": {}, "c": {}}
	if d, ok := explicitEdge(out, "a", "b"); !ok || !d {
		t.Fatal("a->b should dominate")
	}
	if d, ok := explicitEdge(out, "b", "a"); !ok || !d {
		t.Fatal("explicit edge must dominate in either direction")
	}
	if d, ok := explicitEdge(out, "b", "c"); !ok || d {
		t.Fatal("b-c has no explicit edge")
	}
	if _, ok := explicitEdge(out, "a", "z"); ok {
		t.Fatal("unknown endpoint must be unresolved")
	}
}

// fakeRequester returns scripted replies in order and counts calls.
type fakeRequester struct {
	mu      sync.Mutex
	replies []func() (*nats.Msg, error)
	calls   int
}

func (f *fakeRequester) RequestMsgWithContext(_ context.Context, _ *nats.Msg) (*nats.Msg, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	i := f.calls
	f.calls++
	if i >= len(f.replies) {
		i = len(f.replies) - 1
	}
	return f.replies[i]()
}

func okReply(t *testing.T, id string, similar []neighbor) func() (*nats.Msg, error) {
	t.Helper()
	data, err := json.Marshal(similarResponse{EntityID: id, Similar: similar, Duration: "1ms"})
	if err != nil {
		t.Fatal(err)
	}
	return func() (*nats.Msg, error) { return &nats.Msg{Data: data}, nil }
}

func errorReply(class, code, message string) func() (*nats.Msg, error) {
	return func() (*nats.Msg, error) {
		m := &nats.Msg{Header: nats.Header{}, Data: []byte(`{"message":"` + message + `"}`)}
		m.Header.Set(headerStatus, statusError)
		m.Header.Set(headerErrorClass, class)
		if code != "" {
			m.Header.Set(headerErrorCode, code)
		}
		return m, nil
	}
}

func TestQuerySimilarRetriesTransientAtMostTwice(t *testing.T) {
	f := &fakeRequester{replies: []func() (*nats.Msg, error){
		func() (*nats.Msg, error) { return nil, nats.ErrTimeout },
	}}
	res := querySimilar(context.Background(), f, "x", 8, 0.75, time.Second, 0)
	if res.Status != statusFailed || res.FailureKind != failureTransport || res.Attempts != 3 || f.calls != 3 {
		t.Fatalf("got status=%s kind=%s attempts=%d calls=%d, want failed/transport/3/3",
			res.Status, res.FailureKind, res.Attempts, f.calls)
	}
}

func TestQuerySimilarRecoversAfterTransientHandlerError(t *testing.T) {
	f := &fakeRequester{replies: []func() (*nats.Msg, error){
		errorReply(classTransient, "index_not_ready", "cold"),
		okReply(t, "x", []neighbor{{"y", 0.8}, {"z", 0.7}}),
	}}
	res := querySimilar(context.Background(), f, "x", 8, 0.75, time.Second, 0)
	if res.Status != statusOK || res.Attempts != 2 || res.Error != "" || res.ErrorCode != "" {
		t.Fatalf("got %+v", res)
	}
	if want := []neighbor{{"y", 0.8}}; !reflect.DeepEqual(res.Directed, want) {
		t.Fatalf("directed = %v, want %v", res.Directed, want)
	}
	if len(res.Similar) != 2 {
		t.Fatalf("raw similar list must be preserved, got %v", res.Similar)
	}
}

func TestQuerySimilarNeverRetriesParseOrMiss(t *testing.T) {
	cases := map[string]struct {
		reply      func() (*nats.Msg, error)
		wantStatus string
		wantKind   string
	}{
		"parse error": {
			reply:      func() (*nats.Msg, error) { return &nats.Msg{Data: []byte("{not json")}, nil },
			wantStatus: statusFailed, wantKind: failureParse,
		},
		"embedding unavailable": {
			reply:      errorReply("invalid", codeEmbeddingUnavailable, "no vector"),
			wantStatus: statusMiss,
		},
		"invalid handler error": {
			reply:      errorReply("invalid", "", "bad request"),
			wantStatus: statusFailed, wantKind: failureHandler,
		},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			f := &fakeRequester{replies: []func() (*nats.Msg, error){tc.reply}}
			res := querySimilar(context.Background(), f, "x", 8, 0.75, time.Second, 0)
			if res.Status != tc.wantStatus || res.FailureKind != tc.wantKind || f.calls != 1 {
				t.Fatalf("got status=%s kind=%s calls=%d", res.Status, res.FailureKind, f.calls)
			}
		})
	}
}

func TestQuerySimilarRejectsMismatchedReply(t *testing.T) {
	f := &fakeRequester{replies: []func() (*nats.Msg, error){okReply(t, "other", nil)}}
	res := querySimilar(context.Background(), f, "x", 8, 0.75, time.Second, 0)
	if res.Status != statusFailed || res.FailureKind != failureIDMismatch || f.calls != 1 {
		t.Fatalf("got %+v", res)
	}
}

func TestSweepAbortsWhenFailureBudgetExhausted(t *testing.T) {
	f := &fakeRequester{replies: []func() (*nats.Msg, error){
		func() (*nats.Msg, error) { return &nats.Msg{Data: []byte("garbage")}, nil },
	}}
	ids := make([]string, 100)
	for i := range ids {
		ids[i] = string(rune('a'+i%26)) + string(rune('a'+i/26))
	}
	results, err := sweep(context.Background(), f, ids, 8, 0.75, time.Second, 0, 1, nil)
	if err == nil {
		t.Fatal("expected the failure budget to abort the sweep")
	}
	failed, notQueried := 0, 0
	for _, r := range results {
		switch r.Status {
		case statusFailed:
			failed++
		case statusNotQueried:
			notQueried++
		}
	}
	// Budget is 10% of 100; the 11th failure aborts and nothing after it is sent.
	if failed != 11 || failed+notQueried != len(ids) {
		t.Fatalf("failed=%d notQueried=%d", failed, notQueried)
	}
}

func TestSweepPreservesInputOrder(t *testing.T) {
	f := &fakeRequester{replies: []func() (*nats.Msg, error){
		errorReply("invalid", codeEmbeddingUnavailable, "none"),
	}}
	ids := []string{"c", "a", "b"}
	results, err := sweep(context.Background(), f, ids, 8, 0.75, time.Second, 0, 3, nil)
	if err != nil {
		t.Fatal(err)
	}
	for i, r := range results {
		if r.EntityID != ids[i] || r.Status != statusMiss {
			t.Fatalf("results[%d] = %+v", i, r)
		}
	}
}

type fakeEntry struct {
	jetstream.KeyValueEntry
	value []byte
}

func (e fakeEntry) Value() []byte { return e.value }

type fakeKV map[string][]byte

func (f fakeKV) Get(_ context.Context, key string) (jetstream.KeyValueEntry, error) {
	v, ok := f[key]
	if !ok {
		return nil, jetstream.ErrKeyNotFound
	}
	return fakeEntry{value: v}, nil
}

func TestLoadOutgoing(t *testing.T) {
	kv := fakeKV{
		"a":   []byte(`[{"to_entity_id":"b","predicate":"code.relation.calls"}]`),
		"bad": []byte(`{"not":"an array"}`),
	}
	out, errsByID := loadOutgoing(context.Background(), kv, []string{"a", "missing", "bad"}, 2)
	if !out["a"]["b"] {
		t.Fatalf("a should point at b: %v", out)
	}
	if set, ok := out["missing"]; !ok || len(set) != 0 {
		t.Fatalf("a missing key is an entity with no outgoing edges, got %v ok=%v", set, ok)
	}
	if _, ok := errsByID["bad"]; !ok {
		t.Fatal("undecodable row must be reported, not treated as no edges")
	}
	if _, ok := out["bad"]; ok {
		t.Fatal("undecodable row must not resolve")
	}
}

func TestIsTransientTransport(t *testing.T) {
	if !isTransientTransport(nats.ErrNoResponders) || !isTransientTransport(context.DeadlineExceeded) {
		t.Fatal("timeouts and no-responders are transient")
	}
	if isTransientTransport(errors.New("boom")) || isTransientTransport(nats.ErrConnectionClosed) {
		t.Fatal("other errors are not transient")
	}
}
