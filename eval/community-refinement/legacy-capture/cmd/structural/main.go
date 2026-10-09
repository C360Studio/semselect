// Command structural freezes the structural side of one legacy tier-1 family
// capture from a live SemSource/SemStreams stack, read-only: the ENTITY_STATES
// population, the explicit topology (OUTGOING_INDEX, checked against
// INCOMING_INDEX), the identity virtual edges and effective weights the legacy
// EntityIDProvider synthesizes for the LPA vote, and the structural-only
// partition graph-clustering wrote to COMMUNITY_INDEX. It waits until that
// partition covers every entity and is unchanged across one settle interval,
// so the frozen partition is one the legacy detector computed over the whole
// graph. Legacy SemStreams capture; not a SemEngine result.
package main

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

const (
	provenance = "legacy SemStreams capture; not a SemEngine result"

	bucketEntityStates   = "ENTITY_STATES"
	bucketOutgoingIndex  = "OUTGOING_INDEX"
	bucketIncomingIndex  = "INCOMING_INDEX"
	bucketEmbeddingIndex = "EMBEDDING_INDEX"
	bucketCommunityIndex = "COMMUNITY_INDEX"

	// Value bounds, so one oversized row cannot exhaust the tool.
	maxOutgoingValueBytes  = 8 << 20
	maxCommunityValueBytes = 8 << 20
	maxExamples            = 20
)

var errChecksFailed = errors.New("structural capture checks failed")

type options struct {
	natsURL       string
	output        string
	listTimeout   time.Duration
	maxKeys       int
	poll          time.Duration
	settle        time.Duration
	settleTimeout time.Duration
	identity      identityParams
	runMetadata   string
}

func main() {
	opts, err := parseFlags(os.Args[1:], os.Stderr)
	if err != nil {
		if errors.Is(err, flag.ErrHelp) {
			os.Exit(2)
		}
		fmt.Fprintln(os.Stderr, "structural:", err)
		os.Exit(2)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := run(ctx, opts, os.Stderr); err != nil {
		fmt.Fprintln(os.Stderr, "structural:", err)
		os.Exit(1)
	}
}

func parseFlags(args []string, stderr io.Writer) (options, error) {
	var o options
	fs := flag.NewFlagSet("structural", flag.ContinueOnError)
	fs.SetOutput(stderr)
	fs.StringVar(&o.natsURL, "nats", "nats://localhost:4222", "NATS URL of the legacy stack")
	fs.StringVar(&o.output, "output", "", "output directory; must not exist, its parent must")
	fs.DurationVar(&o.listTimeout, "list-timeout", 120*time.Second, "deadline for opening and listing one KV bucket")
	fs.IntVar(&o.maxKeys, "max-keys", 500000, "abort if a bucket holds more keys than this")
	fs.DurationVar(&o.poll, "poll", 5*time.Second, "re-read interval while COMMUNITY_INDEX does not yet cover ENTITY_STATES")
	fs.DurationVar(&o.settle, "settle", 35*time.Second,
		"the partition must be unchanged across this interval (longer than the legacy 30 s detection interval)")
	fs.DurationVar(&o.settleTimeout, "settle-timeout", 10*time.Minute, "give up waiting for a settled partition after this")
	fs.BoolVar(&o.identity.IncludeSiblings, "include-siblings", true, "synthesize sibling edges (legacy default true)")
	fs.BoolVar(&o.identity.IncludeSystemPeers, "include-system-peers", true, "synthesize system-peer edges (legacy default true)")
	fs.Float64Var(&o.identity.SiblingWeight, "sibling-weight", 0.7, "sibling edge weight")
	fs.IntVar(&o.identity.MaxSiblings, "max-siblings", 5, "sibling cap per entity (semantic-profile baseline 5; structural default 10)")
	fs.Float64Var(&o.identity.SystemPeerWeight, "system-peer-weight", 0.2, "system-peer edge weight (semantic-profile baseline 0.2; structural default 0.3)")
	fs.IntVar(&o.identity.MaxSystemPeers, "max-system-peers", 8, "system-peer cap per entity (semantic-profile baseline 8; structural default 15)")
	fs.StringVar(&o.runMetadata, "run-metadata", "", "optional JSON file embedded verbatim under structural.run")
	if err := fs.Parse(args); err != nil {
		return o, err
	}
	if fs.NArg() != 0 {
		return o, fmt.Errorf("unexpected arguments: %v", fs.Args())
	}
	if o.output == "" {
		return o, errors.New("-output is required")
	}
	if o.settle <= 0 || o.poll <= 0 || o.settleTimeout <= 0 || o.listTimeout <= 0 {
		return o, errors.New("-settle, -poll, -settle-timeout and -list-timeout must be positive")
	}
	if o.maxKeys <= 0 {
		return o, errors.New("-max-keys must be positive")
	}
	if o.identity.SiblingWeight <= 0 || o.identity.SystemPeerWeight <= 0 || o.identity.MaxSiblings <= 0 || o.identity.MaxSystemPeers <= 0 {
		// The legacy provider replaces zero values with its own defaults; refuse
		// rather than silently capture a different profile.
		return o, errors.New("identity weights and caps must be positive")
	}
	return o, nil
}

// --- Records written to the evidence directory.

type entityRecord struct {
	ID               string            `json:"entity_id"`
	Valid            bool              `json:"valid_six_part_id"`
	Domain           string            `json:"domain,omitempty"`
	System           string            `json:"system,omitempty"`
	Type             string            `json:"type,omitempty"`
	Embedded         bool              `json:"embedded"`
	ExplicitOut      int               `json:"explicit_out"`
	ExplicitIn       int               `json:"explicit_in"`
	ExplicitBoth     int               `json:"explicit_both"`
	SiblingsListed   int               `json:"siblings_listed"`
	PeersListed      int               `json:"system_peers_listed"`
	CommunityByLevel map[string]string `json:"community_by_level"`
}

type explicitEdge struct {
	From                string `json:"from"`
	To                  string `json:"to"`
	Predicate           string `json:"predicate"`
	FromInEntityStates  bool   `json:"from_in_entity_states"`
	ToInEntityStates    bool   `json:"to_in_entity_states"`
	InIncomingIndex     bool   `json:"in_incoming_index"`
	SameCommunityLevel0 *bool  `json:"same_community_level0"`
}

type votingEdge struct {
	From                string  `json:"from"`
	To                  string  `json:"to"`
	ListedAs            string  `json:"listed_as"`
	WeightTier          string  `json:"weight_tier"`
	Weight              float64 `json:"weight"`
	ToInEntityStates    bool    `json:"to_in_entity_states"`
	SameCommunityLevel0 *bool   `json:"same_community_level0"`
}

type community struct {
	Key     string          `json:"key"`
	Level   int             `json:"level"`
	ID      string          `json:"id"`
	Members []string        `json:"members"`
	Value   json.RawMessage `json:"value"`
}

type levelReport struct {
	Level         int            `json:"level"`
	Communities   int            `json:"communities"`
	Members       int            `json:"members"`
	EntityMapped  int            `json:"entity_mapped"`
	Singletons    int            `json:"singletons"`
	Largest       int            `json:"largest"`
	SizeHistogram map[string]int `json:"size_histogram"`
}

type partition struct {
	Communities   []community                  `json:"communities"`
	EntityMap     map[int]map[string]string    `json:"-"`
	EntityMapJSON map[string]map[string]string `json:"entity_to_community"`
	Levels        []levelReport                `json:"levels"`
	MalformedKeys []string                     `json:"malformed_keys"`
	Covered       bool                         `json:"level0_covers_entity_states"`
	Disjoint      bool                         `json:"level0_members_disjoint"`
	MapConsistent bool                         `json:"level0_entity_map_consistent"`
	CoverNote     string                       `json:"cover_note,omitempty"`
	Hash          string                       `json:"hash"`
}

type settleRead struct {
	UTC               string `json:"utc"`
	Entities          int    `json:"entities"`
	CommunitiesLevel0 int    `json:"communities_level0"`
	Covered           bool   `json:"covered"`
	Hash              string `json:"hash"`
	RawValueHash      string `json:"raw_value_hash"`
	Note              string `json:"note,omitempty"`
}

type summary struct {
	Provenance  string `json:"provenance"`
	NATSURL     string `json:"nats_url"`
	StartedUTC  string `json:"started_utc"`
	FinishedUTC string `json:"finished_utc"`
	Parameters  struct {
		Identity      identityParams `json:"identity"`
		SettleSeconds float64        `json:"settle_seconds"`
		PollSeconds   float64        `json:"poll_seconds"`
		MaxKeys       int            `json:"max_keys"`
	} `json:"parameters"`
	Entities struct {
		Total        int            `json:"total"`
		ValidIDs     int            `json:"valid_six_part_ids"`
		MalformedIDs []string       `json:"malformed_ids"`
		Embedded     int            `json:"embedded"`
		BySystem     map[string]int `json:"by_system"`
		ByType       map[string]int `json:"by_type"`
		ByDomain     map[string]int `json:"by_domain"`
	} `json:"entities"`
	Explicit struct {
		OutgoingRows                 int            `json:"outgoing_rows"`
		OutgoingRowsRejected         int            `json:"outgoing_rows_rejected"`
		OutgoingKeysNotInEntityState int            `json:"outgoing_keys_not_in_entity_states"`
		Edges                        int            `json:"edges"`
		EdgesToOutsideEntityStates   int            `json:"edges_to_outside_entity_states"`
		Predicates                   map[string]int `json:"predicates"`
		IncomingKeys                 int            `json:"incoming_keys"`
		IncomingKeysMalformed        int            `json:"incoming_keys_malformed"`
		OutgoingOnly                 int            `json:"outgoing_only"`
		IncomingOnly                 int            `json:"incoming_only"`
		OutgoingOnlyExamples         []string       `json:"outgoing_only_examples"`
		IncomingOnlyExamples         []string       `json:"incoming_only_examples"`
		IndexesConsistent            bool           `json:"indexes_consistent"`
		UndirectedPairs              int            `json:"undirected_pairs"`
		UndirectedPairsCrossLevel0   int            `json:"undirected_pairs_cross_level0"`
	} `json:"explicit"`
	Voting struct {
		Edges                   int                `json:"edges"`
		ByTier                  map[string]int     `json:"by_tier"`
		ByListedAs              map[string]int     `json:"by_listed_as"`
		WeightMassByTier        map[string]float64 `json:"weight_mass_by_tier"`
		EdgesToOutside          int                `json:"edges_to_outside_entity_states"`
		CrossLevel0ByTier       map[string]int     `json:"cross_level0_by_tier"`
		EntitiesSiblingCapped   int                `json:"entities_sibling_capped"`
		EntitiesPeerCapped      int                `json:"entities_system_peer_capped"`
		EntitiesWithoutNeighbor int                `json:"entities_without_neighbors"`
	} `json:"voting"`
	Partition struct {
		Levels        []levelReport `json:"levels"`
		Covered       bool          `json:"level0_covers_entity_states"`
		Disjoint      bool          `json:"level0_members_disjoint"`
		MapConsistent bool          `json:"level0_entity_map_consistent"`
		MalformedKeys int           `json:"malformed_keys"`
		Hash          string        `json:"hash"`
	} `json:"partition"`
	Settle struct {
		Reads           []settleRead `json:"reads"`
		Settled         bool         `json:"settled"`
		SettleSeconds   float64      `json:"settled_over_seconds"`
		WaitedSeconds   float64      `json:"waited_seconds"`
		StableAfterRead bool         `json:"stable_after_topology_read"`
		// ValuesByteStable is false when the stored community values changed
		// between the two settling reads although memberships and back-pointers
		// did not (the legacy statistical summary's keyword order is not
		// deterministic across cycles). Reported, never gating.
		ValuesByteStable bool `json:"community_values_byte_stable"`
	} `json:"settle"`
	Checks map[string]bool `json:"checks"`
	Run    json.RawMessage `json:"run"`
}

// --- Capture.

type buckets struct {
	entities, outgoing, incoming, embedding, community jetstream.KeyValue
}

func run(ctx context.Context, opts options, stderr io.Writer) (err error) {
	if err := os.Mkdir(opts.output, 0o755); err != nil {
		return fmt.Errorf("create -output: %w", err)
	}
	wrote := false
	defer func() {
		if !wrote {
			_ = os.RemoveAll(opts.output)
		}
	}()
	var runMeta json.RawMessage
	if opts.runMetadata != "" {
		data, err := os.ReadFile(opts.runMetadata)
		if err != nil {
			return fmt.Errorf("read -run-metadata: %w", err)
		}
		if !json.Valid(data) {
			return errors.New("-run-metadata is not valid JSON")
		}
		runMeta = json.RawMessage(data)
	}

	started := time.Now().UTC()
	nc, err := nats.Connect(opts.natsURL, nats.Timeout(10*time.Second), nats.MaxReconnects(-1))
	if err != nil {
		return fmt.Errorf("connect %s: %w", opts.natsURL, err)
	}
	defer nc.Close()
	js, err := jetstream.New(nc)
	if err != nil {
		return fmt.Errorf("jetstream: %w", err)
	}
	var b buckets
	for _, open := range []struct {
		name string
		dst  *jetstream.KeyValue
	}{
		{bucketEntityStates, &b.entities}, {bucketOutgoingIndex, &b.outgoing}, {bucketIncomingIndex, &b.incoming},
		{bucketEmbeddingIndex, &b.embedding}, {bucketCommunityIndex, &b.community},
	} {
		kv, err := openBucket(ctx, js, open.name, opts.listTimeout)
		if err != nil {
			return err
		}
		*open.dst = kv
	}

	// Phase 1: wait for a partition that covers the whole graph and is unchanged
	// across one settle interval.
	var sum summary
	sum.Provenance = provenance
	sum.NATSURL = opts.natsURL
	sum.StartedUTC = started.Format(time.RFC3339)
	sum.Parameters.Identity = opts.identity
	sum.Parameters.SettleSeconds = opts.settle.Seconds()
	sum.Parameters.PollSeconds = opts.poll.Seconds()
	sum.Parameters.MaxKeys = opts.maxKeys
	sum.Run = runMeta

	deadline := time.Now().Add(opts.settleTimeout)
	var prev, settled *snapshot
	var prevAt time.Time
	for {
		snap, err := readSnapshot(ctx, b, opts)
		if err != nil {
			return err
		}
		at := time.Now()
		read := snap.read()
		if prev != nil && prev.part.Covered && snap.part.Covered && prev.hash == snap.hash && at.Sub(prevAt) >= opts.settle {
			read.Note = fmt.Sprintf("memberships unchanged since the read %.1fs earlier", at.Sub(prevAt).Seconds())
			sum.Settle.ValuesByteStable = prev.rawHash == snap.rawHash
			if !sum.Settle.ValuesByteStable {
				read.Note += "; stored community values differ byte-wise (summary decoration), memberships do not"
			}
			sum.Settle.Reads = append(sum.Settle.Reads, read)
			sum.Settle.Settled = true
			sum.Settle.SettleSeconds = at.Sub(prevAt).Seconds()
			settled = snap
			break
		}
		sum.Settle.Reads = append(sum.Settle.Reads, read)
		fmt.Fprintf(stderr, "structural: %s entities=%d communities_level0=%d covered=%v\n",
			read.UTC, read.Entities, read.CommunitiesLevel0, read.Covered)
		prev, prevAt = snap, at
		if at.After(deadline) {
			// Not settled: the last read is frozen as-is and the checks say so.
			settled = snap
			break
		}
		wait := opts.poll
		if snap.part.Covered {
			wait = opts.settle
		}
		if err := sleep(ctx, wait); err != nil {
			return err
		}
	}
	sum.Settle.WaitedSeconds = time.Since(started).Seconds()

	// Phase 2: explicit topology and embedding identity, then confirm the graph
	// did not move underneath those reads.
	outgoingKeys, err := listKeys(ctx, b.outgoing, opts.maxKeys, opts.listTimeout)
	if err != nil {
		return err
	}
	outgoingRows := make(map[string][]byte, len(outgoingKeys))
	for _, k := range outgoingKeys {
		v, ok, err := getValue(ctx, b.outgoing, k, maxOutgoingValueBytes, opts.listTimeout)
		if err != nil {
			return fmt.Errorf("get %s %s: %w", bucketOutgoingIndex, k, err)
		}
		if ok {
			outgoingRows[k] = v
		}
	}
	incomingKeys, err := listKeys(ctx, b.incoming, opts.maxKeys, opts.listTimeout)
	if err != nil {
		return err
	}
	embeddedKeys, err := listKeys(ctx, b.embedding, opts.maxKeys, opts.listTimeout)
	if err != nil {
		return err
	}
	after, err := readSnapshot(ctx, b, opts)
	if err != nil {
		return err
	}
	sum.Settle.StableAfterRead = after.hash == settled.hash
	if !sum.Settle.StableAfterRead {
		sum.Settle.Reads = append(sum.Settle.Reads, after.read())
	}

	// Phase 3: derive the records.
	entitySet := make(map[string]bool, len(settled.entities))
	for _, id := range settled.entities {
		entitySet[id] = true
	}
	embedded := make(map[string]bool, len(embeddedKeys))
	for _, k := range embeddedKeys {
		embedded[k] = true
	}
	level0 := settled.part.EntityMap[0]
	sameLevel0 := func(a, b string) *bool {
		ca, oka := level0[a]
		cb, okb := level0[b]
		if !oka || !okb {
			return nil
		}
		v := ca == cb
		return &v
	}

	// Explicit edges from OUTGOING_INDEX, as getNeighborsFromBucket reads them: a
	// row with an entry lacking to_entity_id is rejected whole.
	type edgeKey struct{ from, to, predicate string }
	outgoingEdges := map[edgeKey]bool{}
	outNeighbors := map[string]map[string]bool{}
	var explicitEdges []explicitEdge
	sum.Explicit.Predicates = map[string]int{}
	sum.Explicit.OutgoingRows = len(outgoingRows)
	for _, from := range outgoingKeys {
		raw, ok := outgoingRows[from]
		if !ok {
			continue
		}
		if !entitySet[from] {
			sum.Explicit.OutgoingKeysNotInEntityState++
		}
		var entries []struct {
			Predicate  string `json:"predicate"`
			ToEntityID string `json:"to_entity_id"`
		}
		if err := json.Unmarshal(raw, &entries); err != nil {
			sum.Explicit.OutgoingRowsRejected++
			continue
		}
		rejected := false
		for _, e := range entries {
			if e.ToEntityID == "" {
				rejected = true
				break
			}
		}
		if rejected {
			sum.Explicit.OutgoingRowsRejected++
			continue
		}
		for _, e := range entries {
			k := edgeKey{from, e.ToEntityID, e.Predicate}
			if outgoingEdges[k] {
				continue
			}
			outgoingEdges[k] = true
			if outNeighbors[from] == nil {
				outNeighbors[from] = map[string]bool{}
			}
			outNeighbors[from][e.ToEntityID] = true
			sum.Explicit.Predicates[e.Predicate]++
		}
	}
	// INCOMING_INDEX keys are target.source.hex(predicate): thirteen dot tokens.
	incomingEdges := map[edgeKey]bool{}
	inNeighbors := map[string]map[string]bool{}
	sum.Explicit.IncomingKeys = len(incomingKeys)
	for _, k := range incomingKeys {
		target, source, predicate, ok := parseIncomingKey(k)
		if !ok {
			sum.Explicit.IncomingKeysMalformed++
			continue
		}
		incomingEdges[edgeKey{source, target, predicate}] = true
		if inNeighbors[target] == nil {
			inNeighbors[target] = map[string]bool{}
		}
		inNeighbors[target][source] = true
	}
	sum.Explicit.OutgoingOnlyExamples, sum.Explicit.IncomingOnlyExamples = []string{}, []string{}
	outKeys := make([]edgeKey, 0, len(outgoingEdges))
	for k := range outgoingEdges {
		outKeys = append(outKeys, k)
	}
	sort.Slice(outKeys, func(i, j int) bool {
		a, b := outKeys[i], outKeys[j]
		return a.from < b.from || a.from == b.from && (a.to < b.to || a.to == b.to && a.predicate < b.predicate)
	})
	for _, k := range outKeys {
		inIncoming := incomingEdges[k]
		if !inIncoming {
			sum.Explicit.OutgoingOnly++
			if len(sum.Explicit.OutgoingOnlyExamples) < maxExamples {
				sum.Explicit.OutgoingOnlyExamples = append(sum.Explicit.OutgoingOnlyExamples, k.from+" -["+k.predicate+"]-> "+k.to)
			}
		}
		if !entitySet[k.to] {
			sum.Explicit.EdgesToOutsideEntityStates++
		}
		explicitEdges = append(explicitEdges, explicitEdge{
			From: k.from, To: k.to, Predicate: k.predicate,
			FromInEntityStates: entitySet[k.from], ToInEntityStates: entitySet[k.to],
			InIncomingIndex: inIncoming, SameCommunityLevel0: sameLevel0(k.from, k.to),
		})
	}
	sum.Explicit.Edges = len(explicitEdges)
	inKeys := make([]edgeKey, 0, len(incomingEdges))
	for k := range incomingEdges {
		if !outgoingEdges[k] {
			inKeys = append(inKeys, k)
		}
	}
	sort.Slice(inKeys, func(i, j int) bool {
		a, b := inKeys[i], inKeys[j]
		return a.from < b.from || a.from == b.from && (a.to < b.to || a.to == b.to && a.predicate < b.predicate)
	})
	sum.Explicit.IncomingOnly = len(inKeys)
	for i, k := range inKeys {
		if i >= maxExamples {
			break
		}
		sum.Explicit.IncomingOnlyExamples = append(sum.Explicit.IncomingOnlyExamples, k.from+" -["+k.predicate+"]-> "+k.to)
	}
	sum.Explicit.IndexesConsistent = sum.Explicit.OutgoingOnly == 0 && sum.Explicit.IncomingOnly == 0 && sum.Explicit.IncomingKeysMalformed == 0

	// bothNeighborSet(A) = outgoing targets of A ∪ incoming sources of A, exactly
	// what the legacy kvProvider serves the LPA loop.
	both := func(id string) map[string]bool {
		set := map[string]bool{}
		for n := range outNeighbors[id] {
			set[n] = true
		}
		for n := range inNeighbors[id] {
			set[n] = true
		}
		return set
	}
	undirected := map[[2]string]bool{}
	for k := range outgoingEdges {
		a, b := k.from, k.to
		if a > b {
			a, b = b, a
		}
		undirected[[2]string{a, b}] = true
	}
	for k := range incomingEdges {
		a, b := k.from, k.to
		if a > b {
			a, b = b, a
		}
		undirected[[2]string{a, b}] = true
	}
	sum.Explicit.UndirectedPairs = len(undirected)
	for p := range undirected {
		if s := sameLevel0(p[0], p[1]); s != nil && !*s {
			sum.Explicit.UndirectedPairsCrossLevel0++
		}
	}

	// Identity synthesis and the voting graph, per voter in ENTITY_STATES.
	idx := buildIDIndex(settled.entities)
	sum.Voting.ByTier = map[string]int{tierExplicit: 0, tierSibling: 0, tierSystemPeer: 0}
	sum.Voting.ByListedAs = map[string]int{tierExplicit: 0, tierSibling: 0, tierSystemPeer: 0}
	sum.Voting.WeightMassByTier = map[string]float64{tierExplicit: 0, tierSibling: 0, tierSystemPeer: 0}
	sum.Voting.CrossLevel0ByTier = map[string]int{tierExplicit: 0, tierSibling: 0, tierSystemPeer: 0}
	sum.Entities.BySystem, sum.Entities.ByType, sum.Entities.ByDomain = map[string]int{}, map[string]int{}, map[string]int{}
	sum.Entities.MalformedIDs = []string{}
	var entities []entityRecord
	var voting []votingEdge
	for _, id := range settled.entities {
		rec := entityRecord{ID: id, Embedded: embedded[id], CommunityByLevel: map[string]string{}}
		parts := strings.Split(id, ".")
		if len(parts) == entityIDParts {
			rec.Valid = true
			rec.Domain, rec.System, rec.Type = parts[2], parts[3], parts[4]
			sum.Entities.ValidIDs++
			sum.Entities.BySystem[rec.System]++
			sum.Entities.ByType[rec.Type]++
			sum.Entities.ByDomain[rec.Domain]++
		} else {
			sum.Entities.MalformedIDs = append(sum.Entities.MalformedIDs, id)
		}
		if embedded[id] {
			sum.Entities.Embedded++
		}
		for level, m := range settled.part.EntityMap {
			if c, ok := m[id]; ok {
				rec.CommunityByLevel[strconv.Itoa(level)] = c
			}
		}
		explicit := both(id)
		rec.ExplicitOut, rec.ExplicitIn, rec.ExplicitBoth = len(outNeighbors[id]), len(inNeighbors[id]), len(explicit)
		explicitList := make([]string, 0, len(explicit))
		for n := range explicit {
			explicitList = append(explicitList, n)
		}
		sort.Strings(explicitList)
		siblings, peers := listedNeighbors(id, explicit, idx, opts.identity)
		rec.SiblingsListed, rec.PeersListed = len(siblings), len(peers)
		if len(siblings) == opts.identity.MaxSiblings && opts.identity.IncludeSiblings {
			sum.Voting.EntitiesSiblingCapped++
		}
		if len(peers) == opts.identity.MaxSystemPeers && opts.identity.IncludeSystemPeers {
			sum.Voting.EntitiesPeerCapped++
		}
		if len(explicitList)+len(siblings)+len(peers) == 0 {
			sum.Voting.EntitiesWithoutNeighbor++
		}
		emit := func(list []string, listedAs string) {
			for _, n := range list {
				tier, w := edgeWeight(id, n, explicit, opts.identity)
				e := votingEdge{From: id, To: n, ListedAs: listedAs, WeightTier: tier, Weight: w,
					ToInEntityStates: entitySet[n], SameCommunityLevel0: sameLevel0(id, n)}
				voting = append(voting, e)
				sum.Voting.Edges++
				sum.Voting.ByTier[tier]++
				sum.Voting.ByListedAs[listedAs]++
				sum.Voting.WeightMassByTier[tier] += w
				if !e.ToInEntityStates {
					sum.Voting.EdgesToOutside++
				}
				if e.SameCommunityLevel0 != nil && !*e.SameCommunityLevel0 {
					sum.Voting.CrossLevel0ByTier[tier]++
				}
			}
		}
		emit(explicitList, tierExplicit)
		emit(siblings, tierSibling)
		emit(peers, tierSystemPeer)
		entities = append(entities, rec)
	}
	sum.Entities.Total = len(entities)

	sum.Partition.Levels = settled.part.Levels
	sum.Partition.Covered = settled.part.Covered
	sum.Partition.Disjoint = settled.part.Disjoint
	sum.Partition.MapConsistent = settled.part.MapConsistent
	sum.Partition.MalformedKeys = len(settled.part.MalformedKeys)
	sum.Partition.Hash = settled.hash
	sum.Checks = map[string]bool{
		"partition_settled":               sum.Settle.Settled,
		"partition_covers_entity_states":  settled.part.Covered,
		"partition_members_disjoint":      settled.part.Disjoint,
		"partition_entity_map_consistent": settled.part.MapConsistent,
		"stable_after_topology_read":      sum.Settle.StableAfterRead,
		"explicit_indexes_consistent":     sum.Explicit.IndexesConsistent,
		"no_malformed_entity_ids":         len(sum.Entities.MalformedIDs) == 0,
	}
	sum.FinishedUTC = time.Now().UTC().Format(time.RFC3339)

	if entities == nil {
		entities = []entityRecord{}
	}
	if explicitEdges == nil {
		explicitEdges = []explicitEdge{}
	}
	if voting == nil {
		voting = []votingEdge{}
	}
	for name, w := range map[string]func() error{
		"entities.jsonl":       func() error { return writeJSONL(filepath.Join(opts.output, "entities.jsonl"), entities) },
		"explicit_edges.jsonl": func() error { return writeJSONL(filepath.Join(opts.output, "explicit_edges.jsonl"), explicitEdges) },
		"voting_edges.jsonl":   func() error { return writeJSONL(filepath.Join(opts.output, "voting_edges.jsonl"), voting) },
		"partition.json":       func() error { return writeJSON(filepath.Join(opts.output, "partition.json"), settled.part) },
		"structural.json":      func() error { return writeJSON(filepath.Join(opts.output, "structural.json"), sum) },
	} {
		if err := w(); err != nil {
			return fmt.Errorf("write %s: %w", name, err)
		}
	}
	wrote = true

	// Gate on the partition being a settled whole-graph partition; index
	// disagreement and malformed IDs are reported, not fatal, because the
	// capture records what the legacy stack actually served.
	for _, c := range []string{"partition_settled", "partition_covers_entity_states", "partition_members_disjoint",
		"partition_entity_map_consistent", "stable_after_topology_read"} {
		if !sum.Checks[c] {
			return fmt.Errorf("%w: %s is false (outputs written to %s)", errChecksFailed, c, opts.output)
		}
	}
	return nil
}

// --- Snapshots of ENTITY_STATES and COMMUNITY_INDEX.

// snapshot is one read of ENTITY_STATES and COMMUNITY_INDEX. hash covers the
// entity set, every community's level, ID and members, and the entity
// back-pointers: the partition. rawHash covers the stored bytes, which also
// carry the statistical summary and keywords that the legacy summarizer does
// not reproduce byte-for-byte between cycles.
type snapshot struct {
	utc      string
	entities []string
	part     *partition
	hash     string
	rawHash  string
}

func (s *snapshot) read() settleRead {
	n := 0
	for _, l := range s.part.Levels {
		if l.Level == 0 {
			n = l.Communities
		}
	}
	return settleRead{UTC: s.utc, Entities: len(s.entities), CommunitiesLevel0: n, Covered: s.part.Covered,
		Hash: s.hash, RawValueHash: s.rawHash, Note: s.part.CoverNote}
}

func readSnapshot(ctx context.Context, b buckets, opts options) (*snapshot, error) {
	utc := time.Now().UTC().Format(time.RFC3339)
	entities, err := listKeys(ctx, b.entities, opts.maxKeys, opts.listTimeout)
	if err != nil {
		return nil, err
	}
	keys, err := listKeys(ctx, b.community, opts.maxKeys, opts.listTimeout)
	if err != nil {
		return nil, err
	}
	values := make(map[string][]byte, len(keys))
	for _, k := range keys {
		v, ok, err := getValue(ctx, b.community, k, maxCommunityValueBytes, opts.listTimeout)
		if err != nil {
			return nil, fmt.Errorf("get %s %s: %w", bucketCommunityIndex, k, err)
		}
		if ok {
			values[k] = v
		}
	}
	part := parsePartition(keys, values, entities)
	h, raw := sha256.New(), sha256.New()
	for _, id := range entities {
		h.Write([]byte(id))
		h.Write([]byte{'\n'})
	}
	h.Write([]byte("--\n"))
	for _, c := range part.Communities {
		fmt.Fprintf(h, "%d %s %s\n", c.Level, c.ID, strings.Join(c.Members, " "))
	}
	h.Write([]byte("--\n"))
	levels := make([]int, 0, len(part.EntityMap))
	for l := range part.EntityMap {
		levels = append(levels, l)
	}
	sort.Ints(levels)
	for _, l := range levels {
		ids := make([]string, 0, len(part.EntityMap[l]))
		for id := range part.EntityMap[l] {
			ids = append(ids, id)
		}
		sort.Strings(ids)
		for _, id := range ids {
			fmt.Fprintf(h, "%d %s %s\n", l, id, part.EntityMap[l][id])
		}
	}
	for _, k := range part.MalformedKeys {
		fmt.Fprintf(h, "malformed %s\n", k)
	}
	for _, k := range keys {
		raw.Write([]byte(k))
		raw.Write([]byte{'='})
		raw.Write(values[k])
		raw.Write([]byte{'\n'})
	}
	part.Hash = hex.EncodeToString(h.Sum(nil))
	return &snapshot{utc: utc, entities: entities, part: part, hash: part.Hash, rawHash: hex.EncodeToString(raw.Sum(nil))}, nil
}

// parsePartition decodes COMMUNITY_INDEX: "{level}.{community_id}" holds a
// community JSON value, "entity.{level}.{entity_id}" holds that entity's
// community ID (graph/clustering/storage.go).
func parsePartition(keys []string, values map[string][]byte, entities []string) *partition {
	p := &partition{Communities: []community{}, EntityMap: map[int]map[string]string{}, EntityMapJSON: map[string]map[string]string{},
		Levels: []levelReport{}, MalformedKeys: []string{}}
	for _, k := range keys {
		raw, ok := values[k]
		if !ok {
			continue
		}
		if rest, ok := strings.CutPrefix(k, "entity."); ok {
			lvl, id, ok := strings.Cut(rest, ".")
			level, err := strconv.Atoi(lvl)
			if !ok || err != nil || id == "" {
				p.MalformedKeys = append(p.MalformedKeys, k)
				continue
			}
			if p.EntityMap[level] == nil {
				p.EntityMap[level] = map[string]string{}
			}
			p.EntityMap[level][id] = string(raw)
			continue
		}
		lvl, id, ok := strings.Cut(k, ".")
		level, err := strconv.Atoi(lvl)
		if !ok || err != nil || id == "" {
			p.MalformedKeys = append(p.MalformedKeys, k)
			continue
		}
		var v struct {
			ID      string   `json:"id"`
			Level   int      `json:"level"`
			Members []string `json:"members"`
		}
		if err := json.Unmarshal(raw, &v); err != nil || v.ID != id || v.Level != level {
			p.MalformedKeys = append(p.MalformedKeys, k)
			continue
		}
		members := append([]string(nil), v.Members...)
		sort.Strings(members)
		p.Communities = append(p.Communities, community{Key: k, Level: level, ID: id, Members: members, Value: json.RawMessage(raw)})
	}
	sort.Slice(p.Communities, func(i, j int) bool {
		a, b := p.Communities[i], p.Communities[j]
		return a.Level < b.Level || a.Level == b.Level && a.ID < b.ID
	})
	for level, m := range p.EntityMap {
		p.EntityMapJSON[strconv.Itoa(level)] = m
	}

	levels := map[int]*levelReport{}
	memberSets := map[int]map[string]int{} // level -> member -> communities seen in
	for _, c := range p.Communities {
		r := levels[c.Level]
		if r == nil {
			r = &levelReport{Level: c.Level, SizeHistogram: map[string]int{}}
			levels[c.Level] = r
		}
		r.Communities++
		r.Members += len(c.Members)
		r.SizeHistogram[strconv.Itoa(len(c.Members))]++
		if len(c.Members) == 1 {
			r.Singletons++
		}
		if len(c.Members) > r.Largest {
			r.Largest = len(c.Members)
		}
		if memberSets[c.Level] == nil {
			memberSets[c.Level] = map[string]int{}
		}
		for _, m := range c.Members {
			memberSets[c.Level][m]++
		}
	}
	for level, r := range levels {
		r.EntityMapped = len(p.EntityMap[level])
	}
	for level, m := range p.EntityMap {
		if levels[level] == nil {
			levels[level] = &levelReport{Level: level, EntityMapped: len(m), SizeHistogram: map[string]int{}}
		}
	}
	lvls := make([]int, 0, len(levels))
	for l := range levels {
		lvls = append(lvls, l)
	}
	sort.Ints(lvls)
	for _, l := range lvls {
		p.Levels = append(p.Levels, *levels[l])
	}

	// Level-0 checks against ENTITY_STATES.
	members := memberSets[0]
	p.Disjoint = true
	for _, n := range members {
		if n > 1 {
			p.Disjoint = false
			break
		}
	}
	covered := len(members) > 0 && len(members) == len(entities)
	missing, extra := 0, 0
	for _, id := range entities {
		if members[id] == 0 {
			missing++
		}
	}
	entitySet := make(map[string]bool, len(entities))
	for _, id := range entities {
		entitySet[id] = true
	}
	for m := range members {
		if !entitySet[m] {
			extra++
		}
	}
	p.Covered = covered && missing == 0 && extra == 0
	if !p.Covered {
		p.CoverNote = fmt.Sprintf("level 0 covers %d of %d entities (%d missing, %d not in ENTITY_STATES)", len(members)-extra, len(entities), missing, extra)
	}
	p.MapConsistent = len(p.EntityMap[0]) == len(members)
	for _, c := range p.Communities {
		if c.Level != 0 {
			continue
		}
		for _, m := range c.Members {
			if p.EntityMap[0][m] != c.ID {
				p.MapConsistent = false
			}
		}
	}
	return p
}

// parseIncomingKey decodes "target.source.hex(predicate)" where target and
// source are six-part entity IDs (kvProvider.getIncomingNeighbors).
func parseIncomingKey(k string) (target, source, predicate string, ok bool) {
	parts := strings.Split(k, ".")
	if len(parts) != 2*entityIDParts+1 {
		return "", "", "", false
	}
	raw, err := hex.DecodeString(parts[2*entityIDParts])
	if err != nil {
		return "", "", "", false
	}
	for _, p := range parts[:2*entityIDParts] {
		if p == "" {
			return "", "", "", false
		}
	}
	return strings.Join(parts[:entityIDParts], "."), strings.Join(parts[entityIDParts:2*entityIDParts], "."), string(raw), true
}

// --- KV helpers.

func openBucket(ctx context.Context, js jetstream.JetStream, name string, timeout time.Duration) (jetstream.KeyValue, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	kv, err := js.KeyValue(ctx, name)
	if err != nil {
		return nil, fmt.Errorf("open %s: %w", name, err)
	}
	return kv, nil
}

// listKeys returns the sorted, deduplicated keys of a bucket, failing rather
// than truncating when it holds more than maxKeys or the deadline passes.
func listKeys(ctx context.Context, kv jetstream.KeyValue, maxKeys int, timeout time.Duration) ([]string, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	lister, err := kv.ListKeys(ctx)
	if err != nil {
		if errors.Is(err, jetstream.ErrNoKeysFound) {
			return []string{}, nil
		}
		return nil, fmt.Errorf("list %s: %w", kv.Bucket(), err)
	}
	defer func() { _ = lister.Stop() }()
	seen := map[string]bool{}
	keys := []string{}
	for {
		select {
		case <-ctx.Done():
			return nil, fmt.Errorf("list %s: %w", kv.Bucket(), ctx.Err())
		case key, ok := <-lister.Keys():
			if !ok {
				if err := ctx.Err(); err != nil {
					return nil, fmt.Errorf("list %s: %w", kv.Bucket(), err)
				}
				sort.Strings(keys)
				return keys, nil
			}
			if seen[key] {
				continue
			}
			seen[key] = true
			if len(keys) >= maxKeys {
				return nil, fmt.Errorf("bucket %s holds more than -max-keys=%d keys", kv.Bucket(), maxKeys)
			}
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
		return nil, false, err
	}
	if len(entry.Value()) > maxBytes {
		return nil, false, fmt.Errorf("value of %s is %d bytes, over the %d byte bound", key, len(entry.Value()), maxBytes)
	}
	return entry.Value(), true, nil
}

func sleep(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

func writeJSON(p string, v any) error {
	data, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(p, append(data, '\n'), 0o644)
}

func writeJSONL[T any](p string, rows []T) error {
	f, err := os.Create(p)
	if err != nil {
		return err
	}
	w := bufio.NewWriter(f)
	enc := json.NewEncoder(w)
	for i := range rows {
		if err := enc.Encode(rows[i]); err != nil {
			_ = f.Close()
			return err
		}
	}
	if err := w.Flush(); err != nil {
		_ = f.Close()
		return err
	}
	return f.Close()
}
