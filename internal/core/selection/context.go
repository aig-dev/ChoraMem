package selection

import (
	"errors"

	"github.com/aig-dev/ChoraMem/internal/core/ledger"
)

var (
	ErrInvalidSelectRequest = errors.New("invalid SelectMemory request")
	ErrRunConflict          = errors.New("run ref already identifies a different MemoryContext request")
	ErrAmbiguousMemoryRef   = errors.New("memory ref resolves to multiple semantic kinds")
)

// Constitution is an external normative baseline, not writable Memory.
type Constitution = ledger.Constitution

type SelectRequest struct {
	Scope                   ledger.Scope
	RunRef                  string
	SituationSourceRefs     []string
	Constitution            Constitution
	EpisodeEvidenceMaxBytes int
}

type ApplicationScope string

const (
	ApplicationScopeSelf      ApplicationScope = "self"
	ApplicationScopeOther     ApplicationScope = "other"
	ApplicationScopeRelation  ApplicationScope = "relation"
	ApplicationScopeSituation ApplicationScope = "situation"
)

type Recollection struct {
	MemoryRef   string
	Text        string
	Application ApplicationScope
}

type Disposition struct {
	MemoryRef   string
	Text        string
	Application ApplicationScope
}

// EpisodeEvidence is frozen canonical historical source text, not learned Memory.
type EpisodeEvidence struct {
	MemoryRef string
	Text      string
}

type MemoryContext struct {
	Ref             string
	RunRef          string
	Scope           ledger.Scope
	Constitution    Constitution
	Recollections   []Recollection
	Dispositions    []Disposition
	EpisodeEvidence []EpisodeEvidence
}
