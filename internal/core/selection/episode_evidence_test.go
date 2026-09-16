package selection

import (
	"reflect"
	"strings"
	"testing"
)

func TestBoundEpisodeEvidenceKeepsOnlyCompleteEntriesWithinUTF8Budget(t *testing.T) {
	items := []EpisodeEvidence{
		{MemoryRef: "episode-too-large", Text: "一二三"}, // 9 UTF-8 bytes
		{MemoryRef: "episode-fits", Text: "ab\ncd"},   // 5 UTF-8 bytes
		{MemoryRef: "episode-rest", Text: "xy"},       // 2 UTF-8 bytes
	}

	got := BoundEpisodeEvidence(items, 7)
	want := []EpisodeEvidence{
		{MemoryRef: "episode-fits", Text: "ab\ncd"},
		{MemoryRef: "episode-rest", Text: "xy"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("BoundEpisodeEvidence() = %#v; want complete later entries %#v", got, want)
	}
}

func TestBoundEpisodeEvidenceCapsReturnedEntriesAtEight(t *testing.T) {
	items := make([]EpisodeEvidence, 9)
	for index := range items {
		items[index] = EpisodeEvidence{MemoryRef: "episode-" + string(rune('a'+index)), Text: "x"}
	}

	if got := BoundEpisodeEvidence(items, 9); len(got) != 8 {
		t.Fatalf("BoundEpisodeEvidence() returned %d entries; want at most 8", len(got))
	}
}

func TestEpisodeEvidenceBudgetAcceptsOnlyZeroOrOneThrough16384(t *testing.T) {
	for _, value := range []int{-1, 16385} {
		if ValidEpisodeEvidenceMaxBytes(value) {
			t.Fatalf("ValidEpisodeEvidenceMaxBytes(%d) = true; want false", value)
		}
	}
	for _, value := range []int{0, 1, 16384} {
		if !ValidEpisodeEvidenceMaxBytes(value) {
			t.Fatalf("ValidEpisodeEvidenceMaxBytes(%d) = false; want true", value)
		}
	}
}

func TestBoundEpisodeEvidenceZeroBudgetPreservesLegacyEmptyLane(t *testing.T) {
	got := BoundEpisodeEvidence([]EpisodeEvidence{{MemoryRef: "episode-1", Text: strings.Repeat("x", 4)}}, 0)
	if len(got) != 0 {
		t.Fatalf("BoundEpisodeEvidence() = %#v; want empty legacy lane", got)
	}
}
