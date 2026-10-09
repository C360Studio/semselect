// Command legacy-count lists ENTITY_STATES and OUTGOING_INDEX keys from a
// legacy SemSource/SemStreams NATS KV store and reports entity counts per
// system and type plus an explicit-edge proxy per system. It reads keys and
// outgoing-index values only; it never writes to the store.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

// outgoingEntry mirrors the OUTGOING_INDEX value element in semstreams
// v1.0.0-beta.160 (processor/graph-clustering relationshipEntry).
type outgoingEntry struct {
	Predicate  string `json:"predicate"`
	ToEntityID string `json:"to_entity_id"`
}

type edgeProxy struct {
	SourceKeys        int            `json:"source_keys"`
	Edges             int            `json:"edges"`
	InducedEdges      int            `json:"induced_edges"`
	CrossSystemEdges  int            `json:"cross_system_edges"`
	DanglingEdges     int            `json:"dangling_edges"`
	UndecodableValues int            `json:"undecodable_values"`
	Predicates        map[string]int `json:"predicates"`
}

type systemCounts struct {
	Entities int            `json:"entities"`
	ByType   map[string]int `json:"by_type"`
	ByDomain map[string]int `json:"by_domain"`
	Edges    *edgeProxy     `json:"edge_proxy,omitempty"`
}

type report struct {
	NATSURL          string                   `json:"nats_url"`
	CollectedAt      string                   `json:"collected_at"`
	EntityKeys       int                      `json:"entity_keys"`
	MalformedKeys    []string                 `json:"malformed_keys"`
	Systems          map[string]*systemCounts `json:"systems"`
	OutgoingIndexErr string                   `json:"outgoing_index_error,omitempty"`
}

func main() {
	url := flag.String("nats", "nats://localhost:14222", "NATS URL of the capture stack")
	out := flag.String("out", "", "output JSON path (default stdout)")
	timeout := flag.Duration("timeout", 3*time.Minute, "overall deadline")
	flag.Parse()

	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()

	rep, err := collect(ctx, *url)
	if err != nil {
		fmt.Fprintln(os.Stderr, "legacy-count:", err)
		os.Exit(1)
	}
	data, err := json.MarshalIndent(rep, "", "  ")
	if err != nil {
		fmt.Fprintln(os.Stderr, "legacy-count:", err)
		os.Exit(1)
	}
	data = append(data, '\n')
	if *out == "" {
		_, err = os.Stdout.Write(data)
	} else {
		err = os.WriteFile(*out, data, 0o644)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "legacy-count:", err)
		os.Exit(1)
	}
}

func collect(ctx context.Context, url string) (*report, error) {
	nc, err := nats.Connect(url, nats.Timeout(10*time.Second))
	if err != nil {
		return nil, fmt.Errorf("connect %s: %w", url, err)
	}
	defer nc.Close()
	js, err := jetstream.New(nc)
	if err != nil {
		return nil, fmt.Errorf("jetstream: %w", err)
	}

	entities, err := js.KeyValue(ctx, "ENTITY_STATES")
	if err != nil {
		return nil, fmt.Errorf("open ENTITY_STATES: %w", err)
	}
	keys, err := listKeys(ctx, entities)
	if err != nil {
		return nil, fmt.Errorf("list ENTITY_STATES: %w", err)
	}

	rep := &report{
		NATSURL:       url,
		CollectedAt:   time.Now().UTC().Format(time.RFC3339),
		EntityKeys:    len(keys),
		MalformedKeys: []string{},
		Systems:       map[string]*systemCounts{},
	}
	known := make(map[string]string, len(keys)) // entity ID -> system
	for _, k := range keys {
		parts := strings.Split(k, ".")
		if len(parts) != 6 {
			rep.MalformedKeys = append(rep.MalformedKeys, k)
			continue
		}
		sys := system(rep, parts[3])
		sys.Entities++
		sys.ByType[parts[4]]++
		sys.ByDomain[parts[2]]++
		known[k] = parts[3]
	}

	// The edge proxy is best effort: a missing bucket is reported, not fatal.
	if err := countOutgoing(ctx, js, rep, known); err != nil {
		rep.OutgoingIndexErr = err.Error()
	}
	return rep, nil
}

func system(rep *report, name string) *systemCounts {
	s, ok := rep.Systems[name]
	if !ok {
		s = &systemCounts{ByType: map[string]int{}, ByDomain: map[string]int{}}
		rep.Systems[name] = s
	}
	return s
}

func countOutgoing(ctx context.Context, js jetstream.JetStream, rep *report, known map[string]string) error {
	kv, err := js.KeyValue(ctx, "OUTGOING_INDEX")
	if err != nil {
		return fmt.Errorf("open OUTGOING_INDEX: %w", err)
	}
	keys, err := listKeys(ctx, kv)
	if err != nil {
		return fmt.Errorf("list OUTGOING_INDEX: %w", err)
	}
	for _, k := range keys {
		parts := strings.Split(k, ".")
		if len(parts) != 6 {
			continue
		}
		sys := system(rep, parts[3])
		if sys.Edges == nil {
			sys.Edges = &edgeProxy{Predicates: map[string]int{}}
		}
		sys.Edges.SourceKeys++
		entry, err := kv.Get(ctx, k)
		if err != nil {
			if errors.Is(err, jetstream.ErrKeyNotFound) {
				continue
			}
			return fmt.Errorf("get OUTGOING_INDEX %s: %w", k, err)
		}
		var rels []outgoingEntry
		if err := json.Unmarshal(entry.Value(), &rels); err != nil {
			sys.Edges.UndecodableValues++
			continue
		}
		for _, r := range rels {
			sys.Edges.Edges++
			sys.Edges.Predicates[r.Predicate]++
			target, ok := known[r.ToEntityID]
			switch {
			case !ok:
				sys.Edges.DanglingEdges++
			case target == parts[3]:
				sys.Edges.InducedEdges++
			default:
				sys.Edges.CrossSystemEdges++
			}
		}
	}
	return nil
}

func listKeys(ctx context.Context, kv jetstream.KeyValue) ([]string, error) {
	lister, err := kv.ListKeys(ctx)
	if err != nil {
		return nil, err
	}
	defer func() { _ = lister.Stop() }()
	var keys []string
	for k := range lister.Keys() {
		keys = append(keys, k)
	}
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	sort.Strings(keys)
	return keys, nil
}
