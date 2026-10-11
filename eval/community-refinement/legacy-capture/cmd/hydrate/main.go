// Command hydrate dumps every ENTITY_STATES value of a live SemSource/SemStreams
// stack, read-only, together with the verbatim bodies those states reference
// in the CONTENT object store (SemSource ADR-062: code.body.store/key and
// source.doc.body-store/key handles). The quality pilot's reviewer packets
// are serialized from this dump offline, so the passages a reviewer sees are
// the bytes the system embedded, not a re-parse of the workspace.
//
// Legacy SemStreams capture; not a SemEngine result.
package main

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"time"
	"unicode/utf8"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

const (
	bucketEntityStates = "ENTITY_STATES"
	provenance         = "legacy SemStreams capture; not a SemEngine result"
	// bodyStoreInstance is the storage component name SemSource stamps into
	// every body handle (graph/bodystore.go BodyStoreInstance).
	bodyStoreInstance = "objectstore"
)

// bodyHandles are the (store, key) predicate pairs a producer stamps when it
// offloads an entity's verbatim body (source/ast/predicates.go,
// source/vocabulary/predicates.go at the pinned SemSource commit).
var bodyHandles = [][2]string{
	{"code.body.store", "code.body.key"},
	{"source.doc.body-store", "source.doc.body-key"},
}

var errChecksFailed = errors.New("hydration checks failed; see hydration.json")

type options struct {
	nats          string
	output        string
	contentBucket string
	runMetadata   string
	listTimeout   time.Duration
	getTimeout    time.Duration
	maxKeys       int
	maxValueBytes int
	maxBodyBytes  int
}

func parseFlags(args []string) (options, error) {
	var o options
	fs := flag.NewFlagSet("hydrate", flag.ContinueOnError)
	fs.StringVar(&o.nats, "nats", "nats://127.0.0.1:4222", "NATS URL of the stack under capture")
	fs.StringVar(&o.output, "output", "", "output directory (must not exist)")
	fs.StringVar(&o.contentBucket, "content-bucket", "CONTENT", "object store bucket holding verbatim bodies")
	fs.StringVar(&o.runMetadata, "run-metadata", "", "optional JSON file copied verbatim into hydration.json as .run")
	fs.DurationVar(&o.listTimeout, "list-timeout", 2*time.Minute, "bound on listing ENTITY_STATES keys")
	fs.DurationVar(&o.getTimeout, "get-timeout", 30*time.Second, "bound on one KV or object read")
	fs.IntVar(&o.maxKeys, "max-keys", 500000, "refuse more ENTITY_STATES keys than this")
	fs.IntVar(&o.maxValueBytes, "max-value-bytes", 4<<20, "refuse an entity state larger than this")
	fs.IntVar(&o.maxBodyBytes, "max-body-bytes", 8<<20, "record, but do not store, a body larger than this")
	if err := fs.Parse(args); err != nil {
		return o, err
	}
	if o.output == "" {
		return o, errors.New("-output is required")
	}
	if o.maxKeys <= 0 || o.maxValueBytes <= 0 || o.maxBodyBytes <= 0 || o.listTimeout <= 0 || o.getTimeout <= 0 {
		return o, errors.New("bounds and timeouts must be positive")
	}
	return o, nil
}

// stateRow is one ENTITY_STATES value, verbatim, with the body handle lifted
// out of its triples for the serializer.
type stateRow struct {
	EntityID  string          `json:"entity_id"`
	Bytes     int             `json:"bytes"`
	SHA256    string          `json:"sha256"`
	Triples   int             `json:"triples"`
	BodyStore string          `json:"body_store,omitempty"`
	BodyKey   string          `json:"body_key,omitempty"`
	State     json.RawMessage `json:"state,omitempty"`
	// Raw carries a value that is not JSON (then State is absent and the
	// entity is listed in malformed_states).
	Raw string `json:"raw,omitempty"`
}

// bodyRow is one distinct body blob and the entities whose handles name it.
type bodyRow struct {
	Store     string   `json:"store"`
	Key       string   `json:"key"`
	Entities  []string `json:"entities"`
	Bytes     int      `json:"bytes"`
	SHA256    string   `json:"sha256"`
	ValidUTF8 bool     `json:"valid_utf8"`
	Text      string   `json:"text,omitempty"`
	Base64    string   `json:"base64,omitempty"`
}

type summary struct {
	Provenance           string          `json:"provenance"`
	StartedUTC           string          `json:"started_utc"`
	FinishedUTC          string          `json:"finished_utc"`
	NATSURL              string          `json:"nats_url"`
	Parameters           parameters      `json:"parameters"`
	Entities             int             `json:"entities"`
	StateBytes           int             `json:"state_bytes"`
	MalformedStates      []string        `json:"malformed_states"`
	StateIDMismatches    []string        `json:"state_id_mismatches"`
	WithBodyHandle       int             `json:"with_body_handle"`
	HandlesByPredicate   map[string]int  `json:"handles_by_predicate"`
	HandlesByStore       map[string]int  `json:"handles_by_store"`
	DistinctBodyKeys     int             `json:"distinct_body_keys"`
	ContentBucketPresent bool            `json:"content_bucket_present"`
	BodiesFetched        int             `json:"bodies_fetched"`
	BodyBytes            int             `json:"body_bytes"`
	BodiesMissing        []string        `json:"bodies_missing"`
	BodiesOversize       []string        `json:"bodies_oversize"`
	InvalidUTF8          int             `json:"invalid_utf8"`
	Checks               map[string]bool `json:"checks"`
	Run                  json.RawMessage `json:"run,omitempty"`
}

type parameters struct {
	ContentBucket string `json:"content_bucket"`
	MaxKeys       int    `json:"max_keys"`
	MaxValueBytes int    `json:"max_value_bytes"`
	MaxBodyBytes  int    `json:"max_body_bytes"`
	ListTimeout   string `json:"list_timeout"`
	GetTimeout    string `json:"get_timeout"`
}

func main() {
	opts, err := parseFlags(os.Args[1:])
	if err != nil {
		fmt.Fprintln(os.Stderr, "hydrate:", err)
		os.Exit(2)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := run(ctx, opts, os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, "hydrate:", err)
		if errors.Is(err, errChecksFailed) {
			os.Exit(1)
		}
		os.Exit(2)
	}
}

func run(ctx context.Context, opts options, log io.Writer) error {
	if _, err := os.Stat(opts.output); err == nil {
		return fmt.Errorf("output %s already exists; refusing to overwrite evidence", opts.output)
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	var runMeta json.RawMessage
	if opts.runMetadata != "" {
		raw, err := os.ReadFile(opts.runMetadata)
		if err != nil {
			return err
		}
		if !json.Valid(raw) {
			return fmt.Errorf("%s is not JSON", opts.runMetadata)
		}
		runMeta = raw
	}
	sum := &summary{
		Provenance: provenance, StartedUTC: time.Now().UTC().Format(time.RFC3339), NATSURL: opts.nats,
		Parameters: parameters{ContentBucket: opts.contentBucket, MaxKeys: opts.maxKeys, MaxValueBytes: opts.maxValueBytes,
			MaxBodyBytes: opts.maxBodyBytes, ListTimeout: opts.listTimeout.String(), GetTimeout: opts.getTimeout.String()},
		MalformedStates: []string{}, StateIDMismatches: []string{}, HandlesByPredicate: map[string]int{},
		HandlesByStore: map[string]int{}, BodiesMissing: []string{}, BodiesOversize: []string{}, Run: runMeta,
	}

	nc, err := nats.Connect(opts.nats, nats.Name("semselect-hydrate"), nats.Timeout(10*time.Second))
	if err != nil {
		return fmt.Errorf("connect %s: %w", opts.nats, err)
	}
	defer nc.Close()
	js, err := jetstream.New(nc)
	if err != nil {
		return err
	}
	kv, err := js.KeyValue(ctx, bucketEntityStates)
	if err != nil {
		return fmt.Errorf("open %s: %w", bucketEntityStates, err)
	}

	keys, err := listKeys(ctx, kv, opts.maxKeys, opts.listTimeout)
	if err != nil {
		return err
	}
	sort.Strings(keys)
	states := make([]stateRow, 0, len(keys))
	handles := map[string][]string{} // body key -> entity ids
	handleStore := map[string]string{}
	for _, key := range keys {
		value, ok, err := getValue(ctx, kv, key, opts.maxValueBytes, opts.getTimeout)
		if err != nil {
			return err
		}
		if !ok {
			continue // deleted between the listing and the read
		}
		row := stateRow{EntityID: key, Bytes: len(value), SHA256: sha256Hex(value)}
		parsed, perr := parseState(value)
		switch {
		case !json.Valid(value):
			row.Raw = string(value)
			sum.MalformedStates = append(sum.MalformedStates, key)
		case perr != nil:
			row.State = json.RawMessage(value)
			sum.MalformedStates = append(sum.MalformedStates, key)
		default:
			row.State = json.RawMessage(value)
			row.Triples = len(parsed.Triples)
			if parsed.ID != key {
				sum.StateIDMismatches = append(sum.StateIDMismatches, key)
			}
			store, bodyKey, predicate := bodyHandle(parsed.Triples)
			if bodyKey != "" {
				row.BodyStore, row.BodyKey = store, bodyKey
				sum.WithBodyHandle++
				sum.HandlesByPredicate[predicate]++
				sum.HandlesByStore[store]++
				handles[bodyKey] = append(handles[bodyKey], key)
				handleStore[bodyKey] = store
			}
		}
		sum.StateBytes += len(value)
		states = append(states, row)
	}
	sum.Entities = len(states)
	sum.DistinctBodyKeys = len(handles)
	fmt.Fprintf(log, "hydrate: %d entity states (%d bytes), %d with a body handle, %d distinct bodies\n",
		sum.Entities, sum.StateBytes, sum.WithBodyHandle, sum.DistinctBodyKeys)

	bodies := []bodyRow{}
	store, err := js.ObjectStore(ctx, opts.contentBucket)
	switch {
	case err == nil:
		sum.ContentBucketPresent = true
	case errors.Is(err, jetstream.ErrBucketNotFound):
		sum.ContentBucketPresent = false
	default:
		return fmt.Errorf("open object store %s: %w", opts.contentBucket, err)
	}
	bodyKeys := make([]string, 0, len(handles))
	for k := range handles {
		bodyKeys = append(bodyKeys, k)
	}
	sort.Strings(bodyKeys)
	for _, bodyKey := range bodyKeys {
		entities := handles[bodyKey]
		sort.Strings(entities)
		row := bodyRow{Store: handleStore[bodyKey], Key: bodyKey, Entities: entities}
		if !sum.ContentBucketPresent {
			sum.BodiesMissing = append(sum.BodiesMissing, entities...)
			continue
		}
		data, ok, err := getObject(ctx, store, bodyKey, opts.getTimeout)
		if err != nil {
			return err
		}
		if !ok {
			sum.BodiesMissing = append(sum.BodiesMissing, entities...)
			continue
		}
		row.Bytes, row.SHA256 = len(data), sha256Hex(data)
		if len(data) > opts.maxBodyBytes {
			sum.BodiesOversize = append(sum.BodiesOversize, entities...)
			bodies = append(bodies, row)
			continue
		}
		row.ValidUTF8 = utf8.Valid(data)
		if row.ValidUTF8 {
			row.Text = string(data)
		} else {
			row.Base64 = base64.StdEncoding.EncodeToString(data)
			sum.InvalidUTF8++
		}
		sum.BodiesFetched++
		sum.BodyBytes += len(data)
		bodies = append(bodies, row)
	}
	sort.Strings(sum.BodiesMissing)
	sort.Strings(sum.BodiesOversize)

	storesOK := true
	for s := range sum.HandlesByStore {
		if s != bodyStoreInstance {
			storesOK = false
		}
	}
	sum.Checks = map[string]bool{
		"all_states_parse":          len(sum.MalformedStates) == 0,
		"all_handles_resolved":      len(sum.BodiesMissing) == 0 && len(sum.BodiesOversize) == 0,
		"handles_name_object_store": storesOK,
	}
	sum.FinishedUTC = time.Now().UTC().Format(time.RFC3339)

	if err := os.MkdirAll(opts.output, 0o755); err != nil {
		return err
	}
	if err := writeJSONL(filepath.Join(opts.output, "entity_states.jsonl"), len(states), func(i int) any { return states[i] }); err != nil {
		return err
	}
	if err := writeJSONL(filepath.Join(opts.output, "bodies.jsonl"), len(bodies), func(i int) any { return bodies[i] }); err != nil {
		return err
	}
	if err := writeJSON(filepath.Join(opts.output, "hydration.json"), sum); err != nil {
		return err
	}
	fmt.Fprintf(log, "hydrate: %d bodies fetched (%d bytes), %d missing, %d oversize; checks %v\n",
		sum.BodiesFetched, sum.BodyBytes, len(sum.BodiesMissing), len(sum.BodiesOversize), sum.Checks)
	for _, ok := range sum.Checks {
		if !ok {
			return errChecksFailed
		}
	}
	return nil
}

// entityState is the subset of graph.EntityState (SemStreams graph/types.go)
// the hydration reads: the ID and the triples' predicate/object pairs.
type entityState struct {
	ID      string   `json:"id"`
	Triples []triple `json:"triples"`
}

type triple struct {
	Predicate string `json:"predicate"`
	Object    any    `json:"object"`
}

func parseState(value []byte) (entityState, error) {
	var s entityState
	if err := json.Unmarshal(value, &s); err != nil {
		return s, err
	}
	if s.ID == "" || s.Triples == nil {
		return s, errors.New("entity state without id or triples")
	}
	return s, nil
}

// bodyHandle returns the (store, key, key predicate) of the first complete
// body handle among the triples; both triples must be strings.
func bodyHandle(triples []triple) (store, key, predicate string) {
	values := map[string]string{}
	for _, t := range triples {
		if s, ok := t.Object.(string); ok {
			if _, seen := values[t.Predicate]; !seen {
				values[t.Predicate] = s
			}
		}
	}
	for _, h := range bodyHandles {
		if values[h[0]] != "" && values[h[1]] != "" {
			return values[h[0]], values[h[1]], h[1]
		}
	}
	return "", "", ""
}

func sha256Hex(b []byte) string {
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}

func listKeys(ctx context.Context, kv jetstream.KeyValue, maxKeys int, timeout time.Duration) ([]string, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	lister, err := kv.ListKeys(ctx)
	if err != nil {
		if errors.Is(err, jetstream.ErrNoKeysFound) {
			return nil, nil
		}
		return nil, fmt.Errorf("list %s: %w", bucketEntityStates, err)
	}
	defer func() { _ = lister.Stop() }()
	seen := map[string]bool{}
	keys := []string{}
	for {
		select {
		case <-ctx.Done():
			return nil, fmt.Errorf("list %s: %w", bucketEntityStates, ctx.Err())
		case key, ok := <-lister.Keys():
			if !ok {
				if err := ctx.Err(); err != nil {
					return nil, fmt.Errorf("list %s: %w", bucketEntityStates, err)
				}
				return keys, nil
			}
			if seen[key] {
				continue
			}
			if len(keys) >= maxKeys {
				return nil, fmt.Errorf("%s holds more than %d keys", bucketEntityStates, maxKeys)
			}
			seen[key] = true
			keys = append(keys, key)
		}
	}
}

func getValue(ctx context.Context, kv jetstream.KeyValue, key string, maxBytes int, timeout time.Duration) ([]byte, bool, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	entry, err := kv.Get(ctx, key)
	if err != nil {
		if errors.Is(err, jetstream.ErrKeyNotFound) {
			return nil, false, nil
		}
		return nil, false, fmt.Errorf("get %s %s: %w", bucketEntityStates, key, err)
	}
	if len(entry.Value()) > maxBytes {
		return nil, false, fmt.Errorf("value of %s is %d bytes, over the %d byte bound", key, len(entry.Value()), maxBytes)
	}
	return entry.Value(), true, nil
}

func getObject(ctx context.Context, store jetstream.ObjectStore, key string, timeout time.Duration) ([]byte, bool, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	data, err := store.GetBytes(ctx, key)
	if err != nil {
		if errors.Is(err, jetstream.ErrObjectNotFound) {
			return nil, false, nil
		}
		return nil, false, fmt.Errorf("get object %s: %w", key, err)
	}
	return data, true, nil
}

func writeJSONL(path string, n int, row func(int) any) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	w := bufio.NewWriter(f)
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	for i := 0; i < n; i++ {
		if err := enc.Encode(row(i)); err != nil {
			f.Close()
			return err
		}
	}
	if err := w.Flush(); err != nil {
		f.Close()
		return err
	}
	return f.Close()
}

func writeJSON(path string, v any) error {
	data, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(data, '\n'), 0o644)
}
