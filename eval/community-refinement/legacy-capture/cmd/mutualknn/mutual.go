package main

import (
	"fmt"
	"math"
	"sort"
	"strings"
	"time"
)

// neighbor is one entry of a graph.embedding.query.similar reply, kept with
// the native similarity value.
type neighbor struct {
	EntityID   string  `json:"entity_id"`
	Similarity float64 `json:"similarity"`
}

// directedNeighbors reproduces how the legacy SemanticEdgeProvider turns one
// similar-reply into a directed top-k set: parseSimilarResponse keeps
// similarity >= threshold, refreshCache drops a self-edge, and the set
// semantics collapse duplicates. The server already applies limit=k; the k cap
// here only guards a reply that over-returns. Reply order is preserved.
func directedNeighbors(self string, similar []neighbor, threshold float64, k int) []neighbor {
	out := make([]neighbor, 0, min(len(similar), k))
	seen := make(map[string]bool, len(similar))
	for _, n := range similar {
		if len(out) >= k {
			break
		}
		if n.Similarity < threshold || n.EntityID == self || n.EntityID == "" || seen[n.EntityID] {
			continue
		}
		seen[n.EntityID] = true
		out = append(out, n)
	}
	return out
}

// computeMutual is a copy of the legacy semstreams
// graph/clustering.computeMutual: an edge A-B survives iff B is in A's
// directed set AND A is in B's. The result is symmetric by construction.
func computeMutual(directed map[string]map[string]bool) map[string]map[string]bool {
	mutual := make(map[string]map[string]bool, len(directed))
	for a, aSet := range directed {
		for b := range aSet {
			if directed[b] != nil && directed[b][a] {
				if mutual[a] == nil {
					mutual[a] = make(map[string]bool)
				}
				mutual[a][b] = true
			}
		}
	}
	return mutual
}

// unorderedPair names one mutual edge once, with A < B.
type unorderedPair struct {
	A, B string
}

// mutualPairs lists each symmetric mutual edge once, sorted for stable output.
func mutualPairs(mutual map[string]map[string]bool) []unorderedPair {
	var pairs []unorderedPair
	for a, set := range mutual {
		for b := range set {
			if a < b {
				pairs = append(pairs, unorderedPair{A: a, B: b})
			}
		}
	}
	sort.Slice(pairs, func(i, j int) bool {
		if pairs[i].A != pairs[j].A {
			return pairs[i].A < pairs[j].A
		}
		return pairs[i].B < pairs[j].B
	})
	return pairs
}

// entityIDParts holds the segments of a 6-part SemStreams entity ID
// {org}.{platform}.{domain}.{system}.{type}.{instance}.
type entityIDParts struct {
	System string
	Type   string
	Valid  bool
}

// parseEntityID splits at most six segments so an instance containing dots
// stays whole; fewer than six segments is reported as invalid.
func parseEntityID(id string) entityIDParts {
	parts := strings.SplitN(id, ".", 6)
	if len(parts) != 6 {
		return entityIDParts{}
	}
	for _, p := range parts {
		if p == "" {
			return entityIDParts{}
		}
	}
	return entityIDParts{System: parts[3], Type: parts[4], Valid: true}
}

// histogramBin is one fixed-width similarity bin. The final bin is closed so a
// similarity of exactly 1.0 is counted.
type histogramBin struct {
	Range string `json:"range"`
	Count int    `json:"count"`
}

const similarityBinWidth = 0.05

// similarityHistogram bins values >= lower into 0.05-wide bins up to 1.0.
// Values below lower are skipped; callers pass threshold-filtered values.
func similarityHistogram(values []float64, lower float64) []histogramBin {
	n := int(math.Ceil((1.0-lower)/similarityBinWidth - 1e-9))
	if n < 1 {
		n = 1
	}
	bins := make([]histogramBin, n)
	for i := range bins {
		lo := lower + float64(i)*similarityBinWidth
		hi := math.Min(lo+similarityBinWidth, 1.0)
		closer := ")"
		if i == n-1 {
			closer = "]"
		}
		bins[i].Range = fmt.Sprintf("[%.2f,%.2f%s", lo, hi, closer)
	}
	for _, v := range values {
		if v < lower {
			continue
		}
		// The epsilon keeps a boundary value such as 0.85 out of the lower bin
		// despite binary floating-point subtraction.
		i := int((v-lower)/similarityBinWidth + 1e-9)
		if i >= n {
			i = n - 1
		}
		bins[i].Count++
	}
	return bins
}

// latencyStats reports nearest-rank percentiles in milliseconds.
type latencyStats struct {
	Count int     `json:"count"`
	P50MS float64 `json:"p50_ms"`
	P95MS float64 `json:"p95_ms"`
	MaxMS float64 `json:"max_ms"`
}

func summarizeLatency(durations []time.Duration) latencyStats {
	if len(durations) == 0 {
		return latencyStats{}
	}
	sorted := append([]time.Duration(nil), durations...)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i] < sorted[j] })
	rank := func(p float64) time.Duration {
		i := int(math.Ceil(p*float64(len(sorted)))) - 1
		if i < 0 {
			i = 0
		}
		return sorted[i]
	}
	ms := func(d time.Duration) float64 { return float64(d.Microseconds()) / 1000.0 }
	return latencyStats{
		Count: len(sorted),
		P50MS: ms(rank(0.50)),
		P95MS: ms(rank(0.95)),
		MaxMS: ms(sorted[len(sorted)-1]),
	}
}
