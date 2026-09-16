package consolidation

import (
	"fmt"
	"strings"

	"github.com/aig-dev/ChoraMem/internal/core/ledger"
)

// RenderConstitution presents one external snapshot as readonly context, never
// evidence. Adapters select the effective snapshot using canonical admission order.
func RenderConstitution(constitution ledger.Constitution) string {
	if constitution == (ledger.Constitution{}) {
		return "CONSTITUTION\nUNKNOWN\n\n"
	}
	// Prefix every line, including trailing empty lines, so external text cannot
	// manufacture structural markers. Removing one prefix recovers exact text.
	quoted := "> " + strings.ReplaceAll(constitution.Text, "\n", "\n> ")
	return fmt.Sprintf("CONSTITUTION\nMEMORY_REF %s\n%s\nEND_CONSTITUTION\n\n", constitution.MemoryRef, quoted)
}

// ConstitutionRefCollision detects a baseline ref also offered as a writable
// Target or evidence Basis. Core freezes such ambiguous windows as a no-op.
func ConstitutionRefCollision(constitution ledger.Constitution, request WorkerRequest) bool {
	ref := constitution.MemoryRef
	if ref == "" {
		return false
	}
	for _, allowed := range request.AllowedTargetRefs {
		if ref == allowed {
			return true
		}
	}
	for _, allowed := range request.AllowedBasisRefs {
		if ref == allowed {
			return true
		}
	}
	return false
}
