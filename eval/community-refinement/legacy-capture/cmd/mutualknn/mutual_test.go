package main

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"sort"
	"strings"
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

func TestDirectedNeighborsCapsAtKAfterThresholdFilter(t *testing.T) {
	// The below-threshold head would consume a k slot if the cap ran first.
	similar := []neighbor{{"z", 0.5}, {"a", 0.9}, {"b", 0.9}, {"c", 0.9}, {"d", 0.9}}
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

// fakeRequester serves scripted replies in call order, repeating the last.
// Like *nats.Conn it fails a request whose context is already done, and it
// checks every request against the legacy wire contract: the similar subject,
// a body of exactly {entity_id, limit == k}, and a per-request deadline.
type fakeRequester struct {
	t       *testing.T
	k       int
	replies []func(ctx context.Context) (*nats.Msg, error)

	mu    sync.Mutex
	calls int
	ids   []string
}

func newFake(t *testing.T, k int, replies ...func(context.Context) (*nats.Msg, error)) *fakeRequester {
	return &fakeRequester{t: t, k: k, replies: replies}
}

func (f *fakeRequester) RequestMsgWithContext(ctx context.Context, msg *nats.Msg) (*nats.Msg, error) {
	if msg.Subject != similarSubject {
		f.t.Errorf("subject = %q, want %q", msg.Subject, similarSubject)
	}
	var fields map[string]json.RawMessage
	err := json.Unmarshal(msg.Data, &fields)
	_, hasID := fields["entity_id"]
	_, hasLimit := fields["limit"]
	if err != nil || len(fields) != 2 || !hasID || !hasLimit {
		f.t.Errorf("request body %s: want exactly entity_id and limit (decode error: %v)", msg.Data, err)
	}
	var req similarRequest
	if err := json.Unmarshal(msg.Data, &req); err != nil || req.EntityID == "" || req.Limit != f.k {
		f.t.Errorf("request body %s: want a non-empty entity_id and limit %d (decode error: %v)", msg.Data, f.k, err)
	}
	if _, ok := ctx.Deadline(); !ok {
		f.t.Error("request context carries no per-request deadline")
	}
	f.mu.Lock()
	i := min(f.calls, len(f.replies)-1)
	f.calls++
	f.ids = append(f.ids, req.EntityID)
	f.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return f.replies[i](ctx)
}

func (f *fakeRequester) seen() (calls int, ids []string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.calls, append([]string(nil), f.ids...)
}

func reply(m *nats.Msg, err error) func(context.Context) (*nats.Msg, error) {
	return func(context.Context) (*nats.Msg, error) { return m, err }
}

func okReply(t *testing.T, id string, similar []neighbor) func(context.Context) (*nats.Msg, error) {
	t.Helper()
	data, err := json.Marshal(similarResponse{EntityID: id, Similar: similar, Duration: "1ms"})
	if err != nil {
		t.Fatal(err)
	}
	return reply(&nats.Msg{Data: data}, nil)
}

func errorReply(class, code, message string) func(context.Context) (*nats.Msg, error) {
	m := &nats.Msg{Header: nats.Header{}, Data: []byte(`{"message":"` + message + `"}`)}
	m.Header.Set(headerStatus, statusError)
	m.Header.Set(headerErrorClass, class)
	if code != "" {
		m.Header.Set(headerErrorCode, code)
	}
	return reply(m, nil)
}

func TestQuerySimilarRetriesTransientAtMostTwice(t *testing.T) {
	f := newFake(t, 8, reply(nil, nats.ErrTimeout))
	res := querySimilar(context.Background(), f, "x", 8, 0.75, time.Second, 0)
	calls, ids := f.seen()
	if res.Status != statusFailed || res.FailureKind != failureTransport || res.Attempts != 3 || calls != 3 {
		t.Fatalf("got status=%s kind=%s attempts=%d calls=%d, want failed/transport/3/3",
			res.Status, res.FailureKind, res.Attempts, calls)
	}
	if want := []string{"x", "x", "x"}; !reflect.DeepEqual(ids, want) {
		t.Fatalf("requested %v, want %v", ids, want)
	}
}

func TestQuerySimilarRecoversAfterTransientHandlerError(t *testing.T) {
	f := newFake(t, 8,
		errorReply(classTransient, "index_not_ready", "cold"),
		okReply(t, "x", []neighbor{{"y", 0.8}, {"z", 0.7}}),
	)
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
		reply      func(context.Context) (*nats.Msg, error)
		wantStatus string
		wantKind   string
	}{
		"parse error": {
			reply:      reply(&nats.Msg{Data: []byte("{not json")}, nil),
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
			f := newFake(t, 8, tc.reply)
			res := querySimilar(context.Background(), f, "x", 8, 0.75, time.Second, 0)
			if calls, _ := f.seen(); res.Status != tc.wantStatus || res.FailureKind != tc.wantKind || calls != 1 {
				t.Fatalf("got status=%s kind=%s calls=%d", res.Status, res.FailureKind, calls)
			}
		})
	}
}

func TestQuerySimilarRejectsMismatchedReply(t *testing.T) {
	f := newFake(t, 8, okReply(t, "other", nil))
	res := querySimilar(context.Background(), f, "x", 8, 0.75, time.Second, 0)
	if calls, _ := f.seen(); res.Status != statusFailed || res.FailureKind != failureIDMismatch || calls != 1 {
		t.Fatalf("got %+v", res)
	}
}

func TestSweepAbortsWhenFailureBudgetExhausted(t *testing.T) {
	f := newFake(t, 8, reply(&nats.Msg{Data: []byte("garbage")}, nil))
	ids := make([]string, 100)
	for i := range ids {
		ids[i] = string(rune('a'+i%26)) + string(rune('a'+i/26))
	}
	results, err := sweep(context.Background(), f, ids, len(ids), 8, 0.75, time.Second, 0, 1, nil)
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

func TestSweepFailureBudgetUsesEntityStatesDenominator(t *testing.T) {
	ids := func(n int) []string {
		out := make([]string, n)
		for i := range out {
			out[i] = string(rune('a' + i))
		}
		return out
	}
	garbage := reply(&nats.Msg{Data: []byte("garbage")}, nil)
	cases := []struct {
		name        string
		swept       int
		budgetTotal int
		wantAbort   bool
	}{
		// The legacy threshold is strict: 10 of 100 is not over 10%.
		{name: "at the fraction of a larger entity set", swept: 10, budgetTotal: 100, wantAbort: false},
		{name: "over the fraction of a larger entity set", swept: 11, budgetTotal: 100, wantAbort: true},
		{name: "empty denominator never aborts", swept: 3, budgetTotal: 0, wantAbort: false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			results, err := sweep(context.Background(), newFake(t, 8, garbage), ids(tc.swept), tc.budgetTotal,
				8, 0.75, time.Second, 0, 1, nil)
			if (err != nil) != tc.wantAbort {
				t.Fatalf("abort error = %v, want abort %v", err, tc.wantAbort)
			}
			for _, r := range results {
				if r.Status != statusFailed {
					t.Fatalf("%s: status %s, want every swept entity failed", r.EntityID, r.Status)
				}
			}
		})
	}
}

func TestSweepPreservesInputOrder(t *testing.T) {
	f := newFake(t, 8, errorReply("invalid", codeEmbeddingUnavailable, "none"))
	ids := []string{"c", "a", "b"}
	results, err := sweep(context.Background(), f, ids, len(ids), 8, 0.75, time.Second, 0, 3, nil)
	if err != nil {
		t.Fatal(err)
	}
	for i, r := range results {
		if r.EntityID != ids[i] || r.Status != statusMiss {
			t.Fatalf("results[%d] = %+v", i, r)
		}
	}
	_, requested := f.seen()
	sort.Strings(requested)
	if want := []string{"a", "b", "c"}; !reflect.DeepEqual(requested, want) {
		t.Fatalf("requested %v, want each id once: %v", requested, want)
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

func TestQuerySimilarCancelledInFlight(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	entered := make(chan struct{})
	f := newFake(t, 8, func(ctx context.Context) (*nats.Msg, error) {
		close(entered)
		<-ctx.Done()
		return nil, ctx.Err()
	})
	done := make(chan queryResult)
	go func() { done <- querySimilar(ctx, f, "x", 8, 0.75, time.Minute, 0) }()
	<-entered
	cancel()
	res := <-done
	if calls, _ := f.seen(); res.Status != statusFailed || res.FailureKind != failureCancelled ||
		res.Attempts != 1 || calls != 1 {
		t.Fatalf("got status=%s kind=%s attempts=%d calls=%d, want failed/cancelled/1/1",
			res.Status, res.FailureKind, res.Attempts, calls)
	}
}

func TestQuerySimilarPerRequestTimeoutIsTransient(t *testing.T) {
	// The fake blocks until the per-request deadline derived from the parent
	// fires; the parent stays live, so this is a retried transport timeout.
	f := newFake(t, 8, func(ctx context.Context) (*nats.Msg, error) {
		<-ctx.Done()
		return nil, ctx.Err()
	})
	res := querySimilar(context.Background(), f, "x", 8, 0.75, 10*time.Millisecond, 0)
	if calls, _ := f.seen(); res.FailureKind != failureTransport || res.Attempts != 3 || calls != 3 {
		t.Fatalf("got kind=%s attempts=%d calls=%d, want transport/3/3", res.FailureKind, res.Attempts, calls)
	}
}

func TestQuerySimilarCancelDuringBackoffIsCancelled(t *testing.T) {
	cases := map[string]func(context.Context) (*nats.Msg, error){
		"transient transport": reply(nil, nats.ErrNoResponders),
		"transient handler":   errorReply(classTransient, "index_not_ready", "cold"),
	}
	for name, first := range cases {
		t.Run(name, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			answered := make(chan struct{})
			f := newFake(t, 8, func(ctx context.Context) (*nats.Msg, error) {
				defer close(answered)
				return first(ctx)
			})
			done := make(chan queryResult)
			// An hour of backoff: only the cancel can end the wait.
			go func() { done <- querySimilar(ctx, f, "x", 8, 0.75, time.Second, time.Hour) }()
			<-answered
			cancel()
			res := <-done
			if calls, _ := f.seen(); res.Status != statusFailed || res.FailureKind != failureCancelled ||
				res.Attempts != 1 || calls != 1 {
				t.Fatalf("got status=%s kind=%s attempts=%d calls=%d, want failed/cancelled/1/1",
					res.Status, res.FailureKind, res.Attempts, calls)
			}
		})
	}
}

func TestLoadOutgoingRejectsRowWithEmptyTarget(t *testing.T) {
	// Legacy getNeighborsFromBucket rejects the whole row, not just the entry.
	kv := fakeKV{"a": []byte(`[{"to_entity_id":"b","predicate":"p"},{"to_entity_id":"","predicate":"p"}]`)}
	out, errsByID := loadOutgoing(context.Background(), kv, []string{"a"}, 1)
	if _, ok := out["a"]; ok {
		t.Fatalf("a row with an empty to_entity_id must not resolve, got %v", out["a"])
	}
	if !strings.Contains(errsByID["a"], "to_entity_id") {
		t.Fatalf("error for a = %q, want it to name to_entity_id", errsByID["a"])
	}
}

// staticLister is a jetstream.KeyLister over a prepared channel.
type staticLister chan string

func (l staticLister) Keys() <-chan string { return l }
func (l staticLister) Stop() error         { return nil }

// listKV is a jetstream.KeyValue whose ListKeys returns a prepared lister.
type listKV struct {
	jetstream.KeyValue
	lister staticLister
}

func (k listKV) ListKeys(context.Context, ...jetstream.WatchOpt) (jetstream.KeyLister, error) {
	return k.lister, nil
}
func (k listKV) Bucket() string { return "TEST" }

func TestListKeysTreatsListerClosedAfterExpiryAsError(t *testing.T) {
	// jetstream's lister closes its channel when its context ends, exactly as
	// it does on completion. A closed channel after expiry is a truncated
	// listing. Both select arms are ready here, so repeat to cover both orders.
	for range 64 {
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		lister := make(staticLister, 1)
		lister <- "k1"
		close(lister)
		keys, err := listKeys(ctx, listKV{lister: lister}, 10)
		if err == nil {
			t.Fatalf("listKeys returned %v with no error after its context ended", keys)
		}
	}
}

// fakeJS is a jetstream.JetStream whose KeyValue returns a prepared bucket.
type fakeJS struct {
	jetstream.JetStream
	kv jetstream.KeyValue
}

func (f fakeJS) KeyValue(context.Context, string) (jetstream.KeyValue, error) { return f.kv, nil }

func TestBucketKeysHonoursListTimeout(t *testing.T) {
	// A lister that never delivers and never closes: only the deadline ends it.
	js := fakeJS{kv: listKV{lister: make(staticLister)}}
	_, err := bucketKeys(context.Background(), js, bucketEntityStates, 10, 20*time.Millisecond)
	if !errors.Is(err, context.DeadlineExceeded) || !strings.Contains(err.Error(), "-list-timeout") {
		t.Fatalf("bucketKeys error = %v, want a -list-timeout deadline error", err)
	}
}

func TestExplicitPhaseErr(t *testing.T) {
	one := 1
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	cases := []struct {
		name    string
		ctx     context.Context
		review  *int
		reason  string
		wantErr string
	}{
		{name: "complete", ctx: context.Background(), review: &one},
		{name: "incomplete", ctx: context.Background(), reason: "open OUTGOING_INDEX: bucket not found",
			wantErr: "explicit-edge resolution incomplete: open OUTGOING_INDEX: bucket not found"},
		{name: "interrupted after completing", ctx: cancelled, review: &one,
			wantErr: "interrupted during explicit-edge resolution"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := summary{MutualPairsReviewCandidates: tc.review, MutualPairsExplicitDominatedReason: tc.reason}
			err := explicitPhaseErr(tc.ctx, s)
			if tc.wantErr == "" {
				if err != nil {
					t.Fatalf("err = %v, want nil", err)
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), tc.wantErr) {
				t.Fatalf("err = %v, want it to contain %q", err, tc.wantErr)
			}
		})
	}
	if err := explicitPhaseErr(cancelled, summary{}); !errors.Is(err, context.Canceled) {
		t.Fatalf("an interrupted phase must wrap the context error, got %v", err)
	}
}

func TestSummarizeDenominatorsAndLostPairs(t *testing.T) {
	id := func(typ, name string) string { return "c360.semsource.code.osh." + typ + "." + name }
	a, b, c, d := id("class", "a"), id("method", "b"), id("class", "c"), id("class", "d")
	e, f, g, orphan := id("class", "e"), id("class", "f"), id("class", "g"), id("class", "orphan")
	ok := func(self string, directed ...neighbor) queryResult {
		return queryResult{EntityID: self, Status: statusOK, Attempts: 1, Similar: directed, Directed: directed}
	}
	o := options{k: 8, threshold: 0.75, concurrency: 8, timeout: 30 * time.Second}
	entityIDs := []string{a, b, c, d, e, f, g}
	embeddedIDs := []string{a, b, c, d, e, f, orphan}
	results := []queryResult{ // the swept set: embedded and in ENTITY_STATES
		ok(a, neighbor{b, 0.9}, neighbor{c, 0.8}, neighbor{orphan, 0.78}),
		ok(b, neighbor{a, 0.85}),
		{EntityID: c, Status: statusFailed, Attempts: 3, FailureKind: failureTransport},
		ok(d, neighbor{c, 0.8}, neighbor{f, 0.77}),
		{EntityID: e, Status: statusMiss, Attempts: 1},
		{EntityID: f, Status: statusNotQueried},
	}
	s, pairs := summarize(o, entityIDs, embeddedIDs, results)

	type counts struct {
		Total, Embedded, Orphans, Swept                         int
		Queried, Answered, Failed, NotQueried, Miss, Retries    int
		DirectedAtThreshold, NeighborsNotInStates, ToUnanswered int
		Mutual, CrossType, Asymmetric, DegreeDenominator        int
		LowerBound                                              bool
	}
	got := counts{
		s.TotalEntities, s.EmbeddedEntities, s.EmbeddedNotInEntityStates, s.EmbeddedInEntityStates,
		s.Queried, s.Answered, s.Failed, s.NotQueried, s.EmbeddingUnavailable, s.Retries,
		s.DirectedPairsAtThreshold, s.NeighborIDsNotInEntityStates, s.DirectedEdgesToUnanswered,
		s.MutualPairs, s.MutualPairsCrossType, s.MutualPairsAsymmetricSimilarity, s.DegreeHistogramDenominatorEntities,
		s.MutualPairsIsLowerBound,
	}
	want := counts{
		Total: 7, Embedded: 7, Orphans: 1, Swept: 6,
		Queried: 5, Answered: 4, Failed: 1, NotQueried: 1, Miss: 1, Retries: 2,
		// a->c, d->c (failed) and d->f (not queried) point at unanswered entities.
		DirectedAtThreshold: 6, NeighborsNotInStates: 1, ToUnanswered: 3,
		Mutual: 1, CrossType: 1, Asymmetric: 1, DegreeDenominator: 4,
		LowerBound: true,
	}
	if got != want {
		t.Fatalf("summary counts\n got %+v\nwant %+v", got, want)
	}
	// Every swept entity lands in exactly one bucket of each partition.
	if s.Queried != s.Answered+s.Failed || s.Queried+s.NotQueried != s.EmbeddedInEntityStates ||
		s.EmbeddedInEntityStates+s.EmbeddedNotInEntityStates != s.EmbeddedEntities {
		t.Fatalf("denominators do not close: %+v", got)
	}
	if !reflect.DeepEqual(s.FailedByKind, map[string]int{failureTransport: 1}) {
		t.Fatalf("failed_by_kind = %v", s.FailedByKind)
	}
	if want := []unorderedPair{{a, b}}; len(pairs) != 1 || pairs[0].unorderedPair != want[0] ||
		pairs[0].simAB != 0.9 || pairs[0].simBA != 0.85 || pairs[0].rankAB != 1 || pairs[0].rankBA != 1 {
		t.Fatalf("pairs = %+v", pairs)
	}

	// The same graph with every entity answered is an exact count.
	results[2] = ok(c)
	results[5] = ok(f)
	exact, _ := summarize(o, entityIDs, embeddedIDs, results)
	if exact.MutualPairsIsLowerBound || exact.DirectedEdgesToUnanswered != 0 || exact.Queried != 6 {
		t.Fatalf("all answered: lower_bound=%v to_unanswered=%d queried=%d, want false/0/6",
			exact.MutualPairsIsLowerBound, exact.DirectedEdgesToUnanswered, exact.Queried)
	}
}
