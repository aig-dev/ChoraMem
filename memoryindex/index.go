// Package memoryindex defines the provider-neutral port for Memory Core's
// optional, disposable semantic candidate projection. The selected relational
// CoreStore remains authoritative for every fact and eligibility decision.
package memoryindex

import (
	"context"
	"errors"
)

// Kind identifies the only canonical document kinds accepted by the index.
type Kind string

const (
	KindEpisode      Kind = "EPISODE"
	KindRecollection Kind = "RECOLLECTION"
	KindDisposition  Kind = "DISPOSITION"
)

// Scope identifies one long-lived owner lane. An empty RelationshipRef is the
// Agent baseline; a non-empty RelationshipRef is that exact relationship.
type Scope struct {
	TenantRef       string
	AgentRef        string
	RelationshipRef string
}

// Document is the complete provider-neutral projection of one canonical item.
type Document struct {
	Kind  Kind
	Ref   string
	Scope Scope
	Text  string
}

// Query asks a provider for an ordered, bounded candidate set in one owner lane.
type Query struct {
	Scope Scope
	Text  string
	Limit int
}

// Candidate contains no similarity score or write authority.
type Candidate struct {
	Kind Kind
	Ref  string
}

// Index is implemented by any semantic provider. Search output is untrusted and
// must be rehydrated from the configured relational CoreStore before it can
// affect selection or consolidation.
type Index interface {
	Search(context.Context, Query) ([]Candidate, error)
	Upsert(context.Context, []Document) error
	Delete(context.Context, Scope, []Candidate) error
	Reset(context.Context) error
}

var ErrUnavailable = errors.New("MemoryIndex projection is unavailable")
