package main

import (
	"bufio"
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/nats-io/nats-server/v2/server"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

type testStack struct {
	url string
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
	return testStack{url: s.ClientURL(), js: js}
}

func seedBucket(t *testing.T, js jetstream.JetStream, bucket string, rows map[string]string) jetstream.KeyValue {
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
	return kv
}

func incomingKey(target, source, predicate string) string {
	return target + "." + source + "." + hex.EncodeToString([]byte(predicate))
}

func communityValue(id string, level int, members ...string) string {
	v, _ := json.Marshal(map[string]any{"id": id, "level": level, "members": members, "metadata": map[string]any{"size": len(members)}})
	return string(v)
}

// A four-entity family: a file containing two functions (explicit edges), a
// struct with no explicit edges, and one dangling edge to an entity that is not
// in ENTITY_STATES. The partition puts the file and both functions together.
var (
	fileA   = fid("fam", "file", "a")
	funcB   = fid("fam", "function", "b")
	funcC   = fid("fam", "function", "c")
	structD = fid("fam", "struct", "d")
	ghostE  = fid("fam", "function", "e")
)

func seedFamily(t *testing.T, js jetstream.JetStream, withPartition bool) {
	t.Helper()
	seedBucket(t, js, bucketEntityStates, map[string]string{fileA: "{}", funcB: "{}", funcC: "{}", structD: "{}"})
	seedBucket(t, js, bucketOutgoingIndex, map[string]string{
		fileA: `[{"predicate":"code.structure.contains","to_entity_id":"` + funcB + `"},{"predicate":"code.structure.contains","to_entity_id":"` + funcC + `"}]`,
		funcB: `[{"predicate":"code.relationship.calls","to_entity_id":"` + ghostE + `"}]`,
	})
	seedBucket(t, js, bucketIncomingIndex, map[string]string{
		incomingKey(funcB, fileA, "code.structure.contains"):  "",
		incomingKey(funcC, fileA, "code.structure.contains"):  "",
		incomingKey(ghostE, funcB, "code.relationship.calls"): "",
		// Incoming-only: an edge the outgoing index does not carry.
		incomingKey(structD, funcC, "code.relationship.references"): "",
	})
	seedBucket(t, js, bucketEmbeddingIndex, map[string]string{fileA: "{}", funcB: "{}", funcC: "{}"})
	rows := map[string]string{}
	if withPartition {
		rows = partitionRows()
	}
	seedBucket(t, js, bucketCommunityIndex, rows)
}

func partitionRows() map[string]string {
	return map[string]string{
		"0." + fileA:          communityValue(fileA, 0, fileA, funcB, funcC),
		"0." + structD:        communityValue(structD, 0, structD),
		"entity.0." + fileA:   fileA,
		"entity.0." + funcB:   fileA,
		"entity.0." + funcC:   fileA,
		"entity.0." + structD: structD,
		"1." + fileA:          communityValue(fileA, 1, fileA, funcB, funcC, structD),
		"entity.1." + fileA:   fileA,
		"entity.1." + funcB:   fileA,
		"entity.1." + funcC:   fileA,
		"entity.1." + structD: fileA,
	}
}

func testOptions(t *testing.T, url string) options {
	t.Helper()
	return options{
		natsURL: url, output: filepath.Join(t.TempDir(), "out"), listTimeout: 20 * time.Second, maxKeys: 1000,
		poll: 100 * time.Millisecond, settle: 300 * time.Millisecond, settleTimeout: 10 * time.Second,
		identity: semanticProfile,
	}
}

func readSummary(t *testing.T, dir string) summary {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(dir, "structural.json"))
	if err != nil {
		t.Fatal(err)
	}
	var s summary
	if err := json.Unmarshal(data, &s); err != nil {
		t.Fatal(err)
	}
	return s
}

func readLines[T any](t *testing.T, p string) []T {
	t.Helper()
	f, err := os.Open(p)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var rows []T
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		var row T
		if err := json.Unmarshal(sc.Bytes(), &row); err != nil {
			t.Fatal(err)
		}
		rows = append(rows, row)
	}
	return rows
}

func TestCaptureFreezesPartitionTopologyAndVotes(t *testing.T) {
	st := startJetStream(t)
	seedFamily(t, st.js, true)
	opts := testOptions(t, st.url)
	meta := filepath.Join(t.TempDir(), "run.json")
	if err := os.WriteFile(meta, []byte(`{"family":"test"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	opts.runMetadata = meta

	if err := run(t.Context(), opts, io.Discard); err != nil {
		t.Fatalf("run: %v", err)
	}
	s := readSummary(t, opts.output)
	if !s.Settle.Settled || !s.Settle.StableAfterRead || !s.Settle.ValuesByteStable || len(s.Settle.Reads) < 2 {
		t.Fatalf("settle = %+v", s.Settle)
	}
	for _, c := range []string{"partition_settled", "partition_covers_entity_states", "partition_members_disjoint",
		"partition_entity_map_consistent", "stable_after_topology_read", "no_malformed_entity_ids"} {
		if !s.Checks[c] {
			t.Errorf("check %s false", c)
		}
	}
	if s.Checks["explicit_indexes_consistent"] {
		t.Error("the incoming-only edge must be reported as an index inconsistency")
	}
	if s.Explicit.IncomingOnly != 1 || s.Explicit.OutgoingOnly != 0 || s.Explicit.Edges != 3 || s.Explicit.EdgesToOutsideEntityStates != 1 {
		t.Errorf("explicit = %+v", s.Explicit)
	}
	if s.Entities.Total != 4 || s.Entities.Embedded != 3 || s.Entities.ByType["function"] != 2 {
		t.Errorf("entities = %+v", s.Entities)
	}
	var runMeta map[string]string
	if err := json.Unmarshal(s.Run, &runMeta); err != nil || runMeta["family"] != "test" {
		t.Errorf("run metadata = %s", s.Run)
	}
	if len(s.Partition.Levels) != 2 || s.Partition.Levels[0].Communities != 2 || s.Partition.Levels[0].Singletons != 1 || s.Partition.Levels[1].Largest != 4 {
		t.Errorf("partition levels = %+v", s.Partition.Levels)
	}

	// Voting graph: fileA votes over its two explicit neighbours, its sibling
	// list is empty (no other file) and its system peers are the rest of the
	// family; funcB's "both" set is fileA (incoming) and ghostE (outgoing);
	// structD has the incoming-only edge from funcC in its explicit set.
	votes := map[string]map[string]votingEdge{}
	for _, e := range readLines[votingEdge](t, filepath.Join(opts.output, "voting_edges.jsonl")) {
		if votes[e.From] == nil {
			votes[e.From] = map[string]votingEdge{}
		}
		votes[e.From][e.To] = e
	}
	want := []struct {
		from, to, listed, tier string
		weight                 float64
		same                   *bool
	}{
		{fileA, funcB, tierExplicit, tierExplicit, 1.0, ptr(true)},
		{fileA, structD, tierSystemPeer, tierSystemPeer, 0.2, ptr(false)},
		{funcB, fileA, tierExplicit, tierExplicit, 1.0, ptr(true)},
		{funcB, ghostE, tierExplicit, tierExplicit, 1.0, nil},
		{funcB, funcC, tierSibling, tierSibling, 0.7, ptr(true)},
		{funcC, structD, tierSystemPeer, tierSystemPeer, 0.2, ptr(false)},
		{structD, funcC, tierExplicit, tierExplicit, 1.0, ptr(false)},
	}
	for _, w := range want {
		e, ok := votes[w.from][w.to]
		if !ok {
			t.Errorf("missing voting edge %s -> %s", w.from, w.to)
			continue
		}
		if e.ListedAs != w.listed || e.WeightTier != w.tier || e.Weight != w.weight {
			t.Errorf("%s -> %s = %+v, want %s/%s/%v", w.from, w.to, e, w.listed, w.tier, w.weight)
		}
		if (w.same == nil) != (e.SameCommunityLevel0 == nil) || (w.same != nil && *w.same != *e.SameCommunityLevel0) {
			t.Errorf("%s -> %s same_community_level0 = %v, want %v", w.from, w.to, e.SameCommunityLevel0, w.same)
		}
	}
	if _, ok := votes[funcC][funcB]; !ok {
		t.Error("funcC must list funcB as a sibling")
	}
	if s.Voting.Edges != len(readLines[votingEdge](t, filepath.Join(opts.output, "voting_edges.jsonl"))) {
		t.Error("voting edge count disagrees with the file")
	}
	ents := readLines[entityRecord](t, filepath.Join(opts.output, "entities.jsonl"))
	if len(ents) != 4 || ents[0].ID != fileA || ents[0].CommunityByLevel["0"] != fileA || ents[0].CommunityByLevel["1"] != fileA {
		t.Errorf("entities.jsonl = %+v", ents)
	}
	for _, name := range []string{"explicit_edges.jsonl", "partition.json"} {
		if _, err := os.Stat(filepath.Join(opts.output, name)); err != nil {
			t.Error(err)
		}
	}
}

func ptr(b bool) *bool { return &b }

func TestCaptureWaitsForThePartitionToAppear(t *testing.T) {
	st := startJetStream(t)
	seedFamily(t, st.js, false)
	opts := testOptions(t, st.url)
	done := make(chan error, 1)
	go func() { done <- run(t.Context(), opts, io.Discard) }()

	time.Sleep(400 * time.Millisecond)
	kv, err := st.js.KeyValue(t.Context(), bucketCommunityIndex)
	if err != nil {
		t.Fatal(err)
	}
	for k, v := range partitionRows() {
		if _, err := kv.Put(t.Context(), k, []byte(v)); err != nil {
			t.Fatal(err)
		}
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("run: %v", err)
		}
	case <-time.After(15 * time.Second):
		t.Fatal("run did not finish")
	}
	s := readSummary(t, opts.output)
	if !s.Settle.Settled || len(s.Settle.Reads) < 3 {
		t.Fatalf("expected uncovered reads before settling: %+v", s.Settle)
	}
	if s.Settle.Reads[0].Covered || s.Settle.Reads[0].Note == "" {
		t.Errorf("first read must record the coverage gap: %+v", s.Settle.Reads[0])
	}
}

func TestCaptureFailsLoudlyWhenThePartitionNeverSettles(t *testing.T) {
	st := startJetStream(t)
	seedFamily(t, st.js, true)
	kv, err := st.js.KeyValue(t.Context(), bucketCommunityIndex)
	if err != nil {
		t.Fatal(err)
	}
	// A graph still being ingested: every tick adds an entity and its singleton
	// community, so no two reads see the same partition.
	entities, err := st.js.KeyValue(t.Context(), bucketEntityStates)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	go func() {
		for tick := 0; ctx.Err() == nil; tick++ {
			id := fid("fam", "function", "late"+strconv.Itoa(tick))
			_, _ = entities.Put(ctx, id, []byte("{}"))
			_, _ = kv.Put(ctx, "0."+id, []byte(communityValue(id, 0, id)))
			_, _ = kv.Put(ctx, "entity.0."+id, []byte(id))
			time.Sleep(50 * time.Millisecond)
		}
	}()
	opts := testOptions(t, st.url)
	opts.settleTimeout = 1500 * time.Millisecond
	err = run(ctx, opts, io.Discard)
	if !errors.Is(err, errChecksFailed) {
		t.Fatalf("run = %v, want %v", err, errChecksFailed)
	}
	s := readSummary(t, opts.output)
	if s.Settle.Settled || s.Checks["partition_settled"] {
		t.Fatalf("must not report settled: %+v", s.Settle)
	}
}

// The legacy statistical summarizer rewrites keywords in a different order on
// every cycle; memberships are what settle, and the instability is reported.
func TestSummaryDecorationDoesNotBlockSettling(t *testing.T) {
	st := startJetStream(t)
	seedFamily(t, st.js, true)
	kv, err := st.js.KeyValue(t.Context(), bucketCommunityIndex)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	go func() {
		for tick := 0; ctx.Err() == nil; tick++ {
			v, _ := json.Marshal(map[string]any{"id": structD, "level": 0, "members": []string{structD},
				"keywords": []string{"k" + strconv.Itoa(tick)}, "metadata": map[string]any{"size": 1}})
			_, _ = kv.Put(ctx, "0."+structD, v)
			time.Sleep(50 * time.Millisecond)
		}
	}()
	opts := testOptions(t, st.url)
	if err := run(ctx, opts, io.Discard); err != nil {
		t.Fatalf("run: %v", err)
	}
	s := readSummary(t, opts.output)
	if !s.Settle.Settled || s.Settle.ValuesByteStable {
		t.Fatalf("expected settled memberships with unstable bytes: %+v", s.Settle)
	}
	last := s.Settle.Reads[len(s.Settle.Reads)-1]
	if last.RawValueHash == s.Settle.Reads[len(s.Settle.Reads)-2].RawValueHash || !strings.Contains(last.Note, "byte-wise") {
		t.Fatalf("raw hashes must differ and the note must say so: %+v", s.Settle.Reads)
	}
}

func TestOutputMustNotExist(t *testing.T) {
	opts := testOptions(t, "nats://127.0.0.1:1")
	if err := os.Mkdir(opts.output, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := run(t.Context(), opts, io.Discard); err == nil || !strings.Contains(err.Error(), "create -output") {
		t.Fatalf("run = %v", err)
	}
}

func TestParseFlagsRejectsZeroProfile(t *testing.T) {
	if _, err := parseFlags([]string{"-output", "x", "-max-siblings", "0"}, io.Discard); err == nil {
		t.Fatal("zero cap must be rejected rather than defaulted by the legacy provider")
	}
	o, err := parseFlags([]string{"-output", "x"}, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	if o.identity != semanticProfile {
		t.Fatalf("defaults = %+v, want the semantic-profile structural baseline", o.identity)
	}
}

func TestParseIncomingKey(t *testing.T) {
	target, source, pred, ok := parseIncomingKey(incomingKey(funcB, fileA, "code.structure.contains"))
	if !ok || target != funcB || source != fileA || pred != "code.structure.contains" {
		t.Fatalf("parse = %q %q %q %v", target, source, pred, ok)
	}
	for _, bad := range []string{"a.b", funcB + "." + fileA + ".zz", funcB + "." + fileA, "..." + funcB + "." + fileA + ".61"} {
		if _, _, _, ok := parseIncomingKey(bad); ok {
			t.Errorf("accepted malformed key %q", bad)
		}
	}
}
