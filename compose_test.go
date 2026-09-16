package memorycore_test

import (
	"os"
	"strings"
	"testing"
)

func TestComposeOwnsOnlyTheStandalonePostgresProfile(t *testing.T) {
	standalone := readCompose(t, "compose.yaml")
	if !strings.Contains(standalone, "\n  postgres:\n") ||
		!strings.Contains(standalone, "MEMORYD_DATABASE_DRIVER: postgres") {
		t.Fatal("standalone compose must own PostgreSQL and select the PostgreSQL Adapter")
	}

	for _, productSpecific := range []string{"chorai", "MEMORY_MYSQL", "chorai_memory_core"} {
		if strings.Contains(standalone, productSpecific) {
			t.Fatalf("standalone compose contains product-specific value %q", productSpecific)
		}
	}
}

func readCompose(t *testing.T, path string) string {
	t.Helper()
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return string(content)
}
