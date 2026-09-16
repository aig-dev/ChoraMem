package postgres

import (
	"os"
	"strings"
	"testing"
)

func TestActiveMemoryReadDeliveryAndFeedbackSourcesDoNotUseOldTables(t *testing.T) {
	activeSources := []string{"memory_context.go", "feedback.go", "feedback_consolidation.go"}
	forbidden := []string{"persona_contexts", "seed_activations", "context_delivery_receipts"}
	for _, path := range activeSources {
		source, err := os.ReadFile(path)
		if err != nil {
			t.Fatalf("read active Memory source %s: %v", path, err)
		}
		for _, table := range forbidden {
			if strings.Contains(string(source), table) {
				t.Fatalf("active Memory source %s still references dormant table %s", path, table)
			}
		}
	}
}
