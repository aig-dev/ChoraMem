package contracttest

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/aig-dev/ChoraMem/memoryindex"
)

// Implemented only by database test wrappers; no inspection API enters CoreStore.
type adaptiveStoreProbe interface {
	AdaptiveJobUpdatedAt(*testing.T) time.Time
	AdaptiveCounts(*testing.T, string, string, string) map[string]int
	AdaptiveFormationOutcomeCount(*testing.T, string, string) int
}

type unavailableAdaptiveIndex struct{ scriptedIndex }

func (*unavailableAdaptiveIndex) Search(context.Context, memoryindex.Query) ([]memoryindex.Candidate, error) {
	return nil, errors.New("disposable index unavailable")
}
