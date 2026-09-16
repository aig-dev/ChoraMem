// Package selection implements deterministic online Memory selection.
// Scores are transient retrieval facts and never leave this package.
package selection

import (
	"errors"
	"math"
	"sort"
)

var ErrInvalidConfiguration = errors.New("invalid selection configuration")

type Query struct {
	Ref  string
	Text string
}

type Anchor struct {
	EpisodeRef string
	SourceRef  string
	Text       string
}

// IndexMatch is one transient semantic association returned by MemoryIndex.
// SupportEpisodeRef is non-empty only when an indexed Episode is canonical
// support for this exact Disposition. Rank is meaningful only inside its
// query/owner lane; LaneOrder deterministically interleaves those lanes.
type IndexMatch struct {
	QuerySourceRef    string
	SupportEpisodeRef string
	Rank              int
	LaneOrder         int
}

type CandidateKind string

const (
	CandidateRecollection CandidateKind = "recollection"
	CandidateDisposition  CandidateKind = "disposition"
)

type Candidate struct {
	Kind         CandidateKind
	VersionRef   string
	Text         string
	Scope        ApplicationScope
	OwnerOrder   int
	Support      []Anchor
	Inhibition   []Anchor
	Reenactments int
	IndexMatches []IndexMatch
}

type Activation struct {
	Kind               CandidateKind
	VersionRef         string
	Scope              ApplicationScope
	QuerySourceRef     string
	MatchedRef         string
	EvidenceEpisodeRef string
	EvidenceSourceRef  string
}

type Ranker interface {
	Score(query, candidate string) float64
}

type Policy struct {
	Ref                             string
	MinimumScore                    float64
	MaxRecollections                int
	MaxDispositions                 int
	AvailabilityCap                 int
	CanonicalCandidatesPerOwnerKind int
}

type rankedActivation struct {
	activation   Activation
	support      float64
	availability int
	ownerOrder   int
	indexed      bool
	indexRank    int
	laneOrder    int
}

type activationPath struct {
	activation Activation
	score      float64
	direct     bool
	indexed    bool
	indexRank  int
	laneOrder  int
}

// Select applies provenance gating, support/inhibition competition and a
// bounded availability tie-break. It deliberately returns no score.
func Select(queries []Query, candidates []Candidate, ranker Ranker, policy Policy) ([]Activation, error) {
	if ranker == nil || policy.Ref == "" || math.IsNaN(policy.MinimumScore) || math.IsInf(policy.MinimumScore, 0) ||
		policy.MinimumScore < 0 || policy.MinimumScore > 1 ||
		policy.MaxRecollections <= 0 || policy.MaxDispositions <= 0 || policy.AvailabilityCap < 0 ||
		policy.CanonicalCandidatesPerOwnerKind <= 0 {
		return nil, ErrInvalidConfiguration
	}

	ranked := make([]rankedActivation, 0, len(candidates))
	for _, candidate := range candidates {
		anchors := liveAnchors(candidate.Support)
		if len(anchors) == 0 || candidate.VersionRef == "" || candidate.Text == "" ||
			(candidate.Kind != CandidateRecollection && candidate.Kind != CandidateDisposition) {
			continue
		}

		path, found := bestSupportPath(queries, candidate, anchors, ranker)
		if !found || (!path.indexed && path.score < policy.MinimumScore) {
			continue
		}
		if candidate.Kind == CandidateDisposition {
			if inhibition, exists := bestInhibition(queries, candidate.Inhibition, ranker); exists && path.score <= inhibition {
				continue
			}
		}

		availability := 0
		if candidate.Kind == CandidateDisposition {
			availability = candidate.Reenactments
			if availability < 0 {
				availability = 0
			}
			if availability > policy.AvailabilityCap {
				availability = policy.AvailabilityCap
			}
		}
		ranked = append(ranked, rankedActivation{
			activation:   path.activation,
			support:      path.score,
			availability: availability,
			ownerOrder:   candidate.OwnerOrder,
			indexed:      path.indexed,
			indexRank:    path.indexRank,
			laneOrder:    path.laneOrder,
		})
	}

	sort.Slice(ranked, func(i, j int) bool {
		if ranked[i].indexed != ranked[j].indexed {
			return ranked[i].indexed
		}
		if ranked[i].indexed {
			if ranked[i].indexRank != ranked[j].indexRank {
				return ranked[i].indexRank < ranked[j].indexRank
			}
			if ranked[i].laneOrder != ranked[j].laneOrder {
				return ranked[i].laneOrder < ranked[j].laneOrder
			}
		}
		if ranked[i].support != ranked[j].support {
			return ranked[i].support > ranked[j].support
		}
		if ranked[i].ownerOrder != ranked[j].ownerOrder {
			return ranked[i].ownerOrder < ranked[j].ownerOrder
		}
		if ranked[i].availability != ranked[j].availability {
			return ranked[i].availability > ranked[j].availability
		}
		if ranked[i].activation.Kind != ranked[j].activation.Kind {
			return ranked[i].activation.Kind < ranked[j].activation.Kind
		}
		return ranked[i].activation.VersionRef < ranked[j].activation.VersionRef
	})
	activations := make([]Activation, 0, policy.MaxRecollections+policy.MaxDispositions)
	recollections, dispositions := 0, 0
	for _, item := range ranked {
		switch item.activation.Kind {
		case CandidateRecollection:
			if recollections >= policy.MaxRecollections {
				continue
			}
			recollections++
		case CandidateDisposition:
			if dispositions >= policy.MaxDispositions {
				continue
			}
			dispositions++
		}
		activations = append(activations, item.activation)
	}
	return activations, nil
}

func bestSupportPath(queries []Query, candidate Candidate, anchors []Anchor, ranker Ranker) (activationPath, bool) {
	evidence := anchors[0]
	var best activationPath
	found := false
	// A direct Memory hit is already the Provider's semantic decision. The same
	// is true when an indexed Episode is canonical support for this exact
	// Disposition. Preserve those paths instead of asking a lexical fallback to
	// rediscover the association. Recollection siblings and Dispositions not
	// supported by the matched Episode do not inherit it. Dispositions retain a
	// local support score so stronger contextual inhibition still wins.
	if candidate.Kind == CandidateRecollection || candidate.Kind == CandidateDisposition {
		queryRefs := make(map[string]Query, len(queries))
		for _, query := range queries {
			if query.Ref != "" && query.Text != "" {
				queryRefs[query.Ref] = query
			}
		}
		for _, match := range candidate.IndexMatches {
			if match.QuerySourceRef == "" || match.Rank < 0 || match.LaneOrder < 0 {
				continue
			}
			query, exists := queryRefs[match.QuerySourceRef]
			if !exists {
				continue
			}
			matchedRef := candidate.VersionRef
			matchedEvidence := evidence
			direct := true
			if match.SupportEpisodeRef != "" {
				if candidate.Kind != CandidateDisposition {
					continue
				}
				foundSupport := false
				for _, anchor := range anchors {
					if anchor.EpisodeRef == match.SupportEpisodeRef {
						matchedEvidence = anchor
						foundSupport = true
						break
					}
				}
				if !foundSupport {
					continue
				}
				matchedRef = match.SupportEpisodeRef
				direct = false
			}
			matchedText := candidate.Text
			if !direct {
				matchedText = matchedEvidence.Text
			}
			path := activationPath{
				indexed:   true,
				score:     normalizeScore(ranker.Score(query.Text, matchedText)),
				indexRank: match.Rank,
				laneOrder: match.LaneOrder,
				direct:    direct,
				activation: Activation{
					Kind:               candidate.Kind,
					VersionRef:         candidate.VersionRef,
					Scope:              candidate.Scope,
					QuerySourceRef:     match.QuerySourceRef,
					MatchedRef:         matchedRef,
					EvidenceEpisodeRef: matchedEvidence.EpisodeRef,
					EvidenceSourceRef:  matchedEvidence.SourceRef,
				},
			}
			if !found || betterPath(path, best) {
				best, found = path, true
			}
		}
	}
	for _, query := range queries {
		if query.Ref == "" || query.Text == "" {
			continue
		}
		direct := activationPath{
			score:  normalizeScore(ranker.Score(query.Text, candidate.Text)),
			direct: true,
			activation: Activation{
				Kind:               candidate.Kind,
				VersionRef:         candidate.VersionRef,
				Scope:              candidate.Scope,
				QuerySourceRef:     query.Ref,
				MatchedRef:         candidate.VersionRef,
				EvidenceEpisodeRef: evidence.EpisodeRef,
				EvidenceSourceRef:  evidence.SourceRef,
			},
		}
		if !found || betterPath(direct, best) {
			best, found = direct, true
		}
		// A Recollection's anchors prove where it came from; they are not an
		// alternate description of every atomic meaning formed in that window.
		// Using them for recall would activate unrelated sibling memories that
		// share the same bounded provenance snapshot.
		if candidate.Kind == CandidateRecollection {
			continue
		}
		for _, anchor := range anchors {
			path := activationPath{
				score: normalizeScore(ranker.Score(query.Text, anchor.Text)),
				activation: Activation{
					Kind:               candidate.Kind,
					VersionRef:         candidate.VersionRef,
					Scope:              candidate.Scope,
					QuerySourceRef:     query.Ref,
					MatchedRef:         anchor.SourceRef,
					EvidenceEpisodeRef: anchor.EpisodeRef,
					EvidenceSourceRef:  anchor.SourceRef,
				},
			}
			if betterPath(path, best) {
				best = path
			}
		}
	}
	return best, found
}

func bestInhibition(queries []Query, anchors []Anchor, ranker Ranker) (float64, bool) {
	best := 0.0
	found := false
	for _, query := range queries {
		if query.Ref == "" || query.Text == "" {
			continue
		}
		for _, anchor := range liveAnchors(anchors) {
			score := normalizeScore(ranker.Score(query.Text, anchor.Text))
			if !found || score > best {
				best, found = score, true
			}
		}
	}
	return best, found
}

func liveAnchors(anchors []Anchor) []Anchor {
	live := make([]Anchor, 0, len(anchors))
	for _, anchor := range anchors {
		if anchor.EpisodeRef != "" && anchor.SourceRef != "" && anchor.Text != "" {
			live = append(live, anchor)
		}
	}
	sort.Slice(live, func(i, j int) bool {
		if live[i].EpisodeRef != live[j].EpisodeRef {
			return live[i].EpisodeRef < live[j].EpisodeRef
		}
		if live[i].SourceRef != live[j].SourceRef {
			return live[i].SourceRef < live[j].SourceRef
		}
		return live[i].Text < live[j].Text
	})
	return live
}

func betterPath(left, right activationPath) bool {
	if left.indexed != right.indexed {
		return left.indexed
	}
	if left.indexed {
		if left.indexRank != right.indexRank {
			return left.indexRank < right.indexRank
		}
		if left.laneOrder != right.laneOrder {
			return left.laneOrder < right.laneOrder
		}
	}
	if left.score != right.score {
		return left.score > right.score
	}
	if left.direct != right.direct {
		return left.direct
	}
	l, r := left.activation, right.activation
	if l.QuerySourceRef != r.QuerySourceRef {
		return l.QuerySourceRef < r.QuerySourceRef
	}
	if l.MatchedRef != r.MatchedRef {
		return l.MatchedRef < r.MatchedRef
	}
	if l.EvidenceEpisodeRef != r.EvidenceEpisodeRef {
		return l.EvidenceEpisodeRef < r.EvidenceEpisodeRef
	}
	return l.EvidenceSourceRef < r.EvidenceSourceRef
}

func normalizeScore(score float64) float64 {
	if math.IsNaN(score) {
		return 0
	}
	if score < 0 {
		return 0
	}
	if score > 1 {
		return 1
	}
	return score
}
