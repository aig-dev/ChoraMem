// connect-json-lifecycle exercises the complete public Memory Core lifecycle
// over Connect JSON without calling an external model.
package main

import (
	"bytes"
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
	"unicode"

	memoryv1 "github.com/aig-dev/ChoraMem/gen/memory/v1"
	"github.com/aig-dev/ChoraMem/gen/memory/v1/memoryv1connect"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

const (
	situationText = "The user asks for a concise Memory Core smoke response."
	agentActText  = "This deterministic response verifies the public lifecycle without a model."
	outcomeText   = "The external smoke observer accepted the deterministic response."
)

type config struct {
	Endpoint        string
	Token           string
	TenantRef       string
	AgentRef        string
	RelationshipRef string
	SessionRef      string
	RunRef          string
	SourceGroupRef  string
}

func main() {
	configuration := config{}
	flag.StringVar(&configuration.Endpoint, "endpoint", envOr("MEMORY_CORE_HTTP_ENDPOINT", "http://127.0.0.1:8081"), "Memory Core Connect base URL")
	flag.StringVar(&configuration.Token, "token", os.Getenv("MEMORY_CORE_TOKEN"), "tenant-scoped bearer token")
	flag.StringVar(&configuration.TenantRef, "tenant", envOr("MEMORY_CORE_TENANT_REF", "smoke-tenant"), "tenant ref")
	flag.StringVar(&configuration.AgentRef, "agent", envOr("MEMORY_CORE_AGENT_REF", "smoke-agent"), "agent ref")
	flag.StringVar(&configuration.RelationshipRef, "relationship", envOr("MEMORY_CORE_RELATIONSHIP_REF", "smoke-relationship"), "relationship ref; blank selects agent scope")
	flag.StringVar(&configuration.SessionRef, "session", envOr("MEMORY_CORE_SESSION_REF", "smoke-session"), "session ref")
	flag.Parse()

	identity := fmt.Sprintf("smoke-%d", time.Now().UnixNano())
	configuration.RunRef = identity + "-run"
	configuration.SourceGroupRef = identity + "-group"

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	result, err := runLifecycle(ctx, configuration, http.DefaultClient)
	if err != nil {
		fmt.Fprintln(os.Stderr, "Memory Core lifecycle smoke failed:", err)
		os.Exit(1)
	}
	fmt.Printf("outcome_event_ref=%s episode_ref=%s\n", result.GetOutcomeEventRef(), result.GetEpisodeRef())
}

func runLifecycle(ctx context.Context, configuration config, client *http.Client) (*memoryv1.OutcomeReceipt, error) {
	if err := validateConfig(configuration, client); err != nil {
		return nil, err
	}
	endpoint := strings.TrimRight(configuration.Endpoint, "/")
	scope := &memoryv1.MemoryScope{
		Kind:       memoryv1.MemoryScopeKind_MEMORY_SCOPE_KIND_AGENT,
		TenantRef:  configuration.TenantRef,
		AgentRef:   configuration.AgentRef,
		SessionRef: configuration.SessionRef,
	}
	if configuration.RelationshipRef != "" {
		scope.Kind = memoryv1.MemoryScopeKind_MEMORY_SCOPE_KIND_RELATIONSHIP
		scope.RelationshipRef = configuration.RelationshipRef
	}

	situation := &memoryv1.SourceEventReceipt{}
	if err := postJSON(ctx, client, endpoint, configuration.Token,
		memoryv1connect.MemoryCoreObserveSourceEventProcedure,
		&memoryv1.ObserveSourceEventRequest{
			IdempotencyKey: configuration.RunRef + "-situation-request",
			SourceEvent: &memoryv1.SourceEvent{
				Scope: scope, Text: situationText, SourceRef: configuration.RunRef + "-situation",
				ActorKind: memoryv1.SourceActorKind_SOURCE_ACTOR_KIND_USER, ActorRef: "smoke-user",
			},
			EpisodeBinding: &memoryv1.EpisodeBinding{
				RunRef: configuration.RunRef, SourceGroupRef: configuration.SourceGroupRef,
				Role: memoryv1.EpisodeSourceRole_EPISODE_SOURCE_ROLE_SITUATION,
			},
		}, situation); err != nil {
		return nil, fmt.Errorf("observe Situation: %w", err)
	}
	if strings.TrimSpace(situation.GetSourceEventRef()) == "" {
		return nil, errors.New("observe Situation returned an empty source_event_ref")
	}

	selected := &memoryv1.MemoryContext{}
	if err := postJSON(ctx, client, endpoint, configuration.Token,
		memoryv1connect.MemoryCoreSelectMemoryProcedure,
		&memoryv1.SelectMemoryRequest{
			Scope: scope, RunRef: configuration.RunRef,
			SituationSourceEventRefs: []string{situation.GetSourceEventRef()},
			Constitution: &memoryv1.Constitution{
				MemoryRef: "smoke-constitution-v1", Text: "Stay honest and concise.",
			},
		}, selected); err != nil {
		return nil, fmt.Errorf("select Memory: %w", err)
	}
	if strings.TrimSpace(selected.GetContextRef()) == "" {
		return nil, errors.New("SelectMemory returned an empty context_ref")
	}
	renderedMemory, deliveredRefs, err := renderMemoryContext(selected)
	if err != nil {
		return nil, fmt.Errorf("validate selected MemoryContext: %w", err)
	}
	if renderedMemory == "" || len(deliveredRefs) == 0 {
		return nil, errors.New("smoke lifecycle selected no injectable memory")
	}
	modelInput := renderedMemory + "\n\nUSER\n" + situationText

	delivery := &memoryv1.ReceiptAck{}
	if err := postJSON(ctx, client, endpoint, configuration.Token,
		memoryv1connect.MemoryCoreRecordMemoryDeliveryProcedure,
		&memoryv1.MemoryDeliveryReceipt{
			IdempotencyKey: configuration.RunRef + "-delivery", Scope: scope,
			RunRef: configuration.RunRef, MemoryContextRef: selected.GetContextRef(),
			DeliveredMemoryRefs: deliveredRefs,
		}, delivery); err != nil {
		return nil, fmt.Errorf("record Memory delivery: %w", err)
	}
	if strings.TrimSpace(delivery.GetReceiptRef()) == "" {
		return nil, errors.New("RecordMemoryDelivery returned an empty receipt_ref")
	}
	agentOutput, err := runDeterministicModel(modelInput)
	if err != nil {
		return nil, err
	}

	agentAct := &memoryv1.SourceEventReceipt{}
	if err := postJSON(ctx, client, endpoint, configuration.Token,
		memoryv1connect.MemoryCoreObserveSourceEventProcedure,
		&memoryv1.ObserveSourceEventRequest{
			IdempotencyKey: configuration.RunRef + "-agent-act-request",
			SourceEvent: &memoryv1.SourceEvent{
				Scope: scope, Text: agentOutput, SourceRef: configuration.RunRef + "-agent-act",
				ActorKind: memoryv1.SourceActorKind_SOURCE_ACTOR_KIND_AGENT, ActorRef: configuration.AgentRef,
			},
			EpisodeBinding: &memoryv1.EpisodeBinding{
				RunRef: configuration.RunRef, SourceGroupRef: configuration.SourceGroupRef,
				Role: memoryv1.EpisodeSourceRole_EPISODE_SOURCE_ROLE_AGENT_ACT,
			},
		}, agentAct); err != nil {
		return nil, fmt.Errorf("observe AgentAct: %w", err)
	}
	if strings.TrimSpace(agentAct.GetSourceEventRef()) == "" {
		return nil, errors.New("observe AgentAct returned an empty source_event_ref")
	}

	outcome := &memoryv1.OutcomeReceipt{}
	if err := postJSON(ctx, client, endpoint, configuration.Token,
		memoryv1connect.MemoryCoreReportOutcomeProcedure,
		&memoryv1.ReportOutcomeRequest{
			IdempotencyKey: configuration.RunRef + "-outcome", Scope: scope,
			RunRef: configuration.RunRef, SourceGroupRef: configuration.SourceGroupRef,
			Text: outcomeText, DeliveryReceiptRefs: []string{delivery.GetReceiptRef()},
			RelatedSourceEventRefs: []string{situation.GetSourceEventRef(), agentAct.GetSourceEventRef()},
			SourceRef:              configuration.RunRef + "-outcome-source",
			ActorKind:              memoryv1.SourceActorKind_SOURCE_ACTOR_KIND_EXTERNAL, ActorRef: "smoke-observer",
		}, outcome); err != nil {
		return nil, fmt.Errorf("report Outcome: %w", err)
	}
	return outcome, nil
}

func renderMemoryContext(selected *memoryv1.MemoryContext) (string, []string, error) {
	if selected == nil {
		return "", nil, errors.New("MemoryContext is required")
	}
	type item struct {
		ref         string
		text        string
		application memoryv1.MemoryApplicationScope
		scoped      bool
		disposition bool
	}
	type section struct {
		label string
		items []item
	}
	sections := []section{{
		label: "CONSTITUTION",
		items: []item{{ref: selected.GetConstitution().GetMemoryRef(), text: selected.GetConstitution().GetText()}},
	}}
	recollections := make([]item, 0, len(selected.GetRecollections()))
	for _, recollection := range selected.GetRecollections() {
		recollections = append(recollections, item{
			ref: recollection.GetMemoryRef(), text: recollection.GetText(),
			application: recollection.GetApplicationScope(), scoped: true,
		})
	}
	dispositions := make([]item, 0, len(selected.GetDispositions()))
	for _, disposition := range selected.GetDispositions() {
		dispositions = append(dispositions, item{
			ref: disposition.GetMemoryRef(), text: disposition.GetText(),
			application: disposition.GetApplicationScope(), scoped: true, disposition: true,
		})
	}
	sections = append(sections,
		section{label: "RECOLLECTIONS", items: recollections},
		section{label: "DISPOSITIONS", items: dispositions},
	)

	refs := make([]string, 0, 1+len(recollections)+len(dispositions))
	seen := make(map[string]struct{}, cap(refs))
	renderedSections := make([]string, 0, len(sections))
	for _, group := range sections {
		lines := make([]string, 0, len(group.items))
		for _, candidate := range group.items {
			text := strings.TrimSpace(candidate.text)
			if text == "" {
				continue
			}
			if strings.TrimSpace(candidate.ref) == "" {
				return "", nil, errors.New("nonblank selected memory requires memory_ref")
			}
			if _, duplicate := seen[candidate.ref]; duplicate {
				return "", nil, fmt.Errorf("duplicate selected memory_ref %q", candidate.ref)
			}
			seen[candidate.ref] = struct{}{}
			refs = append(refs, candidate.ref)
			if candidate.scoped {
				application, err := applicationLabel(candidate.application, candidate.disposition)
				if err != nil {
					return "", nil, err
				}
				text = application + " " + text
			}
			lines = append(lines, text)
		}
		if len(lines) > 0 {
			renderedSections = append(renderedSections, group.label+"\n"+strings.Join(lines, "\n"))
		}
	}
	return strings.Join(renderedSections, "\n\n"), refs, nil
}

func applicationLabel(application memoryv1.MemoryApplicationScope, disposition bool) (string, error) {
	switch application {
	case memoryv1.MemoryApplicationScope_MEMORY_APPLICATION_SCOPE_SELF:
		return "SELF", nil
	case memoryv1.MemoryApplicationScope_MEMORY_APPLICATION_SCOPE_OTHER:
		if !disposition {
			return "OTHER", nil
		}
	case memoryv1.MemoryApplicationScope_MEMORY_APPLICATION_SCOPE_RELATION:
		return "RELATION", nil
	case memoryv1.MemoryApplicationScope_MEMORY_APPLICATION_SCOPE_SITUATION:
		if !disposition {
			return "SITUATION", nil
		}
	}
	return "", fmt.Errorf("unsupported application_scope %s", application)
}

func runDeterministicModel(modelInput string) (string, error) {
	if strings.TrimSpace(modelInput) == "" {
		return "", errors.New("deterministic model received empty input")
	}
	return agentActText, nil
}

func postJSON(ctx context.Context, client *http.Client, endpoint, token, procedure string, input, output proto.Message) error {
	payload, err := protojson.Marshal(input)
	if err != nil {
		return fmt.Errorf("marshal request: %w", err)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint+procedure, bytes.NewReader(payload))
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}
	request.Header.Set("Authorization", "Bearer "+token)
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Connect-Protocol-Version", "1")
	response, err := client.Do(request)
	if err != nil {
		return fmt.Errorf("send request: %w", err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if err != nil {
		return fmt.Errorf("read response: %w", err)
	}
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return fmt.Errorf("Connect HTTP status %d: %s", response.StatusCode, strings.TrimSpace(string(body)))
	}
	if err := protojson.Unmarshal(body, output); err != nil {
		return fmt.Errorf("decode response: %w", err)
	}
	return nil
}

func validateConfig(configuration config, client *http.Client) error {
	if client == nil {
		return errors.New("HTTP client is required")
	}
	for name, value := range map[string]string{
		"endpoint": configuration.Endpoint, "token": configuration.Token,
		"tenant": configuration.TenantRef, "agent": configuration.AgentRef,
		"session": configuration.SessionRef, "run": configuration.RunRef,
		"source group": configuration.SourceGroupRef,
	} {
		if strings.TrimSpace(value) == "" {
			return fmt.Errorf("%s is required", name)
		}
	}
	if strings.IndexFunc(configuration.Token, unicode.IsSpace) >= 0 {
		return errors.New("token must not contain whitespace")
	}
	return nil
}

func envOr(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}
