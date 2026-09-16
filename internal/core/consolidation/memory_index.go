package consolidation

import (
	"context"
	"sort"
	"strings"
	"unicode"

	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/memoryindex"
)

// MemoryIndexCandidates is one transient, bounded semantic candidate set.
// Every ref remains untrusted until the selected CoreStore rehydrates it.
type MemoryIndexCandidates struct {
	EpisodeRefs      []string
	RecollectionRefs []string
	DispositionRefs  []string
}

// SearchMemoryIndex queries each unique non-empty Situation once. Provider
// failures are local to that query, and both each response and each kind are
// capped even when a Provider ignores Query.Limit.
func SearchMemoryIndex(
	ctx context.Context,
	index memoryindex.Index,
	scope memoryindex.Scope,
	situationTexts []string,
	limit int,
) MemoryIndexCandidates {
	if index == nil || limit <= 0 {
		return MemoryIndexCandidates{}
	}
	result := MemoryIndexCandidates{}
	seenQueries := make(map[string]struct{}, len(situationTexts))
	seenRefs := map[memoryindex.Kind]map[string]struct{}{
		memoryindex.KindEpisode:      {},
		memoryindex.KindRecollection: {},
		memoryindex.KindDisposition:  {},
	}
	for _, rawText := range situationTexts {
		queryText := strings.TrimSpace(rawText)
		if queryText == "" {
			continue
		}
		if _, duplicate := seenQueries[queryText]; duplicate {
			continue
		}
		seenQueries[queryText] = struct{}{}
		candidates, err := index.Search(ctx, memoryindex.Query{Scope: scope, Text: queryText, Limit: limit})
		if err != nil {
			continue
		}
		if len(candidates) > limit {
			candidates = candidates[:limit]
		}
		for _, candidate := range candidates {
			seen := seenRefs[candidate.Kind]
			if seen == nil || !validMemoryIndexRef(candidate.Ref) {
				continue
			}
			if _, duplicate := seen[candidate.Ref]; duplicate {
				continue
			}
			seen[candidate.Ref] = struct{}{}
			switch candidate.Kind {
			case memoryindex.KindEpisode:
				if len(result.EpisodeRefs) < limit {
					result.EpisodeRefs = append(result.EpisodeRefs, candidate.Ref)
				}
			case memoryindex.KindRecollection:
				if len(result.RecollectionRefs) < limit {
					result.RecollectionRefs = append(result.RecollectionRefs, candidate.Ref)
				}
			case memoryindex.KindDisposition:
				if len(result.DispositionRefs) < limit {
					result.DispositionRefs = append(result.DispositionRefs, candidate.Ref)
				}
			}
		}
	}
	return result
}

// FuseEpisodeCandidateRanks combines provider order from multiple semantic
// views without accepting or persisting provider scores. Missing candidates
// receive a fixed rank after the requested bound, so a candidate consistently
// near the front of the views wins over a one-view accidental neighbor.
func FuseEpisodeCandidateRanks(
	limit int,
	rankings ...[]memoryindex.Candidate,
) []string {
	if limit <= 0 || len(rankings) == 0 {
		return nil
	}
	type rankedEpisode struct {
		ref        string
		ranks      []int
		firstOrder int
	}
	missingRank := limit + 1
	byRef := make(map[string]*rankedEpisode)
	firstOrder := 0
	for rankingIndex, raw := range rankings {
		if len(raw) > limit {
			raw = raw[:limit]
		}
		seen := make(map[string]struct{}, len(raw))
		episodeRank := 0
		for _, candidate := range raw {
			if candidate.Kind != memoryindex.KindEpisode || !validMemoryIndexRef(candidate.Ref) {
				continue
			}
			if _, duplicate := seen[candidate.Ref]; duplicate {
				continue
			}
			seen[candidate.Ref] = struct{}{}
			episodeRank++
			ranked := byRef[candidate.Ref]
			if ranked == nil {
				ranked = &rankedEpisode{
					ref: candidate.Ref, ranks: make([]int, len(rankings)), firstOrder: firstOrder,
				}
				for index := range ranked.ranks {
					ranked.ranks[index] = missingRank
				}
				byRef[candidate.Ref] = ranked
				firstOrder++
			}
			ranked.ranks[rankingIndex] = episodeRank
		}
	}
	ordered := make([]*rankedEpisode, 0, len(byRef))
	for _, ranked := range byRef {
		ordered = append(ordered, ranked)
	}
	rankStats := func(ranked *rankedEpisode) (sum int, worst int) {
		for _, rank := range ranked.ranks {
			sum += rank
			if rank > worst {
				worst = rank
			}
		}
		return sum, worst
	}
	sort.Slice(ordered, func(left, right int) bool {
		leftSum, leftWorst := rankStats(ordered[left])
		rightSum, rightWorst := rankStats(ordered[right])
		if leftSum != rightSum {
			return leftSum < rightSum
		}
		if leftWorst != rightWorst {
			return leftWorst < rightWorst
		}
		if ordered[left].ranks[0] != ordered[right].ranks[0] {
			return ordered[left].ranks[0] < ordered[right].ranks[0]
		}
		if ordered[left].firstOrder != ordered[right].firstOrder {
			return ordered[left].firstOrder < ordered[right].firstOrder
		}
		return ordered[left].ref < ordered[right].ref
	})
	if len(ordered) > limit {
		ordered = ordered[:limit]
	}
	refs := make([]string, 0, len(ordered))
	for _, ranked := range ordered {
		refs = append(refs, ranked.ref)
	}
	return refs
}

func validMemoryIndexRef(ref string) bool {
	return ledger.ValidStableRef(ref) && strings.IndexFunc(ref, unicode.IsSpace) == -1
}
