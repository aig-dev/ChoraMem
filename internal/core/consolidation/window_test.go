package consolidation

import (
	"reflect"
	"strings"
	"testing"
)

func TestFitsWorkerRequestLimitCountsEveryTransportString(t *testing.T) {
	within := WorkerRequest{WindowText: strings.Repeat("x", MaxWorkerRequestBytes)}
	if !FitsWorkerRequestLimit(within) {
		t.Fatal("request at exact byte limit was rejected")
	}

	over := within
	over.AllowedBasisRefs = []string{"episode-extra"}
	if FitsWorkerRequestLimit(over) {
		t.Fatal("request whose refs cross the byte limit was accepted")
	}
}

func TestFitsWorkerRequestLimitCountsTypedEvidence(t *testing.T) {
	tests := []struct {
		name     string
		evidence *WindowEvidence
	}{
		{name: "episode ref", evidence: &WindowEvidence{Episodes: []EvidenceEpisode{{EpisodeRef: "x"}}}},
		{name: "session ref", evidence: &WindowEvidence{Episodes: []EvidenceEpisode{{SessionRef: "x"}}}},
		{name: "origin", evidence: &WindowEvidence{Episodes: []EvidenceEpisode{{Origin: "x"}}}},
		{name: "source ref", evidence: &WindowEvidence{Episodes: []EvidenceEpisode{{Sources: []EvidenceSource{{SourceRef: "x"}}}}}},
		{name: "source role", evidence: &WindowEvidence{Episodes: []EvidenceEpisode{{Sources: []EvidenceSource{{Role: "x"}}}}}},
		{name: "source actor kind", evidence: &WindowEvidence{Episodes: []EvidenceEpisode{{Sources: []EvidenceSource{{ActorKind: "x"}}}}}},
		{name: "source actor ref", evidence: &WindowEvidence{Episodes: []EvidenceEpisode{{Sources: []EvidenceSource{{ActorRef: "x"}}}}}},
		{name: "source text", evidence: &WindowEvidence{Episodes: []EvidenceEpisode{{Sources: []EvidenceSource{{Text: "x"}}}}}},
		{name: "outcome ref", evidence: &WindowEvidence{Outcomes: []EvidenceOutcome{{OutcomeRef: "x"}}}},
		{name: "outcome episode ref", evidence: &WindowEvidence{Outcomes: []EvidenceOutcome{{EpisodeRef: "x"}}}},
		{name: "outcome actor kind", evidence: &WindowEvidence{Outcomes: []EvidenceOutcome{{ActorKind: "x"}}}},
		{name: "outcome actor ref", evidence: &WindowEvidence{Outcomes: []EvidenceOutcome{{ActorRef: "x"}}}},
		{name: "outcome text", evidence: &WindowEvidence{Outcomes: []EvidenceOutcome{{Text: "x"}}}},
		{name: "recollection version ref", evidence: &WindowEvidence{Recollections: []EvidenceRecollection{{VersionRef: "x"}}}},
		{name: "recollection application", evidence: &WindowEvidence{Recollections: []EvidenceRecollection{{Application: "x"}}}},
		{name: "recollection text", evidence: &WindowEvidence{Recollections: []EvidenceRecollection{{Text: "x"}}}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request := WorkerRequest{
				WindowText: strings.Repeat("x", MaxWorkerRequestBytes),
				Evidence:   test.evidence,
			}
			if FitsWorkerRequestLimit(request) {
				t.Fatal("request whose typed evidence crosses the byte limit was accepted")
			}
		})
	}
}

func TestReceiptSeparatesRecollectionAndDispositionVersionRefs(t *testing.T) {
	receipt := Receipt{
		RecollectionVersionRefs: []string{"recollection-7@2"},
		DispositionVersionRefs:  []string{"disposition-4@1"},
	}
	if !reflect.DeepEqual(receipt.RecollectionVersionRefs, []string{"recollection-7@2"}) {
		t.Fatalf("RecollectionVersionRefs = %#v", receipt.RecollectionVersionRefs)
	}
	if !reflect.DeepEqual(receipt.DispositionVersionRefs, []string{"disposition-4@1"}) {
		t.Fatalf("DispositionVersionRefs = %#v", receipt.DispositionVersionRefs)
	}
}
