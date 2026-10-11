package main

import (
	"reflect"
	"testing"
)

func fid(system, typ, name string) string {
	return "semselect.semsource.golang." + system + "." + typ + "." + name
}

var semanticProfile = identityParams{
	IncludeSiblings: true, IncludeSystemPeers: true,
	SiblingWeight: 0.7, MaxSiblings: 5, SystemPeerWeight: 0.2, MaxSystemPeers: 8,
}

func TestListedNeighborsSortsCapsAndExcludes(t *testing.T) {
	voter := fid("fam", "function", "m")
	var ids []string
	for _, n := range []string{"z", "a", "c", "b", "d", "e", "f", "g"} { // 8 siblings, unsorted
		ids = append(ids, fid("fam", "function", n))
	}
	for _, n := range []string{"y", "x"} { // same system, other type
		ids = append(ids, fid("fam", "struct", n))
	}
	ids = append(ids, voter, fid("other", "function", "q"), "malformed.id")
	idx := buildIDIndex(ids)

	explicit := map[string]bool{fid("fam", "function", "a"): true, fid("fam", "struct", "x"): true}
	p := semanticProfile
	p.MaxSiblings, p.MaxSystemPeers = 3, 4
	siblings, peers := listedNeighbors(voter, explicit, idx, p)

	wantSiblings := []string{fid("fam", "function", "b"), fid("fam", "function", "c"), fid("fam", "function", "d")}
	if !reflect.DeepEqual(siblings, wantSiblings) {
		t.Fatalf("siblings = %v, want sorted, capped, explicit excluded %v", siblings, wantSiblings)
	}
	// Peers: same system, sorted ("function.*" before "struct.*"); excludes self,
	// explicit (a, x) and the listed siblings (b, c, d); siblings cut by the
	// sibling cap (e, f, g, z) stay eligible and fill the peer cap before struct.y.
	wantPeers := []string{fid("fam", "function", "e"), fid("fam", "function", "f"), fid("fam", "function", "g"), fid("fam", "function", "z")}
	p.MaxSystemPeers = 5
	_, peersWider := listedNeighbors(voter, explicit, idx, p)
	if got := peersWider[4]; got != fid("fam", "struct", "y") {
		t.Fatalf("fifth peer = %s, want struct.y after the sibling overflow", got)
	}
	p.MaxSystemPeers = 4
	if !reflect.DeepEqual(peers, wantPeers) {
		t.Fatalf("peers = %v, want %v", peers, wantPeers)
	}
	for _, n := range peers {
		tier, w := edgeWeight(voter, n, explicit, p)
		if typePrefix(n) == typePrefix(voter) && (tier != tierSibling || w != 0.7) {
			t.Fatalf("capped sibling %s listed as peer must vote at the sibling weight, got %s %v", n, tier, w)
		}
		if typePrefix(n) != typePrefix(voter) && (tier != tierSystemPeer || w != 0.2) {
			t.Fatalf("system peer %s: got %s %v", n, tier, w)
		}
	}
}

func TestEdgeWeightCascade(t *testing.T) {
	a, b := fid("fam", "function", "a"), fid("fam", "function", "b")
	c := fid("fam", "struct", "c")
	d := fid("other", "function", "d")
	explicit := map[string]bool{b: true}
	cases := []struct {
		to     string
		p      identityParams
		tier   string
		weight float64
	}{
		{b, semanticProfile, tierExplicit, 1.0},
		{c, semanticProfile, tierSystemPeer, 0.2},
		{d, semanticProfile, tierNone, 0},
		{fid("fam", "function", "z"), semanticProfile, tierSibling, 0.7},
		{fid("fam", "function", "z"), identityParams{IncludeSystemPeers: true, SystemPeerWeight: 0.3}, tierSystemPeer, 0.3},
		{c, identityParams{IncludeSiblings: true, SiblingWeight: 0.7}, tierNone, 0},
		{"malformed", semanticProfile, tierNone, 0},
	}
	for _, tc := range cases {
		tier, w := edgeWeight(a, tc.to, explicit, tc.p)
		if tier != tc.tier || w != tc.weight {
			t.Errorf("edgeWeight(%s -> %s, %+v) = %s %v, want %s %v", a, tc.to, tc.p, tier, w, tc.tier, tc.weight)
		}
	}
}

func TestMalformedIDsGetNoIdentityEdges(t *testing.T) {
	idx := buildIDIndex([]string{"a.b.c", fid("fam", "function", "a"), fid("fam", "function", "b")})
	siblings, peers := listedNeighbors("a.b.c", nil, idx, semanticProfile)
	if len(siblings) != 0 || len(peers) != 0 {
		t.Fatalf("malformed voter got identity edges: %v %v", siblings, peers)
	}
	if _, ok := idx.byPrefix[""]; ok {
		t.Fatal("malformed ids must not be indexed")
	}
	siblings, peers = listedNeighbors(fid("fam", "function", "a"), nil, idx, identityParams{})
	if len(siblings) != 0 || len(peers) != 0 {
		t.Fatalf("disabled synthesis listed %v %v", siblings, peers)
	}
}
