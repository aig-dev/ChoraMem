package consolidation

import (
	"strconv"
	"strings"
)

const (
	EvidenceOriginCurrent        = "current"
	EvidenceOriginRelated        = "related"
	EvidenceOriginRevisionAnchor = "revision_anchor"
	EvidenceFormationAnchor      = "anchor"
	EvidenceFormationCandidate   = "candidate"
)

// WindowEvidence is the typed, immutable-by-convention source snapshot paired
// with the legacy rendered window. It carries no storage or write authority.
type WindowEvidence struct {
	Episodes      []EvidenceEpisode
	Outcomes      []EvidenceOutcome
	Recollections []EvidenceRecollection
}

type EvidenceEpisode struct {
	EpisodeRef    string
	SessionRef    string
	Origin        string
	FormationRole string
	Sources       []EvidenceSource
}

// HasDispositionFormationAnchor reports whether Core selected exactly one
// current causal pair as the focused formation anchor. It deliberately says
// nothing about whether enough historical evidence exists to form a Seed.
func HasDispositionFormationAnchor(evidence *WindowEvidence) bool {
	if evidence == nil {
		return false
	}
	anchors := 0
	for _, episode := range evidence.Episodes {
		if episode.FormationRole == EvidenceFormationAnchor {
			anchors++
		}
	}
	return anchors == 1
}

// DispositionFormationBasis returns the exact Episode/Outcome refs represented
// by the focused model input. The anchor is first, followed by candidates in
// evidence order, and every causal pair remains adjacent.
func DispositionFormationBasis(evidence *WindowEvidence) ([]string, bool) {
	if evidence == nil {
		return nil, false
	}
	var anchor *EvidenceEpisode
	candidates := make([]EvidenceEpisode, 0)
	seenEpisodes := make(map[string]struct{})
	seenSessions := make(map[string]struct{})
	for index := range evidence.Episodes {
		episode := evidence.Episodes[index]
		if episode.FormationRole == "" {
			continue
		}
		if episode.FormationRole != EvidenceFormationAnchor && episode.FormationRole != EvidenceFormationCandidate {
			return nil, false
		}
		if strings.TrimSpace(episode.EpisodeRef) == "" || strings.TrimSpace(episode.SessionRef) == "" {
			return nil, false
		}
		if _, duplicate := seenEpisodes[episode.EpisodeRef]; duplicate {
			return nil, false
		}
		if _, duplicate := seenSessions[episode.SessionRef]; duplicate {
			return nil, false
		}
		seenEpisodes[episode.EpisodeRef] = struct{}{}
		seenSessions[episode.SessionRef] = struct{}{}
		if episode.FormationRole == EvidenceFormationAnchor {
			if anchor != nil || episode.Origin != EvidenceOriginCurrent {
				return nil, false
			}
			copy := episode
			anchor = &copy
			continue
		}
		if episode.Origin != EvidenceOriginCurrent && episode.Origin != EvidenceOriginRelated {
			return nil, false
		}
		candidates = append(candidates, episode)
	}
	if anchor == nil || len(candidates) == 0 {
		return nil, false
	}

	outcomes := make(map[string]EvidenceOutcome, len(evidence.Outcomes))
	seenOutcomes := make(map[string]struct{})
	for _, outcome := range evidence.Outcomes {
		if _, relevant := seenEpisodes[outcome.EpisodeRef]; !relevant || outcome.ActorKind == "agent" {
			continue
		}
		if strings.TrimSpace(outcome.OutcomeRef) == "" || strings.TrimSpace(outcome.ActorKind) == "" || strings.TrimSpace(outcome.Text) == "" {
			return nil, false
		}
		if _, duplicate := outcomes[outcome.EpisodeRef]; duplicate {
			return nil, false
		}
		if _, duplicate := seenOutcomes[outcome.OutcomeRef]; duplicate {
			return nil, false
		}
		outcomes[outcome.EpisodeRef] = outcome
		seenOutcomes[outcome.OutcomeRef] = struct{}{}
	}

	ordered := make([]EvidenceEpisode, 0, len(candidates)+1)
	ordered = append(ordered, *anchor)
	ordered = append(ordered, candidates...)
	basis := make([]string, 0, len(ordered)*2)
	for _, episode := range ordered {
		outcome, exists := outcomes[episode.EpisodeRef]
		if !exists {
			return nil, false
		}
		basis = append(basis, episode.EpisodeRef, outcome.OutcomeRef)
	}
	return basis, true
}

type EvidenceSource struct {
	SourceRef string
	Role      string
	ActorKind string
	ActorRef  string
	Text      string
}

type EvidenceOutcome struct {
	OutcomeRef string
	EpisodeRef string
	ActorKind  string
	ActorRef   string
	Text       string
}

// EvidenceRecollection is a comparison snapshot for background semantic
// deduplication. It grants no target or write authority by itself.
type EvidenceRecollection struct {
	VersionRef  string
	Application string
	Text        string
}

// AssembleWindowEvidence preserves the existing source order while applying
// the current > related > revision-anchor precedence to duplicate Episodes.
func AssembleWindowEvidence(
	current []EvidenceEpisode,
	related []EvidenceEpisode,
	revisionAnchors []EvidenceEpisode,
	outcomes []EvidenceOutcome,
) *WindowEvidence {
	evidence := &WindowEvidence{Outcomes: append([]EvidenceOutcome(nil), outcomes...)}
	seen := make(map[string]struct{}, len(current)+len(related)+len(revisionAnchors))
	appendEpisodes := func(origin string, episodes []EvidenceEpisode) {
		for _, episode := range episodes {
			if _, duplicate := seen[episode.EpisodeRef]; duplicate {
				continue
			}
			seen[episode.EpisodeRef] = struct{}{}
			copy := episode
			copy.Origin = origin
			copy.Sources = append([]EvidenceSource(nil), episode.Sources...)
			evidence.Episodes = append(evidence.Episodes, copy)
		}
	}
	appendEpisodes(EvidenceOriginCurrent, current)
	appendEpisodes(EvidenceOriginRelated, related)
	appendEpisodes(EvidenceOriginRevisionAnchor, revisionAnchors)
	return evidence
}

// AppendWorkerEvidenceHashFields appends a structural, order-preserving
// representation suitable for the adapters' length-delimited snapshot hash.
func AppendWorkerEvidenceHashFields(fields []string, evidence *WindowEvidence) []string {
	if evidence == nil {
		return append(fields, "evidence-absent")
	}
	fields = append(fields, "evidence-present", "episodes", strconv.Itoa(len(evidence.Episodes)))
	for _, episode := range evidence.Episodes {
		fields = append(fields,
			"episode", episode.EpisodeRef, episode.SessionRef, episode.Origin, episode.FormationRole,
			"sources", strconv.Itoa(len(episode.Sources)),
		)
		for _, source := range episode.Sources {
			fields = append(fields,
				"source", source.SourceRef, source.Role, source.ActorKind, source.ActorRef, source.Text,
			)
		}
	}
	fields = append(fields, "outcomes", strconv.Itoa(len(evidence.Outcomes)))
	for _, outcome := range evidence.Outcomes {
		fields = append(fields,
			"outcome", outcome.OutcomeRef, outcome.EpisodeRef,
			outcome.ActorKind, outcome.ActorRef, outcome.Text,
		)
	}
	fields = append(fields, "recollections", strconv.Itoa(len(evidence.Recollections)))
	for _, recollection := range evidence.Recollections {
		fields = append(fields,
			"recollection", recollection.VersionRef, recollection.Application, recollection.Text,
		)
	}
	return fields
}
