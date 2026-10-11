package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
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

const (
	idFunc    = "semselect.semsource.golang.fam.function.guard-go-New"
	idPassage = "semselect.semsource.web.fam.chunk.README-md-0003"
	idFolder  = "semselect.semsource.code.fam.folder.internal"
)

func state(id string, triples ...map[string]any) string {
	b, _ := json.Marshal(map[string]any{"id": id, "triples": triples, "message_type": "ast.entity"})
	return string(b)
}

func tr(predicate string, object any) map[string]any {
	return map[string]any{"subject": "s", "predicate": predicate, "object": object, "source": "semsource",
		"timestamp": "2026-10-10T00:00:00Z", "confidence": 1.0}
}

func seedStates(t *testing.T, js jetstream.JetStream, rows map[string]string) {
	t.Helper()
	kv, err := js.CreateKeyValue(t.Context(), jetstream.KeyValueConfig{Bucket: bucketEntityStates})
	if err != nil {
		t.Fatal(err)
	}
	for k, v := range rows {
		if _, err := kv.Put(t.Context(), k, []byte(v)); err != nil {
			t.Fatal(err)
		}
	}
}

func seedBodies(t *testing.T, js jetstream.JetStream, bodies map[string][]byte) {
	t.Helper()
	store, err := js.CreateObjectStore(t.Context(), jetstream.ObjectStoreConfig{Bucket: "CONTENT"})
	if err != nil {
		t.Fatal(err)
	}
	for k, v := range bodies {
		if _, err := store.PutBytes(t.Context(), k, v); err != nil {
			t.Fatal(err)
		}
	}
}

func testOptions(t *testing.T, url string) options {
	t.Helper()
	opts, err := parseFlags([]string{"-nats", url, "-output", filepath.Join(t.TempDir(), "hydration")})
	if err != nil {
		t.Fatal(err)
	}
	opts.listTimeout, opts.getTimeout = 20*time.Second, 10*time.Second
	return opts
}

func readSummary(t *testing.T, dir string) summary {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(dir, "hydration.json"))
	if err != nil {
		t.Fatal(err)
	}
	var s summary
	if err := json.Unmarshal(raw, &s); err != nil {
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
	sc.Buffer(make([]byte, 1<<20), 64<<20)
	for sc.Scan() {
		var row T
		if err := json.Unmarshal(sc.Bytes(), &row); err != nil {
			t.Fatal(err)
		}
		rows = append(rows, row)
	}
	if err := sc.Err(); err != nil {
		t.Fatal(err)
	}
	return rows
}

func TestHydrateDumpsStatesAndBodies(t *testing.T) {
	st := startJetStream(t)
	funcBody := []byte("func New(c Config) (*Guard, error) {\n\treturn nil, nil\n}\n")
	passageBody := []byte("## Configuration\n\nThe guard reads SEMSELECT_ADDR.\n")
	binary := []byte{0xff, 0xfe, 'x'}
	seedStates(t, st.js, map[string]string{
		idFunc: state(idFunc, tr("code.artifact.path", "internal/guard/guard.go"), tr("code.metric.start-line", 10),
			tr("code.body.store", "objectstore"), tr("code.body.key", "sha-func")),
		idPassage: state(idPassage, tr("source.doc.file-path", "README.md"), tr("source.doc.chunk-index", 3),
			tr("source.doc.body-store", "objectstore"), tr("source.doc.body-key", "sha-passage")),
		idFolder: state(idFolder, tr("code.artifact.path", "internal")),
		"semselect.semsource.web.fam.chunk.README-md-0004": state("semselect.semsource.web.fam.chunk.README-md-0004",
			tr("source.doc.body-store", "objectstore"), tr("source.doc.body-key", "sha-passage")), // shared body
		"semselect.semsource.golang.fam.const.bin": state("semselect.semsource.golang.fam.const.bin",
			tr("code.body.store", "objectstore"), tr("code.body.key", "sha-bin")),
	})
	seedBodies(t, st.js, map[string][]byte{"sha-func": funcBody, "sha-passage": passageBody, "sha-bin": binary})
	opts := testOptions(t, st.url)
	if err := run(context.Background(), opts, io.Discard); err != nil {
		t.Fatal(err)
	}
	sum := readSummary(t, opts.output)
	if sum.Entities != 5 || sum.WithBodyHandle != 4 || sum.DistinctBodyKeys != 3 || sum.BodiesFetched != 3 {
		t.Fatalf("summary counts: %+v", sum)
	}
	if !sum.ContentBucketPresent || sum.InvalidUTF8 != 1 || len(sum.BodiesMissing) != 0 || len(sum.MalformedStates) != 0 {
		t.Fatalf("summary: %+v", sum)
	}
	if sum.HandlesByPredicate["code.body.key"] != 2 || sum.HandlesByPredicate["source.doc.body-key"] != 2 {
		t.Fatalf("handles by predicate: %v", sum.HandlesByPredicate)
	}
	for name, ok := range sum.Checks {
		if !ok {
			t.Fatalf("check %s failed", name)
		}
	}
	states := readLines[stateRow](t, filepath.Join(opts.output, "entity_states.jsonl"))
	if len(states) != 5 {
		t.Fatalf("want 5 state rows, got %d", len(states))
	}
	for i := 1; i < len(states); i++ {
		if states[i-1].EntityID >= states[i].EntityID {
			t.Fatalf("state rows not sorted: %s then %s", states[i-1].EntityID, states[i].EntityID)
		}
	}
	for _, row := range states {
		var parsed map[string]any
		if row.Raw != "" || json.Unmarshal(row.State, &parsed) != nil || parsed["id"] != row.EntityID {
			t.Fatalf("state row %s does not carry the verbatim state: %+v", row.EntityID, row)
		}
		switch row.EntityID {
		case idFunc:
			if row.BodyStore != "objectstore" || row.BodyKey != "sha-func" || row.Triples != 4 {
				t.Fatalf("function row: %+v", row)
			}
		case idFolder:
			if row.BodyKey != "" || row.BodyStore != "" {
				t.Fatalf("folder row should carry no handle: %+v", row)
			}
		}
	}
	bodies := readLines[bodyRow](t, filepath.Join(opts.output, "bodies.jsonl"))
	if len(bodies) != 3 {
		t.Fatalf("want 3 body rows, got %d", len(bodies))
	}
	byKey := map[string]bodyRow{}
	for _, b := range bodies {
		byKey[b.Key] = b
	}
	if byKey["sha-func"].Text != string(funcBody) || !byKey["sha-func"].ValidUTF8 || byKey["sha-func"].Bytes != len(funcBody) {
		t.Fatalf("function body: %+v", byKey["sha-func"])
	}
	if got := byKey["sha-passage"].Entities; len(got) != 2 || got[0] != idPassage {
		t.Fatalf("shared passage body should list both entities sorted: %v", got)
	}
	if byKey["sha-bin"].ValidUTF8 || byKey["sha-bin"].Text != "" || byKey["sha-bin"].Base64 == "" {
		t.Fatalf("binary body should be base64: %+v", byKey["sha-bin"])
	}
}

func TestHydrateFailsLoudlyWhenABodyIsMissing(t *testing.T) {
	st := startJetStream(t)
	seedStates(t, st.js, map[string]string{
		idFunc:    state(idFunc, tr("code.body.store", "objectstore"), tr("code.body.key", "sha-func")),
		idPassage: state(idPassage, tr("source.doc.body-store", "objectstore"), tr("source.doc.body-key", "sha-gone")),
	})
	seedBodies(t, st.js, map[string][]byte{"sha-func": []byte("body")})
	opts := testOptions(t, st.url)
	err := run(context.Background(), opts, io.Discard)
	if !errors.Is(err, errChecksFailed) {
		t.Fatalf("want errChecksFailed, got %v", err)
	}
	sum := readSummary(t, opts.output)
	if len(sum.BodiesMissing) != 1 || sum.BodiesMissing[0] != idPassage || sum.BodiesFetched != 1 {
		t.Fatalf("summary: %+v", sum)
	}
	if sum.Checks["all_handles_resolved"] || !sum.Checks["all_states_parse"] {
		t.Fatalf("checks: %v", sum.Checks)
	}
}

func TestHydrateFailsWhenHandlesExistButTheBucketDoesNot(t *testing.T) {
	st := startJetStream(t)
	seedStates(t, st.js, map[string]string{
		idFunc: state(idFunc, tr("code.body.store", "objectstore"), tr("code.body.key", "sha-func")),
	})
	opts := testOptions(t, st.url)
	err := run(context.Background(), opts, io.Discard)
	if !errors.Is(err, errChecksFailed) {
		t.Fatalf("want errChecksFailed, got %v", err)
	}
	sum := readSummary(t, opts.output)
	if sum.ContentBucketPresent || len(sum.BodiesMissing) != 1 {
		t.Fatalf("summary: %+v", sum)
	}
}

func TestHydrateWithoutHandlesSucceedsWithoutTheBucket(t *testing.T) {
	st := startJetStream(t)
	seedStates(t, st.js, map[string]string{
		idFolder:                                state(idFolder, tr("code.artifact.path", "internal")),
		"semselect.semsource.code.fam.repo.fam": "not json",
	})
	opts := testOptions(t, st.url)
	err := run(context.Background(), opts, io.Discard)
	if !errors.Is(err, errChecksFailed) {
		t.Fatalf("a malformed state must fail the parse check, got %v", err)
	}
	sum := readSummary(t, opts.output)
	if sum.ContentBucketPresent || sum.Entities != 2 || len(sum.MalformedStates) != 1 || !sum.Checks["all_handles_resolved"] {
		t.Fatalf("summary: %+v", sum)
	}
	states := readLines[stateRow](t, filepath.Join(opts.output, "entity_states.jsonl"))
	if len(states) != 2 {
		t.Fatalf("malformed states are still dumped verbatim: got %d rows", len(states))
	}
	for _, row := range states {
		if row.EntityID == "semselect.semsource.code.fam.repo.fam" && (row.Raw != "not json" || row.State != nil) {
			t.Fatalf("non-JSON value should be dumped as raw text: %+v", row)
		}
	}
}

func TestOutputMustNotExist(t *testing.T) {
	dir := t.TempDir()
	opts, err := parseFlags([]string{"-nats", "nats://127.0.0.1:1", "-output", dir})
	if err != nil {
		t.Fatal(err)
	}
	if err := run(context.Background(), opts, io.Discard); err == nil || errors.Is(err, errChecksFailed) {
		t.Fatalf("want a refusal to overwrite, got %v", err)
	}
}

func TestParseFlagsRejectsNonPositiveBounds(t *testing.T) {
	for _, args := range [][]string{
		{"-output", "x", "-max-keys", "0"},
		{"-output", "x", "-max-body-bytes", "-1"},
		{"-output", "x", "-get-timeout", "0s"},
		{},
	} {
		if _, err := parseFlags(args); err == nil {
			t.Fatalf("args %v should be rejected", args)
		}
	}
}

func TestBodyHandlePrefersCompletePairs(t *testing.T) {
	store, key, pred := bodyHandle([]triple{{Predicate: "code.body.store", Object: "objectstore"}})
	if store != "" || key != "" || pred != "" {
		t.Fatalf("a store without a key is not a handle: %q %q %q", store, key, pred)
	}
	store, key, pred = bodyHandle([]triple{
		{Predicate: "code.body.key", Object: 42}, // not a string
		{Predicate: "source.doc.body-store", Object: "objectstore"},
		{Predicate: "source.doc.body-key", Object: "abc"},
	})
	if store != "objectstore" || key != "abc" || pred != "source.doc.body-key" {
		t.Fatalf("doc handle expected: %q %q %q", store, key, pred)
	}
}
