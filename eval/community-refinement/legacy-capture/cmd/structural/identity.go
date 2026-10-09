package main

import (
	"sort"
	"strings"
)

// Edge tiers of the legacy clustering provider chain (SemStreams
// v1.0.0-beta.160, graph/clustering/entityid_provider.go). Explicit edges come
// from the graph indexes; sibling and system-peer edges are synthesized from the
// six-part entity ID and never persisted.
const (
	tierExplicit   = "explicit"
	tierSibling    = "sibling"
	tierSystemPeer = "system_peer"
	tierNone       = "none"
)

// identityParams mirrors clustering.EntityIDProviderConfig. The defaults used by
// the capture are the semantic-enabled structural baseline of
// processor/graph-clustering (sibling 0.7 capped at 5, system peer 0.2 capped
// at 8): the structural weights the semantic profile keeps when its semantic
// tier is off, which is the structural-only partition the protocol freezes.
type identityParams struct {
	IncludeSiblings    bool    `json:"include_siblings"`
	IncludeSystemPeers bool    `json:"include_system_peers"`
	SiblingWeight      float64 `json:"sibling_weight"`
	MaxSiblings        int     `json:"max_siblings"`
	SystemPeerWeight   float64 `json:"system_peer_weight"`
	MaxSystemPeers     int     `json:"max_system_peers"`
}

// entityIDParts is the canonical six-part entity ID length:
// org.platform.domain.system.type.instance.
const entityIDParts = 6

// typePrefix returns the five-part type prefix of a six-part entity ID, or ""
// for any other shape (getTypePrefix in the legacy provider).
func typePrefix(id string) string {
	parts := strings.Split(id, ".")
	if len(parts) != entityIDParts {
		return ""
	}
	return strings.Join(parts[:5], ".")
}

// systemOf returns the system segment of a six-part entity ID, or "" (getSystem
// in the legacy provider).
func systemOf(id string) string {
	parts := strings.Split(id, ".")
	if len(parts) != entityIDParts {
		return ""
	}
	return parts[3]
}

// idIndex is the legacy provider's prefix and system caches: candidate lists
// sorted lexically, so the per-entity caps keep the same candidates on every run.
type idIndex struct {
	byPrefix map[string][]string
	bySystem map[string][]string
}

func buildIDIndex(ids []string) idIndex {
	idx := idIndex{byPrefix: map[string][]string{}, bySystem: map[string][]string{}}
	for _, id := range ids {
		prefix := typePrefix(id)
		if prefix == "" {
			continue
		}
		idx.byPrefix[prefix] = append(idx.byPrefix[prefix], id)
		if system := systemOf(id); system != "" {
			idx.bySystem[system] = append(idx.bySystem[system], id)
		}
	}
	for k := range idx.byPrefix {
		sort.Strings(idx.byPrefix[k])
	}
	for k := range idx.bySystem {
		sort.Strings(idx.bySystem[k])
	}
	return idx
}

// listedNeighbors reproduces EntityIDProvider.GetNeighbors for one voter: the
// capped sibling list excludes self and explicit neighbours; the capped
// system-peer list additionally excludes the siblings that were listed, so a
// sibling cut by the sibling cap can still be listed as a system peer.
func listedNeighbors(id string, explicit map[string]bool, idx idIndex, p identityParams) (siblings, peers []string) {
	siblings, peers = []string{}, []string{}
	if p.IncludeSiblings {
		for _, s := range idx.byPrefix[typePrefix(id)] {
			if s == id || explicit[s] {
				continue
			}
			siblings = append(siblings, s)
			if len(siblings) >= p.MaxSiblings {
				break
			}
		}
	}
	if p.IncludeSystemPeers {
		listed := make(map[string]bool, len(siblings))
		for _, s := range siblings {
			listed[s] = true
		}
		for _, s := range idx.bySystem[systemOf(id)] {
			if s == id || explicit[s] || listed[s] {
				continue
			}
			peers = append(peers, s)
			if len(peers) >= p.MaxSystemPeers {
				break
			}
		}
	}
	return siblings, peers
}

// edgeWeight reproduces the GetEdgeWeight cascade for a voter and one listed
// neighbour: an explicit edge in either direction is 1.0; otherwise the sibling
// tier, then the system-peer tier, each only when enabled. The cascade is
// evaluated on identity, not on which list the neighbour came from, so a
// neighbour listed as a system peer that shares the type prefix still votes at
// the sibling weight.
func edgeWeight(from, to string, explicit map[string]bool, p identityParams) (tier string, weight float64) {
	if explicit[to] {
		return tierExplicit, 1.0
	}
	if p.IncludeSiblings {
		if pf := typePrefix(from); pf != "" && pf == typePrefix(to) {
			return tierSibling, p.SiblingWeight
		}
	}
	if p.IncludeSystemPeers {
		if sf := systemOf(from); sf != "" && sf == systemOf(to) {
			return tierSystemPeer, p.SystemPeerWeight
		}
	}
	return tierNone, 0
}
