// Command mutualknn reproduces, externally and read-only, the candidate
// generation of the legacy SemStreams SemanticEdgeProvider against a live
// legacy tier-1 stack: one graph.embedding.query.similar request per embedded
// entity, a client-side similarity threshold, and the symmetric mutual-kNN
// intersection. Its output is a legacy SemStreams capture, never a SemEngine
// result.
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
	"log/slog"
	"math"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"runtime/debug"
	"sort"
	"syscall"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

const provenance = "legacy SemStreams capture; not a SemEngine result"

// Legacy KV buckets (semstreams graph/constants.go).
const (
	bucketEntityStates   = "ENTITY_STATES"
	bucketEmbeddingIndex = "EMBEDDING_INDEX"
	bucketOutgoingIndex  = "OUTGOING_INDEX"
)

const maxMetadataBytes = 1 << 20

type options struct {
	natsURL     string
	k           int
	threshold   float64
	concurrency int
	timeout     time.Duration
	backoff     time.Duration
	output      string
	maxEntities int

	corpusRepo        string
	corpusCommit      string
	semsourceCommit   string
	semstreamsVersion string
	semembedImage     string
	semembedModel     string
	configPath        string
	composeFile       string
	composeOverride   string
	dockerArch        string
	runMetadata       string
}

func parseFlags(args []string) (options, error) {
	var o options
	fs := flag.NewFlagSet("mutualknn", flag.ContinueOnError)
	fs.StringVar(&o.natsURL, "nats", "nats://localhost:4222", "NATS URL of the legacy stack")
	fs.IntVar(&o.k, "k", 8, "mutual-kNN k (legacy DefaultSemanticMaxNeighbors)")
	fs.Float64Var(&o.threshold, "threshold", 0.75, "client-side similarity threshold (legacy DefaultSemanticThreshold)")
	fs.IntVar(&o.concurrency, "concurrency", 8, "concurrent similar requests")
	fs.DurationVar(&o.timeout, "timeout", 10*time.Second, "timeout per similar request")
	fs.DurationVar(&o.backoff, "retry-backoff", 250*time.Millisecond, "linear backoff unit between transient retries")
	fs.StringVar(&o.output, "output", "", "output directory; must not exist")
	fs.IntVar(&o.maxEntities, "max-entities", 50000, "abort if a bucket holds more keys than this")

	fs.StringVar(&o.corpusRepo, "corpus-repo", "", "corpus repository URL (provenance)")
	fs.StringVar(&o.corpusCommit, "corpus-commit", "", "corpus commit (provenance)")
	fs.StringVar(&o.semsourceCommit, "semsource-commit", "", "SemSource commit that built the stack (provenance)")
	fs.StringVar(&o.semstreamsVersion, "semstreams-version", "", "SemStreams version from SemSource's go.mod (provenance)")
	fs.StringVar(&o.semembedImage, "semembed-image", "", "semembed image reference with digest (provenance)")
	fs.StringVar(&o.semembedModel, "semembed-model", "", "semembed model name (provenance)")
	fs.StringVar(&o.configPath, "config-path", "", "SemSource config file the stack ran (hashed)")
	fs.StringVar(&o.composeFile, "compose-file", "", "compose file the stack ran (hashed; optional)")
	fs.StringVar(&o.composeOverride, "compose-override", "", "compose override file (hashed; empty when none was used)")
	fs.StringVar(&o.dockerArch, "docker-arch", "", "architecture of the Docker engine that ran the stack")
	fs.StringVar(&o.runMetadata, "run-metadata", "", "optional JSON file embedded verbatim under manifest.run")
	if err := fs.Parse(args); err != nil {
		return o, err
	}
	switch {
	case o.output == "":
		return o, errors.New("-output is required")
	case o.k < 1 || o.k > 100:
		return o, errors.New("-k must be in 1..100 (the handler caps limit at 100)")
	case math.IsNaN(o.threshold) || o.threshold <= 0 || o.threshold > 1:
		return o, errors.New("-threshold must be in (0,1]")
	case o.concurrency < 1 || o.concurrency > 64:
		return o, errors.New("-concurrency must be in 1..64")
	case o.timeout <= 0 || o.backoff < 0:
		return o, errors.New("-timeout must be positive and -retry-backoff non-negative")
	case o.maxEntities < 1:
		return o, errors.New("-max-entities must be positive")
	}
	for name, v := range map[string]string{
		"-corpus-repo": o.corpusRepo, "-corpus-commit": o.corpusCommit,
		"-semsource-commit": o.semsourceCommit, "-semstreams-version": o.semstreamsVersion,
		"-semembed-image": o.semembedImage, "-semembed-model": o.semembedModel,
		"-config-path": o.configPath,
	} {
		if v == "" {
			return o, fmt.Errorf("%s is required: every capture must carry its provenance", name)
		}
	}
	return o, nil
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	logger := slog.New(slog.NewTextHandler(os.Stderr, nil))
	if err := run(ctx, os.Args[1:], logger); err != nil {
		logger.Error("mutualknn failed", "error", err)
		os.Exit(1)
	}
}

func run(ctx context.Context, args []string, logger *slog.Logger) error {
	started := time.Now().UTC()
	o, err := parseFlags(args)
	if err != nil {
		return err
	}
	if _, err := os.Lstat(o.output); err == nil {
		return fmt.Errorf("output %s already exists", o.output)
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	// Hash provenance inputs before touching the stack so a bad path fails fast.
	hashes, err := hashFiles(map[string]string{
		"config": o.configPath, "compose_file": o.composeFile, "compose_override": o.composeOverride,
	})
	if err != nil {
		return err
	}
	var runMeta json.RawMessage
	if o.runMetadata != "" {
		if runMeta, err = readJSONFile(o.runMetadata); err != nil {
			return err
		}
	}
	if err := os.MkdirAll(o.output, 0o755); err != nil {
		return err
	}

	nc, err := nats.Connect(o.natsURL, nats.Name("semselect-legacy-capture"), nats.Timeout(5*time.Second))
	if err != nil {
		return fmt.Errorf("connect %s: %w", o.natsURL, err)
	}
	defer nc.Close()
	js, err := jetstream.New(nc)
	if err != nil {
		return err
	}

	entityIDs, err := bucketKeys(ctx, js, bucketEntityStates, o.maxEntities)
	if err != nil {
		return err
	}
	embeddedIDs, err := bucketKeys(ctx, js, bucketEmbeddingIndex, o.maxEntities)
	if err != nil {
		return err
	}
	logger.Info("listed buckets", "entities", len(entityIDs), "embedded", len(embeddedIDs))

	queryStart := time.Now()
	results, abortErr := sweep(ctx, nc, embeddedIDs, o.k, o.threshold, o.timeout, o.backoff, o.concurrency,
		func(done int) {
			if done%500 == 0 || done == len(embeddedIDs) {
				logger.Info("similar queries", "done", done, "of", len(embeddedIDs))
			}
		})
	queryPhase := time.Since(queryStart)
	if ctx.Err() != nil {
		return fmt.Errorf("interrupted during the similar sweep: %w", ctx.Err())
	}

	sum, pairs := summarize(o, entityIDs, embeddedIDs, results)
	sum.QueryPhaseSeconds = queryPhase.Seconds()
	if abortErr != nil {
		sum.Aborted, sum.AbortReason = true, abortErr.Error()
	}

	lines := resolveExplicit(ctx, js, pairs, o.concurrency, &sum, logger)

	if err := writeJSONL(filepath.Join(o.output, "directed.jsonl"), results); err != nil {
		return err
	}
	if err := writeJSONL(filepath.Join(o.output, "mutual_pairs.jsonl"), lines); err != nil {
		return err
	}
	finished := time.Now().UTC()
	sum.WallclockSeconds = finished.Sub(started).Seconds()
	if err := writeJSON(filepath.Join(o.output, "summary.json"), sum); err != nil {
		return err
	}
	if err := writeJSON(filepath.Join(o.output, "manifest.json"),
		buildManifest(o, hashes, runMeta, started, finished)); err != nil {
		return err
	}
	logger.Info("capture written", "output", o.output, "mutual_pairs", sum.MutualPairs,
		"failed", sum.Failed, "aborted", sum.Aborted)
	if abortErr != nil {
		return abortErr
	}
	return nil
}

func bucketKeys(ctx context.Context, js jetstream.JetStream, bucket string, maxKeys int) ([]string, error) {
	kv, err := js.KeyValue(ctx, bucket)
	if err != nil {
		return nil, fmt.Errorf("open %s: %w", bucket, err)
	}
	keys, err := listKeys(ctx, kv, maxKeys)
	if err != nil {
		return nil, fmt.Errorf("list %s: %w", bucket, err)
	}
	sort.Strings(keys)
	return keys, nil
}

type summary struct {
	Provenance string `json:"provenance"`
	Parameters struct {
		K           int     `json:"k"`
		Threshold   float64 `json:"threshold"`
		Concurrency int     `json:"concurrency"`
		TimeoutMS   int64   `json:"timeout_ms"`
		MaxRetries  int     `json:"max_retries"`
	} `json:"parameters"`

	TotalEntities             int            `json:"total_entities"`
	EmbeddedEntities          int            `json:"embedded_entities"`
	EmbeddedNotInEntityStates int            `json:"embedded_not_in_entity_states"`
	MalformedEntityIDs        int            `json:"malformed_entity_ids"`
	EntitiesByType            map[string]int `json:"entities_by_type"`
	EntitiesBySystem          map[string]int `json:"entities_by_system"`
	EmbeddedByType            map[string]int `json:"embedded_by_type"`

	Queried              int            `json:"queried"`
	Answered             int            `json:"answered"`
	EmbeddingUnavailable int            `json:"embedding_unavailable"`
	Failed               int            `json:"failed"`
	FailedByKind         map[string]int `json:"failed_by_kind"`
	NotQueried           int            `json:"not_queried"`
	Retries              int            `json:"retries"`
	Aborted              bool           `json:"aborted"`
	AbortReason          string         `json:"abort_reason,omitempty"`

	SimilarReturned              int `json:"similar_returned"`
	DirectedPairsAtThreshold     int `json:"directed_pairs_at_threshold"`
	NeighborIDsNotInEntityStates int `json:"neighbor_ids_not_in_entity_states"`

	MutualPairs                         int            `json:"mutual_pairs"`
	MutualPairsExplicitDominated        *int           `json:"mutual_pairs_explicit_dominated"`
	MutualPairsExplicitDominatedReason  string         `json:"mutual_pairs_explicit_dominated_null_reason,omitempty"`
	MutualPairsReviewCandidates         *int           `json:"mutual_pairs_review_candidates"`
	MutualPairsCrossType                int            `json:"mutual_pairs_cross_type"`
	MutualPairsCrossSystem              int            `json:"mutual_pairs_cross_system"`
	MutualPairsByTypePair               map[string]int `json:"mutual_pairs_by_type_pair"`
	MutualPairsAsymmetricSimilarity     int            `json:"mutual_pairs_asymmetric_similarity"`
	EntitiesWithMutualEdges             int            `json:"entities_with_mutual_edges"`
	MutualDegreeHistogram               []int          `json:"mutual_degree_histogram"`
	DirectedDegreeHistogram             []int          `json:"directed_degree_histogram"`
	SimilarityHistogram                 []histogramBin `json:"similarity_histogram"`
	SimilarityHistogramDirected         []histogramBin `json:"similarity_histogram_directed"`
	DegreeHistogramDenominatorEntities  int            `json:"degree_histogram_denominator_entities"`
	SimilarityHistogramMutualDefinition string         `json:"similarity_histogram_definition"`

	Latency           latencyStats `json:"latency"`
	QueryPhaseSeconds float64      `json:"query_phase_seconds"`
	WallclockSeconds  float64      `json:"wallclock_seconds"`
}

// pairInfo carries a mutual pair's per-direction evidence before the explicit
// edge lookup.
type pairInfo struct {
	unorderedPair
	simAB, simBA   float64
	rankAB, rankBA int
}

func summarize(o options, entityIDs, embeddedIDs []string, results []queryResult) (summary, []pairInfo) {
	var s summary
	s.Provenance = provenance
	s.Parameters.K, s.Parameters.Threshold = o.k, o.threshold
	s.Parameters.Concurrency, s.Parameters.TimeoutMS = o.concurrency, o.timeout.Milliseconds()
	s.Parameters.MaxRetries = maxRetries

	known := make(map[string]bool, len(entityIDs))
	s.EntitiesByType, s.EntitiesBySystem, s.EmbeddedByType = map[string]int{}, map[string]int{}, map[string]int{}
	for _, id := range entityIDs {
		known[id] = true
		p := parseEntityID(id)
		if !p.Valid {
			s.MalformedEntityIDs++
			continue
		}
		s.EntitiesByType[p.Type]++
		s.EntitiesBySystem[p.System]++
	}
	for _, id := range embeddedIDs {
		if !known[id] {
			s.EmbeddedNotInEntityStates++
		}
		if p := parseEntityID(id); p.Valid {
			s.EmbeddedByType[p.Type]++
		}
	}
	s.TotalEntities, s.EmbeddedEntities = len(entityIDs), len(embeddedIDs)

	// The legacy provider caches a definitive (possibly empty) directed set for
	// every answered entity, including embedding_unavailable misses.
	directed := make(map[string]map[string]bool, len(results))
	position := make(map[string]map[string]int, len(results))
	sims := make(map[string]map[string]float64, len(results))
	s.FailedByKind = map[string]int{}
	s.DirectedDegreeHistogram = make([]int, o.k+1)
	var latencies []time.Duration
	var directedSims []float64
	for _, r := range results {
		if r.Status != statusNotQueried {
			s.Queried++
			s.Retries += r.Attempts - 1
		}
		switch r.Status {
		case statusOK, statusMiss:
			s.Answered++
			latencies = append(latencies, r.latency)
			set := make(map[string]bool, len(r.Directed))
			position[r.EntityID] = make(map[string]int, len(r.Directed))
			sims[r.EntityID] = make(map[string]float64, len(r.Directed))
			for i, n := range r.Directed {
				set[n.EntityID] = true
				position[r.EntityID][n.EntityID] = i + 1
				sims[r.EntityID][n.EntityID] = n.Similarity
				directedSims = append(directedSims, n.Similarity)
				if !known[n.EntityID] {
					s.NeighborIDsNotInEntityStates++
				}
			}
			directed[r.EntityID] = set
			s.SimilarReturned += len(r.Similar)
			s.DirectedPairsAtThreshold += len(r.Directed)
			s.DirectedDegreeHistogram[len(r.Directed)]++
			if r.Status == statusMiss {
				s.EmbeddingUnavailable++
			}
		case statusFailed:
			s.Failed++
			s.FailedByKind[r.FailureKind]++
		case statusNotQueried:
			s.NotQueried++
		}
	}
	s.Latency = summarizeLatency(latencies)
	s.SimilarityHistogramDirected = similarityHistogram(directedSims, o.threshold)

	mutual := computeMutual(directed)
	s.EntitiesWithMutualEdges = len(mutual)
	s.MutualDegreeHistogram = make([]int, o.k+1)
	for id := range directed {
		s.MutualDegreeHistogram[len(mutual[id])]++
	}
	s.DegreeHistogramDenominatorEntities = len(directed)

	ordered := mutualPairs(mutual)
	pairs := make([]pairInfo, 0, len(ordered))
	s.MutualPairsByTypePair = map[string]int{}
	var mutualSims []float64
	for _, p := range ordered {
		info := pairInfo{
			unorderedPair: p,
			simAB:         sims[p.A][p.B], simBA: sims[p.B][p.A],
			rankAB: position[p.A][p.B], rankBA: position[p.B][p.A],
		}
		pairs = append(pairs, info)
		mutualSims = append(mutualSims, math.Min(info.simAB, info.simBA))
		if math.Abs(info.simAB-info.simBA) > 1e-6 {
			s.MutualPairsAsymmetricSimilarity++
		}
		pa, pb := parseEntityID(p.A), parseEntityID(p.B)
		if pa.Type != pb.Type {
			s.MutualPairsCrossType++
		}
		if pa.System != pb.System {
			s.MutualPairsCrossSystem++
		}
		ta, tb := pa.Type, pb.Type
		if tb < ta {
			ta, tb = tb, ta
		}
		s.MutualPairsByTypePair[ta+"|"+tb]++
	}
	s.MutualPairs = len(pairs)
	s.SimilarityHistogram = similarityHistogram(mutualSims, o.threshold)
	s.SimilarityHistogramMutualDefinition = "mutual pairs binned by min(similarity_a_to_b, similarity_b_to_a); " +
		"directed histogram bins every directed neighbour at or above the threshold"
	return s, pairs
}

type mutualPairLine struct {
	A                 string  `json:"a"`
	B                 string  `json:"b"`
	SimilarityAToB    float64 `json:"similarity_a_to_b"`
	SimilarityBToA    float64 `json:"similarity_b_to_a"`
	RankAToB          int     `json:"rank_a_to_b"`
	RankBToA          int     `json:"rank_b_to_a"`
	SystemA           string  `json:"system_a"`
	TypeA             string  `json:"type_a"`
	SystemB           string  `json:"system_b"`
	TypeB             string  `json:"type_b"`
	SameSystem        bool    `json:"same_system"`
	SameType          bool    `json:"same_type"`
	ExplicitDominated *bool   `json:"explicit_dominated"`
}

// resolveExplicit marks each mutual pair explicit-dominated when an explicit
// edge exists in either direction. The legacy kvProvider's "both" neighbour set
// is OUTGOING_INDEX(A) ∪ INCOMING_INDEX(A); because INCOMING_INDEX(A) holds B
// exactly when OUTGOING_INDEX(B) holds A, reading both endpoints' outgoing rows
// answers the same question. A pair with an unreadable endpoint stays null.
func resolveExplicit(ctx context.Context, js jetstream.JetStream, pairs []pairInfo, concurrency int,
	s *summary, logger *slog.Logger,
) []mutualPairLine {
	lines := make([]mutualPairLine, len(pairs))
	for i, p := range pairs {
		pa, pb := parseEntityID(p.A), parseEntityID(p.B)
		lines[i] = mutualPairLine{
			A: p.A, B: p.B, SimilarityAToB: p.simAB, SimilarityBToA: p.simBA,
			RankAToB: p.rankAB, RankBToA: p.rankBA,
			SystemA: pa.System, TypeA: pa.Type, SystemB: pb.System, TypeB: pb.Type,
			SameSystem: pa.System == pb.System, SameType: pa.Type == pb.Type,
		}
	}
	if len(pairs) == 0 {
		zero := 0
		s.MutualPairsExplicitDominated, s.MutualPairsReviewCandidates = &zero, &zero
		return lines
	}
	kv, err := js.KeyValue(ctx, bucketOutgoingIndex)
	if err != nil {
		s.MutualPairsExplicitDominatedReason = fmt.Sprintf("open %s: %v", bucketOutgoingIndex, err)
		return lines
	}
	endpointSet := make(map[string]bool)
	for _, p := range pairs {
		endpointSet[p.A], endpointSet[p.B] = true, true
	}
	endpoints := make([]string, 0, len(endpointSet))
	for id := range endpointSet {
		endpoints = append(endpoints, id)
	}
	sort.Strings(endpoints)
	out, readErrs := loadOutgoing(ctx, kv, endpoints, concurrency)
	logger.Info("explicit-edge reads", "endpoints", len(endpoints), "errors", len(readErrs))

	dominated, unresolved := 0, 0
	for i, p := range pairs {
		v, resolved := explicitEdge(out, p.A, p.B)
		if !resolved {
			unresolved++
			continue
		}
		lines[i].ExplicitDominated = &v
		if v {
			dominated++
		}
	}
	if unresolved > 0 {
		s.MutualPairsExplicitDominatedReason = fmt.Sprintf(
			"%d of %d pairs unresolved: %d OUTGOING_INDEX reads failed (first: %s)",
			unresolved, len(pairs), len(readErrs), firstError(readErrs))
		return lines
	}
	candidates := len(pairs) - dominated
	s.MutualPairsExplicitDominated, s.MutualPairsReviewCandidates = &dominated, &candidates
	return lines
}

// explicitEdge reports whether an explicit edge joins a and b in either
// direction; resolved is false when either endpoint's outgoing row is unknown.
func explicitEdge(out map[string]map[string]bool, a, b string) (dominated, resolved bool) {
	outA, okA := out[a]
	outB, okB := out[b]
	if !okA || !okB {
		return false, false
	}
	return outA[b] || outB[a], true
}

func firstError(m map[string]string) string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	if len(keys) == 0 {
		return "none"
	}
	return keys[0] + ": " + m[keys[0]]
}

type fileHash struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
}

func hashFiles(paths map[string]string) (map[string]*fileHash, error) {
	out := make(map[string]*fileHash, len(paths))
	for name, p := range paths {
		if p == "" {
			out[name] = nil
			continue
		}
		f, err := os.Open(p)
		if err != nil {
			return nil, fmt.Errorf("hash %s: %w", name, err)
		}
		h := sha256.New()
		_, err = io.Copy(h, f)
		_ = f.Close()
		if err != nil {
			return nil, fmt.Errorf("hash %s: %w", name, err)
		}
		out[name] = &fileHash{Path: p, SHA256: hex.EncodeToString(h.Sum(nil))}
	}
	return out, nil
}

func readJSONFile(p string) (json.RawMessage, error) {
	f, err := os.Open(p)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, maxMetadataBytes+1))
	if err != nil {
		return nil, err
	}
	if len(data) > maxMetadataBytes {
		return nil, fmt.Errorf("%s exceeds %d bytes", p, maxMetadataBytes)
	}
	if !json.Valid(data) {
		return nil, fmt.Errorf("%s is not valid JSON", p)
	}
	return json.RawMessage(data), nil
}

type manifest struct {
	Provenance string `json:"provenance"`
	Corpus     struct {
		Repo   string `json:"repo"`
		Commit string `json:"commit"`
	} `json:"corpus"`
	SemSourceCommit   string `json:"semsource_commit"`
	SemStreamsVersion string `json:"semstreams_version"`
	Semembed          struct {
		Image string `json:"image"`
		Model string `json:"model"`
	} `json:"semembed"`
	Config                    *fileHash       `json:"config"`
	ComposeFile               *fileHash       `json:"compose_file"`
	ComposeOverride           *fileHash       `json:"compose_override"`
	ComposeOverrideNullReason string          `json:"compose_override_null_reason,omitempty"`
	Machine                   machineInfo     `json:"machine"`
	Tool                      toolInfo        `json:"tool"`
	NATSURL                   string          `json:"nats_url"`
	StartedUTC                string          `json:"started_utc"`
	FinishedUTC               string          `json:"finished_utc"`
	Run                       json.RawMessage `json:"run,omitempty"`
}

type machineInfo struct {
	ToolOS     string `json:"tool_os"`
	ToolArch   string `json:"tool_arch"`
	DockerArch string `json:"docker_arch"`
}

type toolInfo struct {
	Module      string `json:"module"`
	GoVersion   string `json:"go_version"`
	VCSRevision string `json:"vcs_revision,omitempty"`
	VCSModified string `json:"vcs_modified,omitempty"`
}

func buildManifest(o options, hashes map[string]*fileHash, runMeta json.RawMessage, started, finished time.Time) manifest {
	var m manifest
	m.Provenance = provenance
	m.Corpus.Repo, m.Corpus.Commit = o.corpusRepo, o.corpusCommit
	m.SemSourceCommit, m.SemStreamsVersion = o.semsourceCommit, o.semstreamsVersion
	m.Semembed.Image, m.Semembed.Model = o.semembedImage, o.semembedModel
	m.Config, m.ComposeFile, m.ComposeOverride = hashes["config"], hashes["compose_file"], hashes["compose_override"]
	if m.ComposeOverride == nil {
		m.ComposeOverrideNullReason = "no compose override was used"
	}
	m.Machine = machineInfo{ToolOS: runtime.GOOS, ToolArch: runtime.GOARCH, DockerArch: o.dockerArch}
	m.Tool.GoVersion = runtime.Version()
	if bi, ok := debug.ReadBuildInfo(); ok {
		m.Tool.Module = bi.Main.Path
		for _, kv := range bi.Settings {
			switch kv.Key {
			case "vcs.revision":
				m.Tool.VCSRevision = kv.Value
			case "vcs.modified":
				m.Tool.VCSModified = kv.Value
			}
		}
	}
	m.NATSURL = o.natsURL
	m.StartedUTC, m.FinishedUTC = started.Format(time.RFC3339), finished.Format(time.RFC3339)
	m.Run = runMeta
	return m
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
