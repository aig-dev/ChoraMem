package ledger

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"unicode"
	"unicode/utf8"
)

var (
	ErrInvalidSourceEvent    = errors.New("invalid source event")
	ErrInvalidEpisodeBinding = errors.New("invalid episode binding")
	ErrSourceEventConflict   = errors.New("source event ref already identifies different immutable input")
	ErrIdempotencyConflict   = errors.New("idempotency key already identifies different request")
	ErrEpisodeSealed         = errors.New("materialized episode only accepts existing links or new outcomes")
)

// MaxSourceTextBytes bounds one immutable observation before it can poison
// every later overlap window. It is an operational byte limit, not a semantic
// confidence or importance score.
const MaxSourceTextBytes = 64 * 1024

// MaxStableRefBytes is the shared PostgreSQL/MySQL acceptance boundary for
// every persisted identity and idempotency reference. It is a storage
// interoperability limit, not a semantic weight.
const MaxStableRefBytes = 384

// ValidStableRef reports whether a required reference is safe and identical
// across every relational adapter.
func ValidStableRef(ref string) bool {
	return ref != "" && len(ref) <= MaxStableRefBytes && utf8.ValidString(ref)
}

func validOptionalStableRef(ref string) bool {
	return ref == "" || ValidStableRef(ref)
}

// Scope is the exact authorization boundary shared by an Episode and all of
// its SourceEvents. SessionRef may be empty, but when present is part of the
// exact boundary and Episode identity.
type ScopeKind string

const (
	ScopeKindAgent        ScopeKind = "agent"
	ScopeKindRelationship ScopeKind = "relationship"
)

type Scope struct {
	Kind            ScopeKind
	TenantRef       string
	AgentRef        string
	RelationshipRef string
	SessionRef      string
}

// SourceRole states how a SourceEvent is linked to an Episode.
type SourceRole string

const (
	RoleSituation SourceRole = "situation"
	RoleAgentAct  SourceRole = "agent_act"
	RoleOutcome   SourceRole = "outcome"
)

// SourceEvent is an immutable input value within its exact Scope. Episode
// membership and role are separate relations, not properties of the source.
type SourceEvent struct {
	Ref          string
	Scope        Scope
	ActorKind    ActorKind
	ActorRef     string
	Text         string
	Constitution Constitution
}

// Constitution is an immutable external normative snapshot, never writable Memory.
// The zero value represents an unknown baseline.
type Constitution struct {
	MemoryRef string
	Text      string
}

// ActorKind is trusted provenance supplied after transport authentication.
type ActorKind string

const (
	ActorKindAgent    ActorKind = "agent"
	ActorKindUser     ActorKind = "user"
	ActorKindSystem   ActorKind = "system"
	ActorKindTool     ActorKind = "tool"
	ActorKindExternal ActorKind = "external"
)

// EpisodeBinding requests one typed provenance link in a stable run/source
// group. An incomplete binding admits the SourceEvent without guessing a
// boundary and can be retried later with all fields present.
type EpisodeBinding struct {
	RunRef         string
	SourceGroupRef string
	Role           SourceRole
}

// SourceLink is a typed provenance edge from an Episode to a SourceEvent.
type SourceLink struct {
	SourceEventRef string
	Role           SourceRole
}

// Episode is the deterministic envelope for one stable run/source group.
// Links are returned in role then source-ref order.
type Episode struct {
	Ref            string
	Scope          Scope
	RunRef         string
	SourceGroupRef string
	Links          []SourceLink
}

// ObserveReceipt is the immutable result frozen by one transport
// idempotency key. EpisodeRef is empty until that request materializes or
// appends to an already materialized Episode.
type ObserveReceipt struct {
	SourceEventRef string
	EpisodeRef     string
}

// Ledger runs after transport request-idempotency. It admits immutable
// SourceEvents and materializes Episodes once distinct sources provide a
// situation and an actual agent act. It deliberately contains no semantic or
// time-based boundary inference.
type Ledger struct {
	mu sync.Mutex

	events map[scopedSourceRef]SourceEvent
	groups map[stableGroupRef]*episodeGroup
}

type scopedSourceRef struct {
	scope Scope
	ref   string
}

type stableGroupRef struct {
	scope          Scope
	runRef         string
	sourceGroupRef string
}

type episodeGroup struct {
	links map[SourceLink]struct{}
}

func New() *Ledger {
	return &Ledger{
		events: make(map[scopedSourceRef]SourceEvent),
		groups: make(map[stableGroupRef]*episodeGroup),
	}
}

// Observe admits event, then appends binding when it is complete. Reusing one
// immutable source with a different binding is a separate relation command,
// not a retry of the transport request. Complete invalid bindings fail before
// either the source or relation is mutated.
func (l *Ledger) Observe(event SourceEvent, binding EpisodeBinding) (Episode, bool, error) {
	if err := Validate(event, binding); err != nil {
		return Episode{}, false, err
	}
	groupRef, grouped, _ := groupOf(event, binding)

	l.mu.Lock()
	defer l.mu.Unlock()

	eventRef := scopedSourceRef{scope: event.Scope, ref: event.Ref}
	_, eventExists := l.events[eventRef]
	if existing, ok := l.events[eventRef]; ok {
		if existing != event {
			return Episode{}, false, fmt.Errorf("%w: %s", ErrSourceEventConflict, event.Ref)
		}
	}

	var group *episodeGroup
	link := SourceLink{SourceEventRef: event.Ref, Role: binding.Role}
	if grouped {
		group = l.groups[groupRef]
		if group != nil {
			if _, exists := group.links[link]; !exists {
				if _, materialized := snapshot(groupRef, group); materialized && binding.Role != RoleOutcome {
					return Episode{}, false, ErrEpisodeSealed
				}
			}
		}
	}
	if !eventExists {
		l.events[eventRef] = event
	}
	if !grouped {
		return Episode{}, false, nil
	}

	if group == nil {
		group = &episodeGroup{links: make(map[SourceLink]struct{})}
		l.groups[groupRef] = group
	}
	group.links[link] = struct{}{}

	episode, materialized := snapshot(groupRef, group)
	return episode, materialized, nil
}

func snapshot(ref stableGroupRef, group *episodeGroup) (Episode, bool) {
	situations := make(map[string]struct{})
	agentActs := make(map[string]struct{})
	links := make([]SourceLink, 0, len(group.links))
	for link := range group.links {
		switch link.Role {
		case RoleSituation:
			situations[link.SourceEventRef] = struct{}{}
		case RoleAgentAct:
			agentActs[link.SourceEventRef] = struct{}{}
		}
		links = append(links, link)
	}
	if !hasDistinctSituationAndAgentAct(situations, agentActs) {
		return Episode{}, false
	}

	sort.Slice(links, func(i, j int) bool {
		left, right := roleOrder(links[i].Role), roleOrder(links[j].Role)
		if left != right {
			return left < right
		}
		return links[i].SourceEventRef < links[j].SourceEventRef
	})

	return Episode{
		Ref:            EpisodeRef(ref.scope, ref.runRef, ref.sourceGroupRef),
		Scope:          ref.scope,
		RunRef:         ref.runRef,
		SourceGroupRef: ref.sourceGroupRef,
		Links:          links,
	}, true
}

// Validate deterministically validates one causal intake command without
// reading or mutating storage.
func Validate(event SourceEvent, binding EpisodeBinding) error {
	if err := validateSourceEvent(event); err != nil {
		return err
	}
	_, _, err := groupOf(event, binding)
	return err
}

func validateSourceEvent(event SourceEvent) error {
	if strings.ContainsRune(event.Constitution.MemoryRef, '\x00') || strings.ContainsRune(event.Constitution.Text, '\x00') {
		return fmt.Errorf("%w: Constitution fields cannot contain NUL", ErrInvalidSourceEvent)
	}
	if (event.Constitution.MemoryRef == "") != (event.Constitution.Text == "") ||
		(event.Constitution.MemoryRef != "" && (!ValidStableRef(event.Constitution.MemoryRef) || strings.IndexFunc(event.Constitution.MemoryRef, unicode.IsSpace) != -1)) ||
		len(event.Constitution.Text) > MaxSourceTextBytes {
		return fmt.Errorf("%w: Constitution requires a stable ref and text within %d bytes, or neither", ErrInvalidSourceEvent, MaxSourceTextBytes)
	}
	if !ValidStableRef(event.Ref) {
		return fmt.Errorf("%w: source event ref is invalid", ErrInvalidSourceEvent)
	}
	if !ValidStableRef(event.Scope.TenantRef) || !ValidStableRef(event.Scope.AgentRef) ||
		!validOptionalStableRef(event.Scope.RelationshipRef) || !validOptionalStableRef(event.Scope.SessionRef) {
		return fmt.Errorf("%w: scope refs are invalid", ErrInvalidSourceEvent)
	}
	switch event.Scope.Kind {
	case ScopeKindAgent:
		if event.Scope.RelationshipRef != "" {
			return fmt.Errorf("%w: agent scope cannot carry relationship ref", ErrInvalidSourceEvent)
		}
	case ScopeKindRelationship:
		if event.Scope.RelationshipRef == "" {
			return fmt.Errorf("%w: relationship scope requires relationship ref", ErrInvalidSourceEvent)
		}
	default:
		return fmt.Errorf("%w: unknown scope kind %q", ErrInvalidSourceEvent, event.Scope.Kind)
	}
	if !validActorKind(event.ActorKind) || !ValidStableRef(event.ActorRef) {
		return fmt.Errorf("%w: known actor kind and actor ref are required", ErrInvalidSourceEvent)
	}
	if event.Text == "" {
		return fmt.Errorf("%w: text is required", ErrInvalidSourceEvent)
	}
	if len(event.Text) > MaxSourceTextBytes {
		return fmt.Errorf("%w: text exceeds %d bytes", ErrInvalidSourceEvent, MaxSourceTextBytes)
	}
	return nil
}

func groupOf(event SourceEvent, binding EpisodeBinding) (stableGroupRef, bool, error) {
	if binding.RunRef == "" || binding.SourceGroupRef == "" {
		if !validOptionalStableRef(binding.RunRef) || !validOptionalStableRef(binding.SourceGroupRef) {
			return stableGroupRef{}, false, fmt.Errorf("%w: run and source group refs are invalid", ErrInvalidEpisodeBinding)
		}
		return stableGroupRef{}, false, nil
	}
	if !ValidStableRef(binding.RunRef) || !ValidStableRef(binding.SourceGroupRef) {
		return stableGroupRef{}, false, fmt.Errorf("%w: run and source group refs are invalid", ErrInvalidEpisodeBinding)
	}
	if !validRole(binding.Role) {
		return stableGroupRef{}, false, fmt.Errorf("%w: unknown source role %q", ErrInvalidEpisodeBinding, binding.Role)
	}
	if binding.Role == RoleAgentAct && (event.ActorKind != ActorKindAgent || event.ActorRef != event.Scope.AgentRef) {
		return stableGroupRef{}, false, fmt.Errorf("%w: agent_act requires the scoped agent actor", ErrInvalidEpisodeBinding)
	}
	return stableGroupRef{scope: event.Scope, runRef: binding.RunRef, sourceGroupRef: binding.SourceGroupRef}, true, nil
}

func validActorKind(kind ActorKind) bool {
	switch kind {
	case ActorKindAgent, ActorKindUser, ActorKindSystem, ActorKindTool, ActorKindExternal:
		return true
	default:
		return false
	}
}

func validRole(role SourceRole) bool {
	switch role {
	case RoleSituation, RoleAgentAct, RoleOutcome:
		return true
	default:
		return false
	}
}

func roleOrder(role SourceRole) int {
	switch role {
	case RoleSituation:
		return 0
	case RoleAgentAct:
		return 1
	case RoleOutcome:
		return 2
	default:
		return 3
	}
}

func hasDistinctSituationAndAgentAct(situations, agentActs map[string]struct{}) bool {
	for situationRef := range situations {
		for agentActRef := range agentActs {
			if situationRef != agentActRef {
				return true
			}
		}
	}
	return false
}

// EpisodeRef derives the stable identity of one exact scope/run/source group.
func EpisodeRef(scope Scope, runRef, sourceGroupRef string) string {
	hash := sha256.New()
	for _, value := range []string{
		"episode.v1",
		string(scope.Kind),
		scope.TenantRef,
		scope.AgentRef,
		scope.RelationshipRef,
		scope.SessionRef,
		runRef,
		sourceGroupRef,
	} {
		var length [8]byte
		binary.BigEndian.PutUint64(length[:], uint64(len(value)))
		_, _ = hash.Write(length[:])
		_, _ = hash.Write([]byte(value))
	}
	return "episode_" + hex.EncodeToString(hash.Sum(nil))
}
