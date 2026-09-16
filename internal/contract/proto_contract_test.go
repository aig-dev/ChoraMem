package contract_test

import (
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"slices"
	"strings"
	"testing"

	inferencev1 "github.com/aig-dev/ChoraMem/gen/memory/inference/v1"
	memoryv1 "github.com/aig-dev/ChoraMem/gen/memory/v1"
	"github.com/aig-dev/ChoraMem/gen/memory/v1/memoryv1connect"
	memoryindexv1 "github.com/aig-dev/ChoraMem/gen/memoryindex/v1"
	"google.golang.org/grpc"
)

func TestGeneratedPublicMemoryCoreServiceHasOnlyFrozenRPCs(t *testing.T) {
	got := methodNames(memoryv1.MemoryCore_ServiceDesc.Methods)
	want := []string{
		"ObserveSourceEvent",
		"SelectMemory",
		"RecordMemoryDelivery",
		"ReportOutcome",
	}
	if !slices.Equal(got, want) {
		t.Fatalf("generated MemoryCore RPCs = %v, want exactly %v", got, want)
	}
	if len(memoryv1.MemoryCore_ServiceDesc.Streams) != 0 {
		t.Fatalf("generated MemoryCore streams = %d, want none", len(memoryv1.MemoryCore_ServiceDesc.Streams))
	}
}

func TestGeneratedConnectSurfaceHasOnlyFrozenMemoryProcedures(t *testing.T) {
	got := []string{
		memoryv1connect.MemoryCoreObserveSourceEventProcedure,
		memoryv1connect.MemoryCoreSelectMemoryProcedure,
		memoryv1connect.MemoryCoreRecordMemoryDeliveryProcedure,
		memoryv1connect.MemoryCoreReportOutcomeProcedure,
	}
	want := []string{
		"/memory.v1.MemoryCore/ObserveSourceEvent",
		"/memory.v1.MemoryCore/SelectMemory",
		"/memory.v1.MemoryCore/RecordMemoryDelivery",
		"/memory.v1.MemoryCore/ReportOutcome",
	}
	if !slices.Equal(got, want) {
		t.Fatalf("generated Connect procedures = %v, want %v", got, want)
	}
}

func TestGeneratedInternalInferenceServiceHasOnlyFrozenRPC(t *testing.T) {
	got := methodNames(inferencev1.InferenceWorker_ServiceDesc.Methods)
	want := []string{"ProcessConsolidationWindow"}
	if !slices.Equal(got, want) {
		t.Fatalf("generated InferenceWorker RPCs = %v, want exactly %v", got, want)
	}
	if len(inferencev1.InferenceWorker_ServiceDesc.Streams) != 0 {
		t.Fatalf("generated InferenceWorker streams = %d, want none", len(inferencev1.InferenceWorker_ServiceDesc.Streams))
	}
}

func TestGeneratedMemoryIndexServiceHasOnlyTypedProjectionRPCs(t *testing.T) {
	got := methodNames(memoryindexv1.MemoryIndex_ServiceDesc.Methods)
	want := []string{"Search", "Upsert", "Delete", "Reset"}
	if !slices.Equal(got, want) {
		t.Fatalf("generated MemoryIndex RPCs = %v, want exactly %v", got, want)
	}
	if len(memoryindexv1.MemoryIndex_ServiceDesc.Streams) != 0 {
		t.Fatalf("generated MemoryIndex streams = %d, want none", len(memoryindexv1.MemoryIndex_ServiceDesc.Streams))
	}
}

func methodNames(methods []grpc.MethodDesc) []string {
	names := make([]string, 0, len(methods))
	for _, method := range methods {
		names = append(names, method.MethodName)
	}
	return names
}

func TestPublicMemoryCoreHasOnlyFrozenRPCs(t *testing.T) {
	proto := readModuleFile(t, "api", "memory", "v1", "memory.proto")

	got := rpcSignatures(proto)
	want := []rpcSignature{
		{"ObserveSourceEvent", "ObserveSourceEventRequest", "SourceEventReceipt"},
		{"SelectMemory", "SelectMemoryRequest", "MemoryContext"},
		{"RecordMemoryDelivery", "MemoryDeliveryReceipt", "ReceiptAck"},
		{"ReportOutcome", "ReportOutcomeRequest", "OutcomeReceipt"},
	}
	if !slices.Equal(got, want) {
		t.Fatalf("MemoryCore RPC signatures = %v, want exactly %v", got, want)
	}
}

func TestInferenceWorkerHasOneRPCAndTextOnlyResponse(t *testing.T) {
	proto := readModuleFile(t, "api", "memory", "inference", "v1", "inference.proto")

	if got, want := rpcSignatures(proto), []rpcSignature{{
		"ProcessConsolidationWindow",
		"ProcessConsolidationWindowRequest",
		"TaggedTextResponse",
	}}; !slices.Equal(got, want) {
		t.Fatalf("InferenceWorker RPC signatures = %v, want exactly %v", got, want)
	}

	body := messageBody(t, proto, "TaggedTextResponse")
	fields := regexp.MustCompile(`(?m)^\s*(?:repeated\s+|optional\s+)?[A-Za-z_.][A-Za-z0-9_.]*\s+[a-z][a-z0-9_]*\s*=\s*\d+\s*;`).FindAllString(body, -1)
	if len(fields) != 1 || !regexp.MustCompile(`^\s*string\s+tagged_text\s*=\s*1\s*;\s*$`).MatchString(fields[0]) {
		t.Fatalf("TaggedTextResponse must contain only `string tagged_text = 1;`; got %q", fields)
	}
}

func TestMemoryIndexProtocolIsTypedGeneratedProtobufWithoutScores(t *testing.T) {
	proto := readModuleFile(t, "api", "memoryindex", "v1", "memory_index.proto")
	got := rpcSignatures(proto)
	want := []rpcSignature{
		{"Search", "SearchRequest", "SearchResponse"},
		{"Upsert", "UpsertRequest", "UpsertResponse"},
		{"Delete", "DeleteRequest", "DeleteResponse"},
		{"Reset", "ResetRequest", "ResetResponse"},
	}
	if !slices.Equal(got, want) {
		t.Fatalf("MemoryIndex RPCs = %#v; want %#v", got, want)
	}
	for _, declaration := range []string{
		"MEMORY_KIND_EPISODE = 1;",
		"MEMORY_KIND_RECOLLECTION = 2;",
		"MEMORY_KIND_DISPOSITION = 3;",
	} {
		if !strings.Contains(proto, declaration) {
			t.Errorf("MemoryIndex proto missing %q", declaration)
		}
	}
	for _, forbidden := range []string{"score", "distance", "confidence", "seed_version_ref"} {
		if strings.Contains(strings.ToLower(proto), forbidden) {
			t.Errorf("MemoryIndex proto leaked forbidden field %q", forbidden)
		}
	}
}

func TestMemoryContextHasOnlyFrozenOutputFields(t *testing.T) {
	proto := readModuleFile(t, "api", "memory", "v1", "memory.proto")

	assertFields(t, proto, "MemoryContext", []string{
		"string context_ref = 1",
		"string run_ref = 2",
		"MemoryScope scope = 3",
		"Constitution constitution = 4",
		"repeated Recollection recollections = 5",
		"repeated Disposition dispositions = 6",
		"repeated EpisodeEvidence episode_evidence = 7",
	})
	assertFields(t, proto, "Constitution", []string{
		"string memory_ref = 1",
		"string text = 2",
	})
	for _, message := range []string{"Recollection", "Disposition"} {
		assertFields(t, proto, message, []string{
			"string memory_ref = 1",
			"string text = 2",
			"MemoryApplicationScope application_scope = 3",
		})
	}
	assertFields(t, proto, "EpisodeEvidence", []string{
		"string memory_ref = 1",
		"string text = 2",
	})
	for _, declaration := range []string{
		"MEMORY_APPLICATION_SCOPE_UNSPECIFIED = 0;",
		"MEMORY_APPLICATION_SCOPE_SELF = 1;",
		"MEMORY_APPLICATION_SCOPE_OTHER = 2;",
		"MEMORY_APPLICATION_SCOPE_RELATION = 3;",
		"MEMORY_APPLICATION_SCOPE_SITUATION = 4;",
	} {
		if !strings.Contains(proto, declaration) {
			t.Errorf("public proto missing %q", declaration)
		}
	}
	if strings.Contains(proto, "DISPOSITION_APPLICATION_SCOPE") {
		t.Error("Disposition application restrictions belong to Core semantics, not the public proto")
	}
}

func TestSelectMemoryCarriesExternalConstitutionWithoutMakingItMemory(t *testing.T) {
	proto := readModuleFile(t, "api", "memory", "v1", "memory.proto")

	assertFields(t, proto, "SelectMemoryRequest", []string{
		"MemoryScope scope = 1",
		"string run_ref = 2",
		"repeated string situation_source_event_refs = 3",
		"Constitution constitution = 4",
		"int32 episode_evidence_max_bytes = 5",
	})
	if strings.Contains(proto, "PersonaContext") || strings.Contains(proto, "AssemblePersona") {
		t.Error("public protocol must not retain PersonaContext or AssemblePersona compatibility names")
	}
}

func TestRecordMemoryDeliveryCarriesExactSelectedMemoryRefs(t *testing.T) {
	proto := readModuleFile(t, "api", "memory", "v1", "memory.proto")
	assertFields(t, proto, "MemoryDeliveryReceipt", []string{
		"string idempotency_key = 1",
		"MemoryScope scope = 2",
		"string run_ref = 3",
		"string memory_context_ref = 4",
		"repeated string delivered_memory_refs = 5",
	})
}

func TestSourceIdentityAndScopeKindAreExplicit(t *testing.T) {
	proto := readModuleFile(t, "api", "memory", "v1", "memory.proto")

	assertFields(t, proto, "MemoryScope", []string{
		"string tenant_ref = 1",
		"string agent_ref = 2",
		"string relationship_ref = 3",
		"string session_ref = 4",
		"MemoryScopeKind kind = 5",
	})
	assertFields(t, proto, "SourceEvent", []string{
		"MemoryScope scope = 1",
		"string text = 2",
		"string source_ref = 3",
		"SourceActorKind actor_kind = 4",
		"string actor_ref = 5",
		"Constitution constitution = 6",
	})

	for _, declaration := range []string{
		"MEMORY_SCOPE_KIND_UNSPECIFIED = 0;",
		"MEMORY_SCOPE_KIND_AGENT = 1;",
		"MEMORY_SCOPE_KIND_RELATIONSHIP = 2;",
		"enum SourceActorKind",
	} {
		if !strings.Contains(proto, declaration) {
			t.Errorf("public proto missing %q", declaration)
		}
	}
}

func TestObserveSourceIdentityContractIsDocumented(t *testing.T) {
	proto := readModuleFile(t, "api", "memory", "v1", "memory.proto")
	for _, phrase := range []string{
		"complete ObserveSourceEvent request, including episode_binding",
		"new idempotency_key",
		"reuse source_event.source_ref",
	} {
		if !strings.Contains(proto, phrase) {
			t.Errorf("ObserveSourceEvent identity contract missing documentation phrase %q", phrase)
		}
	}
}

func TestReportOutcomeCarriesTrustedSourceIdentity(t *testing.T) {
	proto := readModuleFile(t, "api", "memory", "v1", "memory.proto")
	assertFields(t, proto, "ReportOutcomeRequest", []string{
		"string idempotency_key = 1",
		"MemoryScope scope = 2",
		"string run_ref = 3",
		"string source_group_ref = 4",
		"string text = 5",
		"repeated string delivery_receipt_refs = 6",
		"repeated string related_source_event_refs = 7",
		"string source_ref = 8",
		"SourceActorKind actor_kind = 9",
		"string actor_ref = 10",
		"Constitution constitution = 11",
	})
}

func TestEpisodeRoleBelongsToBindingNotSourceEvent(t *testing.T) {
	proto := readModuleFile(t, "api", "memory", "v1", "memory.proto")
	sourceEvent := messageBody(t, proto, "SourceEvent")
	binding := messageBody(t, proto, "EpisodeBinding")

	for _, field := range []string{"run_ref", "source_group_ref", "role"} {
		if strings.Contains(sourceEvent, field) {
			t.Errorf("SourceEvent must not own Episode field %q", field)
		}
		if !strings.Contains(binding, field) {
			t.Errorf("EpisodeBinding must own field %q", field)
		}
	}

	request := messageBody(t, proto, "ObserveSourceEventRequest")
	if !regexp.MustCompile(`(?m)^\s*EpisodeBinding\s+episode_binding\s*=\s*\d+\s*;`).MatchString(request) {
		t.Error("ObserveSourceEventRequest must carry an optional EpisodeBinding message")
	}
	if !strings.Contains(proto, "EPISODE_SOURCE_ROLE_AGENT_ACT = 2;") {
		t.Error("AgentAct must remain an ObserveSourceEvent episode role, not a fifth public RPC")
	}
}

func TestEveryEnumStartsWithUnspecifiedZero(t *testing.T) {
	paths := [][]string{
		{"api", "memory", "v1", "memory.proto"},
		{"api", "memory", "inference", "v1", "inference.proto"},
		{"api", "memoryindex", "v1", "memory_index.proto"},
	}
	enumPattern := regexp.MustCompile(`(?s)enum\s+([A-Za-z][A-Za-z0-9_]*)\s*\{(.*?)\}`)
	valuePattern := regexp.MustCompile(`(?m)^\s*([A-Z][A-Z0-9_]*)\s*=\s*(\d+)\s*;`)

	for _, path := range paths {
		proto := readModuleFile(t, path...)
		for _, enum := range enumPattern.FindAllStringSubmatch(proto, -1) {
			values := valuePattern.FindAllStringSubmatch(enum[2], -1)
			if len(values) == 0 || !strings.HasSuffix(values[0][1], "_UNSPECIFIED") || values[0][2] != "0" {
				t.Errorf("enum %s must start with *_UNSPECIFIED = 0", enum[1])
			}
		}
	}
}

func TestBufUsesFrozenCompatibilityPolicy(t *testing.T) {
	config := readModuleFile(t, "buf.yaml")
	for _, required := range []string{"version: v2", "- STANDARD", "- FILE"} {
		if !strings.Contains(config, required) {
			t.Errorf("buf.yaml missing %q", required)
		}
	}
}

type rpcSignature struct {
	name     string
	request  string
	response string
}

func rpcSignatures(proto string) []rpcSignature {
	matches := regexp.MustCompile(`(?m)^\s*rpc\s+([A-Za-z][A-Za-z0-9_]*)\s*\(\s*([A-Za-z_.][A-Za-z0-9_.]*)\s*\)\s*returns\s*\(\s*([A-Za-z_.][A-Za-z0-9_.]*)\s*\)\s*;`).FindAllStringSubmatch(proto, -1)
	signatures := make([]rpcSignature, 0, len(matches))
	for _, match := range matches {
		signatures = append(signatures, rpcSignature{match[1], match[2], match[3]})
	}
	return signatures
}

func assertFields(t *testing.T, proto, message string, want []string) {
	t.Helper()
	body := messageBody(t, proto, message)
	matches := regexp.MustCompile(`(?m)^\s*((?:repeated\s+|optional\s+)?(?:map\s*<\s*[A-Za-z_.][A-Za-z0-9_.]*\s*,\s*[A-Za-z_.][A-Za-z0-9_.]*\s*>|[A-Za-z_.][A-Za-z0-9_.]*)\s+[a-z][a-z0-9_]*\s*=\s*\d+)\s*;`).FindAllStringSubmatch(body, -1)
	got := make([]string, 0, len(matches))
	spaces := regexp.MustCompile(`\s+`)
	for _, match := range matches {
		got = append(got, spaces.ReplaceAllString(strings.TrimSpace(match[1]), " "))
	}
	if !slices.Equal(got, want) {
		t.Errorf("%s fields = %v, want exactly %v", message, got, want)
	}
}

func messageBody(t *testing.T, proto, name string) string {
	t.Helper()
	match := regexp.MustCompile(`(?s)message\s+` + regexp.QuoteMeta(name) + `\s*\{(.*?)\}`).FindStringSubmatch(proto)
	if len(match) != 2 {
		t.Fatalf("message %s not found", name)
	}
	return match[1]
}

func readModuleFile(t *testing.T, parts ...string) string {
	t.Helper()
	_, currentFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("resolve contract test path")
	}
	moduleRoot := filepath.Clean(filepath.Join(filepath.Dir(currentFile), "..", ".."))
	data, err := os.ReadFile(filepath.Join(append([]string{moduleRoot}, parts...)...))
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}
