package main

// Offline end-to-end tests against an embedded NATS server with JetStream. The
// legacy KV buckets are seeded directly and a test responder serves
// graph.embedding.query.similar, so run() executes as it would against a live
// legacy stack, without Docker or a model.

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/nats-io/nats-server/v2/server"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

var quiet = slog.New(slog.NewTextHandler(io.Discard, nil))

type testStack struct {
	url string
	nc  *nats.Conn
	js  jetstream.JetStream
}

func startJetStream(t *testing.T) testStack {
	t.Helper()
	s, err := server.NewServer(&server.Options{
		Host: "127.0.0.1", Port: -1, JetStream: true, StoreDir: t.TempDir(), NoLog: true, NoSigs: true,
	})
	if err != nil {
		t.Fatal(err)
	}
	s.Start()
	t.Cleanup(func() {
		s.Shutdown()
		s.WaitForShutdown()
	})
	if !s.ReadyForConnections(10 * time.Second) {
		t.Fatal("embedded nats-server not ready")
	}
	nc, err := nats.Connect(s.ClientURL())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(nc.Close)
	js, err := jetstream.New(nc)
	if err != nil {
		t.Fatal(err)
	}
	return testStack{url: s.ClientURL(), nc: nc, js: js}
}

// seedBucket creates bucket and writes rows into it.
func seedBucket(t *testing.T, js jetstream.JetStream, bucket string, rows map[string]string) {
	t.Helper()
	kv, err := js.CreateKeyValue(t.Context(), jetstream.KeyValueConfig{Bucket: bucket})
	if err != nil {
		t.Fatal(err)
	}
	for k, v := range rows {
		if _, err := kv.Put(t.Context(), k, []byte(v)); err != nil {
			t.Fatal(err)
		}
	}
}

func entityRows(ids ...string) map[string]string {
	rows := make(map[string]string, len(ids))
	for _, id := range ids {
		rows[id] = "{}"
	}
	return rows
}

func eid(typ, name string) string { return "c360.semsource.code.osh." + typ + "." + name }

// similarResponder answers graph.embedding.query.similar from a fixed table and
// counts the requests it sees per entity.
type similarResponder struct {
	t       *testing.T
	k       int
	similar map[string][]neighbor
	fail    map[string]bool // non-transient handler error
	miss    map[string]bool // embedding_unavailable

	mu       sync.Mutex
	requests map[string]int
}

func serveSimilar(t *testing.T, nc *nats.Conn, r *similarResponder) {
	t.Helper()
	r.t, r.requests = t, map[string]int{}
	if _, err := nc.Subscribe(similarSubject, r.handle); err != nil {
		t.Fatal(err)
	}
	if err := nc.Flush(); err != nil {
		t.Fatal(err)
	}
}

func (r *similarResponder) handle(m *nats.Msg) {
	var req similarRequest
	if err := json.Unmarshal(m.Data, &req); err != nil || req.Limit != r.k {
		r.t.Errorf("similar request %s: want limit %d (decode error: %v)", m.Data, r.k, err)
	}
	r.mu.Lock()
	r.requests[req.EntityID]++
	r.mu.Unlock()
	var err error
	switch {
	case r.fail[req.EntityID]:
		err = m.RespondMsg(handlerError("invalid", "boom", "handler failed"))
	case r.miss[req.EntityID]:
		err = m.RespondMsg(handlerError("invalid", codeEmbeddingUnavailable, "no vector"))
	default:
		var data []byte
		if data, err = json.Marshal(similarResponse{
			EntityID: req.EntityID, Similar: r.similar[req.EntityID], Duration: "1ms",
		}); err == nil {
			err = m.Respond(data)
		}
	}
	if err != nil {
		r.t.Errorf("respond to %s: %v", req.EntityID, err)
	}
}

func (r *similarResponder) seen() map[string]int {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := make(map[string]int, len(r.requests))
	for k, v := range r.requests {
		out[k] = v
	}
	return out
}

func handlerError(class, code, message string) *nats.Msg {
	m := &nats.Msg{Header: nats.Header{}, Data: []byte(`{"message":"` + message + `"}`)}
	m.Header.Set(headerStatus, statusError)
	m.Header.Set(headerErrorClass, class)
	m.Header.Set(headerErrorCode, code)
	return m
}

func runArgs(t *testing.T, url, out string, extra ...string) []string {
	t.Helper()
	cfg := filepath.Join(t.TempDir(), "mvp.json")
	if err := os.WriteFile(cfg, []byte("{}"), 0o644); err != nil {
		t.Fatal(err)
	}
	return append([]string{
		"-nats", url, "-output", out, "-concurrency", "4",
		"-corpus-repo", "r", "-corpus-commit", "c", "-semsource-commit", "s",
		"-semstreams-version", "v", "-semembed-image", "i", "-semembed-model", "m", "-config-path", cfg,
	}, extra...)
}

func readSummary(t *testing.T, out string) summary {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(out, "summary.json"))
	if err != nil {
		t.Fatal(err)
	}
	var s summary
	if err := json.Unmarshal(data, &s); err != nil {
		t.Fatal(err)
	}
	return s
}

func readPairs(t *testing.T, out string) []mutualPairLine {
	t.Helper()
	f, err := os.Open(filepath.Join(out, "mutual_pairs.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var lines []mutualPairLine
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		var l mutualPairLine
		if err := json.Unmarshal(sc.Bytes(), &l); err != nil {
			t.Fatal(err)
		}
		lines = append(lines, l)
	}
	if err := sc.Err(); err != nil {
		t.Fatal(err)
	}
	return lines
}

func assertAbsent(t *testing.T, p string) {
	t.Helper()
	if _, err := os.Lstat(p); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("%s should not exist after an early failure (stat error: %v)", p, err)
	}
}

// tenMutualPlusSingles is ten entities where only e0 and e1 are mutual.
func tenMutualPlusSingles() ([]string, map[string][]neighbor) {
	ids := make([]string, 10)
	for i := range ids {
		ids[i] = eid("class", "e"+string(rune('0'+i)))
	}
	return ids, map[string][]neighbor{ids[0]: {{ids[1], 0.9}}, ids[1]: {{ids[0], 0.9}}}
}

func TestResolveExplicit(t *testing.T) {
	a, b, c, d, e, f, x := eid("class", "a"), eid("class", "b"), eid("class", "c"), eid("class", "d"),
		eid("class", "e"), eid("class", "f"), eid("class", "x")
	pairs := []pairInfo{
		{unorderedPair: unorderedPair{A: a, B: b}},
		{unorderedPair: unorderedPair{A: c, B: d}},
		{unorderedPair: unorderedPair{A: e, B: f}},
	}
	row := func(to string) string { return `[{"to_entity_id":"` + to + `","predicate":"code.relation.calls"}]` }
	dominated := func(lines []mutualPairLine) []any {
		out := make([]any, len(lines))
		for i, l := range lines {
			if l.ExplicitDominated != nil {
				out[i] = *l.ExplicitDominated
			}
		}
		return out
	}

	t.Run("either direction dominates; an edge to a third entity does not", func(t *testing.T) {
		st := startJetStream(t)
		// a->b on a's row; d->c only on d's row; e points at x, not f.
		seedBucket(t, st.js, bucketOutgoingIndex, map[string]string{a: row(b), d: row(c), e: row(x)})
		var s summary
		lines := resolveExplicit(t.Context(), st.js, pairs, 2, &s, quiet)
		if got, want := dominated(lines), []any{true, true, false}; !reflect.DeepEqual(got, want) {
			t.Fatalf("explicit_dominated = %v, want %v", got, want)
		}
		if s.MutualPairsExplicitDominated == nil || *s.MutualPairsExplicitDominated != 2 ||
			s.MutualPairsReviewCandidates == nil || *s.MutualPairsReviewCandidates != 1 ||
			s.MutualPairsExplicitDominatedReason != "" {
			t.Fatalf("summary dominated=%v review=%v reason=%q, want 2/1/empty",
				s.MutualPairsExplicitDominated, s.MutualPairsReviewCandidates, s.MutualPairsExplicitDominatedReason)
		}
	})

	t.Run("an unreadable row leaves its pair and the totals null", func(t *testing.T) {
		st := startJetStream(t)
		seedBucket(t, st.js, bucketOutgoingIndex, map[string]string{
			a: row(b), d: row(c), e: `[{"to_entity_id":"","predicate":"code.relation.calls"}]`,
		})
		var s summary
		lines := resolveExplicit(t.Context(), st.js, pairs, 2, &s, quiet)
		if got, want := dominated(lines), []any{true, true, nil}; !reflect.DeepEqual(got, want) {
			t.Fatalf("explicit_dominated = %v, want %v", got, want)
		}
		if s.MutualPairsReviewCandidates != nil || s.MutualPairsExplicitDominated != nil ||
			!strings.Contains(s.MutualPairsExplicitDominatedReason, "1 of 3 pairs unresolved") {
			t.Fatalf("summary review=%v dominated=%v reason=%q, want null/null and 1 of 3 unresolved",
				s.MutualPairsReviewCandidates, s.MutualPairsExplicitDominated, s.MutualPairsExplicitDominatedReason)
		}
	})

	t.Run("a missing bucket leaves the totals null", func(t *testing.T) {
		st := startJetStream(t)
		var s summary
		resolveExplicit(t.Context(), st.js, pairs, 2, &s, quiet)
		if s.MutualPairsReviewCandidates != nil ||
			!strings.Contains(s.MutualPairsExplicitDominatedReason, "open "+bucketOutgoingIndex) {
			t.Fatalf("review=%v reason=%q", s.MutualPairsReviewCandidates, s.MutualPairsExplicitDominatedReason)
		}
	})

	t.Run("a cancelled context leaves the totals null", func(t *testing.T) {
		st := startJetStream(t)
		seedBucket(t, st.js, bucketOutgoingIndex, map[string]string{a: row(b)})
		ctx, cancel := context.WithCancel(t.Context())
		cancel()
		var s summary
		resolveExplicit(ctx, st.js, pairs, 2, &s, quiet)
		if s.MutualPairsReviewCandidates != nil || s.MutualPairsExplicitDominatedReason == "" {
			t.Fatalf("review=%v reason=%q", s.MutualPairsReviewCandidates, s.MutualPairsExplicitDominatedReason)
		}
	})

	t.Run("no pairs is a definitive zero without reading the bucket", func(t *testing.T) {
		st := startJetStream(t)
		var s summary
		resolveExplicit(t.Context(), st.js, nil, 2, &s, quiet)
		if s.MutualPairsReviewCandidates == nil || *s.MutualPairsReviewCandidates != 0 ||
			s.MutualPairsExplicitDominated == nil || *s.MutualPairsExplicitDominated != 0 {
			t.Fatalf("review=%v dominated=%v, want 0/0", s.MutualPairsReviewCandidates, s.MutualPairsExplicitDominated)
		}
	})
}

func TestRunEndToEnd(t *testing.T) {
	st := startJetStream(t)
	a0, a1 := eid("class", "a0"), eid("class", "a1")  // mutual; explicit a0->a1 on a0's row
	b0, b1 := eid("class", "b0"), eid("class", "b1")  // mutual; explicit b1->b0 on b1's row only
	c0, c1 := eid("class", "c0"), eid("method", "c1") // mutual, cross-type, no explicit edge
	d0, d1 := eid("class", "d0"), eid("class", "d1")  // one-way only
	f0, f1 := eid("class", "f0"), eid("class", "f1")  // f1 fails: the pair is lost
	m0, g0 := eid("class", "m0"), eid("class", "g0")  // m0 has no embedding; g0 no neighbours
	ids := []string{a0, a1, b0, b1, c0, c1, d0, d1, f0, f1, m0, g0}
	sim := map[string][]neighbor{
		a0: {{a1, 0.91}}, a1: {{a0, 0.90}},
		b0: {{b1, 0.88}, {c0, 0.70}}, b1: {{b0, 0.88}},
		c0: {{c1, 0.80}}, c1: {{c0, 0.80}},
		d0: {{d1, 0.79}}, d1: {{g0, 0.76}},
		f0: {{f1, 0.95}},
	}
	row := func(to string) string { return `[{"to_entity_id":"` + to + `","predicate":"code.relation.calls"}]` }
	seedBucket(t, st.js, bucketEntityStates, entityRows(ids...))
	seedBucket(t, st.js, bucketEmbeddingIndex, entityRows(ids...))
	seedBucket(t, st.js, bucketOutgoingIndex, map[string]string{a0: row(a1), b1: row(b0), c0: row(g0)})
	resp := &similarResponder{k: 8, similar: sim, fail: map[string]bool{f1: true}, miss: map[string]bool{m0: true}}
	serveSimilar(t, st.nc, resp)

	out := filepath.Join(t.TempDir(), "capture")
	// One failure in twelve is under the 10% budget: the run succeeds and
	// records the loss instead of hiding it.
	if err := run(t.Context(), runArgs(t, st.url, out), quiet); err != nil {
		t.Fatal(err)
	}
	s := readSummary(t, out)
	type counts struct {
		Total, Swept, Queried, Answered, Failed, NotQueried, Miss int
		DirectedAtThreshold, ToUnanswered, Mutual, CrossType      int
		LowerBound, Aborted                                       bool
	}
	got := counts{
		s.TotalEntities, s.EmbeddedInEntityStates, s.Queried, s.Answered, s.Failed, s.NotQueried,
		s.EmbeddingUnavailable, s.DirectedPairsAtThreshold, s.DirectedEdgesToUnanswered, s.MutualPairs,
		s.MutualPairsCrossType, s.MutualPairsIsLowerBound, s.Aborted,
	}
	want := counts{
		Total: 12, Swept: 12, Queried: 12, Answered: 11, Failed: 1, NotQueried: 0, Miss: 1,
		DirectedAtThreshold: 9, ToUnanswered: 1, Mutual: 3, CrossType: 1,
		LowerBound: true, Aborted: false,
	}
	if got != want {
		t.Fatalf("summary counts\n got %+v\nwant %+v", got, want)
	}
	if s.MutualPairsExplicitDominated == nil || *s.MutualPairsExplicitDominated != 2 ||
		s.MutualPairsReviewCandidates == nil || *s.MutualPairsReviewCandidates != 1 {
		t.Fatalf("dominated=%v review=%v, want 2/1", s.MutualPairsExplicitDominated, s.MutualPairsReviewCandidates)
	}
	if !reflect.DeepEqual(s.FailedByKind, map[string]int{failureHandler: 1}) {
		t.Fatalf("failed_by_kind = %v", s.FailedByKind)
	}

	lines := readPairs(t, out)
	var gotPairs []string
	for _, l := range lines {
		if l.ExplicitDominated == nil {
			t.Fatalf("pair %s-%s unresolved", l.A, l.B)
		}
		gotPairs = append(gotPairs, l.A+"-"+l.B+"="+map[bool]string{true: "explicit", false: "review"}[*l.ExplicitDominated])
	}
	wantPairs := []string{a0 + "-" + a1 + "=explicit", b0 + "-" + b1 + "=explicit", c0 + "-" + c1 + "=review"}
	if !reflect.DeepEqual(gotPairs, wantPairs) {
		t.Fatalf("mutual pairs\n got %v\nwant %v", gotPairs, wantPairs)
	}

	seen := resp.seen()
	for _, id := range ids {
		if seen[id] != 1 {
			t.Fatalf("requests %v: want each entity exactly once", seen)
		}
	}
	for _, name := range []string{"directed.jsonl", "manifest.json"} {
		if _, err := os.Stat(filepath.Join(out, name)); err != nil {
			t.Fatal(err)
		}
	}
}

func TestRunFailsWhenExplicitPhaseIncomplete(t *testing.T) {
	st := startJetStream(t)
	ids, sim := tenMutualPlusSingles()
	seedBucket(t, st.js, bucketEntityStates, entityRows(ids...))
	seedBucket(t, st.js, bucketEmbeddingIndex, entityRows(ids...))
	// No OUTGOING_INDEX: the mutual pair cannot be checked for an explicit edge.
	serveSimilar(t, st.nc, &similarResponder{k: 8, similar: sim})

	out := filepath.Join(t.TempDir(), "capture")
	err := run(t.Context(), runArgs(t, st.url, out), quiet)
	if err == nil || !strings.Contains(err.Error(), "explicit-edge") {
		t.Fatalf("run error = %v, want an explicit-edge failure", err)
	}
	// The evidence is still written, with the unknown counts left null.
	s := readSummary(t, out)
	if s.MutualPairs != 1 || s.MutualPairsReviewCandidates != nil || s.MutualPairsExplicitDominatedReason == "" {
		t.Fatalf("mutual=%d review=%v reason=%q", s.MutualPairs, s.MutualPairsReviewCandidates,
			s.MutualPairsExplicitDominatedReason)
	}
	if lines := readPairs(t, out); len(lines) != 1 || lines[0].ExplicitDominated != nil {
		t.Fatalf("mutual_pairs.jsonl = %+v, want one pair with explicit_dominated null", lines)
	}
}

func TestRunOrphanEmbeddings(t *testing.T) {
	st := startJetStream(t)
	ids, sim := tenMutualPlusSingles()
	orphan := eid("class", "orphan")
	sim[ids[0]] = append(sim[ids[0]], neighbor{orphan, 0.95})
	seedBucket(t, st.js, bucketEntityStates, entityRows(ids...))
	seedBucket(t, st.js, bucketEmbeddingIndex, entityRows(append([]string{orphan}, ids...)...))
	seedBucket(t, st.js, bucketOutgoingIndex, nil)
	resp := &similarResponder{k: 8, similar: sim}
	serveSimilar(t, st.nc, resp)

	t.Run("refused by default", func(t *testing.T) {
		out := filepath.Join(t.TempDir(), "capture")
		err := run(t.Context(), runArgs(t, st.url, out), quiet)
		if err == nil || !strings.Contains(err.Error(), "-allow-orphan-embeddings") {
			t.Fatalf("run error = %v, want a refusal naming -allow-orphan-embeddings", err)
		}
		assertAbsent(t, out)
		if n := len(resp.seen()); n != 0 {
			t.Fatalf("%d entities were queried before the refusal", n)
		}
	})

	t.Run("allowed: the sweep covers ENTITY_STATES only", func(t *testing.T) {
		out := filepath.Join(t.TempDir(), "capture")
		if err := run(t.Context(), runArgs(t, st.url, out, "-allow-orphan-embeddings"), quiet); err != nil {
			t.Fatal(err)
		}
		s := readSummary(t, out)
		if s.TotalEntities != 10 || s.EmbeddedEntities != 11 || s.EmbeddedNotInEntityStates != 1 ||
			s.EmbeddedInEntityStates != 10 {
			t.Fatalf("total=%d embedded=%d orphans=%d swept=%d, want 10/11/1/10",
				s.TotalEntities, s.EmbeddedEntities, s.EmbeddedNotInEntityStates, s.EmbeddedInEntityStates)
		}
		if s.Queried+s.NotQueried != s.EmbeddedInEntityStates || s.Queried != s.Answered+s.Failed {
			t.Fatalf("queried=%d not_queried=%d answered=%d failed=%d: denominators do not close over the 10 swept",
				s.Queried, s.NotQueried, s.Answered, s.Failed)
		}
		if s.NeighborIDsNotInEntityStates != 1 || s.MutualPairs != 1 {
			t.Fatalf("neighbor_ids_not_in_entity_states=%d mutual=%d, want 1/1",
				s.NeighborIDsNotInEntityStates, s.MutualPairs)
		}
		seen := resp.seen()
		if seen[orphan] != 0 || len(seen) != 10 {
			t.Fatalf("requests %v: want each of the 10 entities once and never the orphan", seen)
		}
	})
}

func TestRunRefusesExistingOutput(t *testing.T) {
	out := t.TempDir()
	marker := filepath.Join(out, "keep")
	if err := os.WriteFile(marker, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	// No server is needed: the refusal precedes any connection.
	if err := run(t.Context(), runArgs(t, "nats://127.0.0.1:1", out), quiet); err == nil {
		t.Fatal("run accepted an existing output directory")
	}
	if _, err := os.Stat(marker); err != nil {
		t.Fatalf("existing output was disturbed: %v", err)
	}
}

func TestRunRemovesOutputOnEarlyFailure(t *testing.T) {
	st := startJetStream(t) // no buckets: listing ENTITY_STATES fails
	out := filepath.Join(t.TempDir(), "capture")
	if err := run(t.Context(), runArgs(t, st.url, out), quiet); err == nil {
		t.Fatal("run succeeded without ENTITY_STATES")
	}
	assertAbsent(t, out)
}
