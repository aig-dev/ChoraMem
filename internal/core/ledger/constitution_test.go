package ledger

import (
	"errors"
	"strings"
	"testing"
)

func TestConstitutionRejectsIncompleteOrInvalidSnapshot(t *testing.T) {
	for _, value := range []Constitution{{MemoryRef: "v1"}, {Text: "role"}, {MemoryRef: "bad ref", Text: "role"}, {MemoryRef: "v1", Text: strings.Repeat("x", MaxSourceTextBytes+1)}} {
		event := source("source")
		event.Constitution = value
		if err := Validate(event, EpisodeBinding{}); !errors.Is(err, ErrInvalidSourceEvent) {
			t.Errorf("accepted invalid snapshot ref=%q bytes=%d: %v", value.MemoryRef, len(value.Text), err)
		}
	}
}

func TestConstitutionRejectsNULInBothSnapshotFields(t *testing.T) {
	for _, value := range []Constitution{{MemoryRef: "v\x001", Text: "role"}, {MemoryRef: "v1", Text: "a\x00b"}} {
		event := source("source")
		event.Constitution = value
		if err := Validate(event, EpisodeBinding{}); !errors.Is(err, ErrInvalidSourceEvent) {
			t.Errorf("accepted NUL snapshot %#v: %v", value, err)
		}
	}
}
