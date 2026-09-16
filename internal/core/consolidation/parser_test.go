package consolidation

import (
	"reflect"
	"strings"
	"testing"
)

func TestParseTaggedTextParsesIndependentRecollectionAndDispositionBlocks(t *testing.T) {
	input := "\r\nTARGET\r\nNEW_RECOLLECTION\r\nAPPLICATION\r\nSELF\r\nCHANGE\r\nTEXT remembers that short answers help\r\nBASIS\r\nepisode-1\r\nepisode-2\r\n\r\nTARGET\r\nNEW_DISPOSITION\r\nAPPLICATION\r\nRELATION\r\nCHANGE\r\nTEXT asks a clarifying question first\r\nBASIS\r\nepisode-3\r\nepisode-4\r\n\r\nTARGET\r\nrecollection-7@2\r\nAPPLICATION\r\nSITUATION\r\nCHANGE\r\nKEEP\r\nBASIS\r\nepisode-5\r\n"

	got, err := ParseTaggedText(input)
	if err != nil {
		t.Fatalf("ParseTaggedText() error = %v", err)
	}
	want := []Change{
		{
			Target: TargetNewRecollection, Application: ApplicationSelf,
			Operation: ChangeText, Text: "remembers that short answers help",
			BasisRefs: []string{"episode-1", "episode-2"},
		},
		{
			Target: TargetNewDisposition, Application: ApplicationRelation,
			Operation: ChangeText, Text: "asks a clarifying question first",
			BasisRefs: []string{"episode-3", "episode-4"},
		},
		{
			Target: "recollection-7@2", Application: ApplicationSituation,
			Operation: ChangeKeep, BasisRefs: []string{"episode-5"},
		},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("ParseTaggedText() = %#v; want %#v", got, want)
	}
}

func TestParseTaggedTextAcceptsModelNaturalTextOnFollowingLine(t *testing.T) {
	input := "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nRELATION\nCHANGE\nTEXT\n用户反复要求系统设计保持最小因果模型\nBASIS\nepisode-alpha\nepisode-beta\n"

	got, err := ParseTaggedText(input)
	if err != nil {
		t.Fatalf("ParseTaggedText() error = %v", err)
	}
	want := []Change{{
		Target: TargetNewRecollection, Application: ApplicationRelation,
		Operation: ChangeText, Text: "用户反复要求系统设计保持最小因果模型",
		BasisRefs: []string{"episode-alpha", "episode-beta"},
	}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("ParseTaggedText() = %#v; want %#v", got, want)
	}
}

func TestParseTaggedTextTreatsBlankOutputAsNoOp(t *testing.T) {
	got, err := ParseTaggedText(" \n\r\n\t")
	if err != nil {
		t.Fatalf("ParseTaggedText() error = %v", err)
	}
	if len(got) != 0 {
		t.Fatalf("ParseTaggedText() returned %d changes; want no-op", len(got))
	}
}

func TestParseTaggedTextAdaptRequiresOneLineText(t *testing.T) {
	for _, target := range []string{TargetNewDisposition, "seed-1@1"} {
		for _, operation := range []string{"ADAPT\n先听我说完再提建议", "ADAPT 先听我说完再提建议"} {
			got, err := ParseTaggedText("TARGET\n" + target + "\nAPPLICATION\nRELATION\nCHANGE\n" + operation + "\nBASIS\nepisode-1\n")
			if err != nil || len(got) != 1 || got[0].Operation != "ADAPT" || got[0].Text != "先听我说完再提建议" {
				t.Fatalf("ADAPT parse = %#v, %v", got, err)
			}
		}
	}
	for _, body := range []string{"ADAPT", "ADAPT\nfirst line\nsecond line", "ADAPT\n" + strings.Repeat("x", MaxChangeTextBytes+1)} {
		got, err := ParseTaggedText("TARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\n" + body + "\nBASIS\nepisode-1\n")
		if err == nil || got != nil {
			t.Fatalf("invalid ADAPT accepted: %#v, %v", got, err)
		}
	}
}

func TestParseTaggedTextAllowsCommitLayerToDecideOperationAndScopeEligibility(t *testing.T) {
	input := "TARGET\nexisting-version-7\nAPPLICATION\nOTHER\nCHANGE\nREENACT\nBASIS\noutcome-4\n\nTARGET\nexisting-version-8\nAPPLICATION\nSITUATION\nCHANGE\nINHIBIT\nBASIS\nepisode-5\n"

	got, err := ParseTaggedText(input)
	if err != nil {
		t.Fatalf("ParseTaggedText() error = %v", err)
	}
	want := []Change{
		{Target: "existing-version-7", Application: ApplicationOther, Operation: ChangeReenact, BasisRefs: []string{"outcome-4"}},
		{Target: "existing-version-8", Application: ApplicationSituation, Operation: ChangeInhibit, BasisRefs: []string{"episode-5"}},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("ParseTaggedText() = %#v; want %#v", got, want)
	}
}

func TestParseTaggedTextRejectsMalformedBlockWithoutPartialChanges(t *testing.T) {
	input := "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nSELF\nCHANGE\nTEXT remembers a detail\nBASIS\nepisode-1\n\nTARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nTEXT learns from the outcome\nBASIS\n"

	changes, err := ParseTaggedText(input)
	if err == nil {
		t.Fatal("ParseTaggedText() error = nil; want malformed second block error")
	}
	if changes != nil {
		t.Fatalf("ParseTaggedText() changes = %#v; want nil on malformed output", changes)
	}
}

func TestParseTaggedTextRejectsDuplicateExistingTargetWithoutPartialChanges(t *testing.T) {
	input := "TARGET\nrecollection-7@2\nAPPLICATION\nSELF\nCHANGE\nKEEP\nBASIS\nepisode-1\n\nTARGET\nrecollection-7@2\nAPPLICATION\nSELF\nCHANGE\nTEXT changes the recollection\nBASIS\nepisode-2\n"

	changes, err := ParseTaggedText(input)
	if err == nil {
		t.Fatal("ParseTaggedText() error = nil; want duplicate existing target error")
	}
	if changes != nil {
		t.Fatalf("ParseTaggedText() changes = %#v; want nil on duplicate target", changes)
	}
}

func TestParseTaggedTextRejectsInvalidGrammarAndDuplicateBasisWithoutPartialChanges(t *testing.T) {
	tests := []struct {
		name  string
		input string
	}{
		{name: "legacy target", input: "TARGET\nNEW\nAPPLICATION\nSELF\nCHANGE\nTEXT old grammar\nBASIS\nepisode-1\n"},
		{name: "missing application marker", input: "TARGET\nNEW_RECOLLECTION\nCHANGE\nTEXT missing application\nBASIS\nepisode-1\n"},
		{name: "unknown application", input: "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nTEAM\nCHANGE\nTEXT unknown scope\nBASIS\nepisode-1\n"},
		{name: "legacy tendency operation", input: "TARGET\nNEW_DISPOSITION\nAPPLICATION\nSELF\nCHANGE\nTENDENCY old operation\nBASIS\nepisode-1\n"},
		{name: "decorated keep", input: "TARGET\nversion-7\nAPPLICATION\nSELF\nCHANGE\nKEEP please\nBASIS\nepisode-1\n"},
		{name: "duplicate basis", input: "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nSELF\nCHANGE\nTEXT duplicate evidence\nBASIS\nepisode-1\nepisode-1\n"},
		{name: "basis is new target token", input: "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nSELF\nCHANGE\nTEXT invalid evidence\nBASIS\nNEW_DISPOSITION\n"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			changes, err := ParseTaggedText(test.input)
			if err == nil {
				t.Fatal("ParseTaggedText() error = nil; want grammar error")
			}
			if changes != nil {
				t.Fatalf("ParseTaggedText() changes = %#v; want nil on invalid output", changes)
			}
		})
	}
}

func TestParseTaggedTextRejectsReservedGrammarTokensAsBasisWithoutPartialChanges(t *testing.T) {
	for _, basis := range []string{
		"TARGET",
		"APPLICATION",
		"CHANGE",
		"BASIS",
		"NEW",
		TargetNewRecollection,
		TargetNewDisposition,
	} {
		t.Run(basis, func(t *testing.T) {
			input := "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nSELF\nCHANGE\nTEXT invalid evidence\nBASIS\n" + basis + "\n"
			changes, err := ParseTaggedText(input)
			if err == nil {
				t.Fatal("ParseTaggedText() error = nil; want reserved basis error")
			}
			if changes != nil {
				t.Fatalf("ParseTaggedText() changes = %#v; want nil on reserved basis", changes)
			}
		})
	}
}

func TestParseTaggedTextRejectsOversizedOutputAndTextWithoutPartialChanges(t *testing.T) {
	tests := []struct {
		name  string
		input string
	}{
		{name: "whole response", input: strings.Repeat("x", MaxTaggedTextBytes+1)},
		{name: "one text value", input: "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nSELF\nCHANGE\nTEXT " + strings.Repeat("x", MaxChangeTextBytes+1) + "\nBASIS\nepisode-1\n"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			changes, err := ParseTaggedText(test.input)
			if err == nil || changes != nil {
				t.Fatalf("ParseTaggedText() = (%#v, %v); want nil, size error", changes, err)
			}
		})
	}
}
