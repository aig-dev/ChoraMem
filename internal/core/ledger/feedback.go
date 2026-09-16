package ledger

import (
	"errors"
	"fmt"
	"strings"
	"unicode"
)

var (
	ErrInvalidMemoryDelivery  = errors.New("invalid memory delivery")
	ErrMemoryDeliveryConflict = errors.New("memory delivery key already identifies a different request")
	ErrInvalidOutcomeReport   = errors.New("invalid outcome report")
	ErrOutcomeReportConflict  = errors.New("outcome key already identifies a different request")
)

// MemoryDelivery states only which stable memories a Harness can prove were
// exposed. It does not claim that the model used them.
type MemoryDelivery struct {
	IdempotencyKey      string
	Scope               Scope
	RunRef              string
	MemoryContextRef    string
	DeliveredMemoryRefs []string
}

type MemoryDeliveryReceipt struct {
	Ref string
}

// OutcomeReport is an observed source-bound result, never a reinforcement
// instruction. Delivery attribution remains optional.
type OutcomeReport struct {
	IdempotencyKey         string
	Event                  SourceEvent
	RunRef                 string
	SourceGroupRef         string
	DeliveryReceiptRefs    []string
	RelatedSourceEventRefs []string
}

type OutcomeReceipt struct {
	OutcomeEventRef string
	EpisodeRef      string
}

func ValidateMemoryDelivery(delivery MemoryDelivery) error {
	if !ValidStableRef(delivery.IdempotencyKey) ||
		!stableFeedbackRef(delivery.RunRef) ||
		!stableFeedbackRef(delivery.MemoryContextRef) ||
		len(delivery.DeliveredMemoryRefs) == 0 ||
		!uniqueFeedbackRefs(delivery.DeliveredMemoryRefs) ||
		!validScope(delivery.Scope) {
		return ErrInvalidMemoryDelivery
	}
	return nil
}

func ValidateOutcomeReport(report OutcomeReport) error {
	if !ValidStableRef(report.IdempotencyKey) ||
		!stableFeedbackRef(report.RunRef) ||
		!stableFeedbackRef(report.SourceGroupRef) ||
		!uniqueFeedbackRefs(report.DeliveryReceiptRefs) ||
		!uniqueFeedbackRefs(report.RelatedSourceEventRefs) {
		return ErrInvalidOutcomeReport
	}
	if err := Validate(report.Event, EpisodeBinding{
		RunRef: report.RunRef, SourceGroupRef: report.SourceGroupRef, Role: RoleOutcome,
	}); err != nil {
		return fmt.Errorf("%w: %v", ErrInvalidOutcomeReport, err)
	}
	return nil
}

func validScope(scope Scope) bool {
	if !ValidStableRef(scope.TenantRef) || !ValidStableRef(scope.AgentRef) ||
		!validOptionalStableRef(scope.RelationshipRef) || !validOptionalStableRef(scope.SessionRef) {
		return false
	}
	switch scope.Kind {
	case ScopeKindAgent:
		return scope.RelationshipRef == ""
	case ScopeKindRelationship:
		return scope.RelationshipRef != ""
	default:
		return false
	}
}

func uniqueFeedbackRefs(refs []string) bool {
	seen := make(map[string]struct{}, len(refs))
	for _, ref := range refs {
		if !stableFeedbackRef(ref) {
			return false
		}
		if _, duplicate := seen[ref]; duplicate {
			return false
		}
		seen[ref] = struct{}{}
	}
	return true
}

func stableFeedbackRef(ref string) bool {
	return ValidStableRef(ref) && strings.IndexFunc(ref, unicode.IsSpace) == -1
}
