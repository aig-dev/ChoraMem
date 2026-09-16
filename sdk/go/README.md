# Go SDK

`SourceEvent.Constitution` 与 Outcome 的可选快照应使用与本轮 Select 相同的外生 Agent
角色版本/正文；nil 保持旧调用兼容。来源快照不可变，后台只读，不是用户档案或 Basis。

The Go SDK is a thin client for the four generated `memory.v1.MemoryCore` RPCs:

1. `ObserveSourceEvent`
2. `SelectMemory`
3. `RecordMemoryDelivery`
4. `ReportOutcome`

It owns no Memory state machine. Requests and responses remain the generated protobuf types.

## Owned connection

`Dial` owns its gRPC connection. The caller supplies transport credentials explicitly and a token provider that receives the exact request `tenant_ref` on every call.

```go
transportCredentials := credentials.NewTLS(&tls.Config{
    MinVersion: tls.VersionTLS12,
    ServerName: "memory.example.com",
})
client, err := memorycore.Dial(ctx, memorycore.Config{
    Endpoint:             "memory.example.com:443",
    DefaultTimeout:       5 * time.Second,
    TransportCredentials: transportCredentials,
    TokenProvider: memorycore.TokenProviderFunc(func(ctx context.Context, tenantRef string) (string, error) {
        return signer.TokenForTenant(ctx, tenantRef)
    }),
})
if err != nil {
    return err
}
defer client.Close()

scope := &memoryv1.MemoryScope{
    Kind:       memoryv1.MemoryScopeKind_MEMORY_SCOPE_KIND_AGENT,
    TenantRef:  "tenant-1",
    AgentRef:   "agent-1",
    SessionRef: "session-1",
}
_, err = client.ObserveSourceEvent(ctx, &memoryv1.ObserveSourceEventRequest{
    IdempotencyKey: "observe-situation-1",
    SourceEvent: &memoryv1.SourceEvent{
        Scope: scope, SourceRef: "source-1", Text: "current situation",
        ActorKind: memoryv1.SourceActorKind_SOURCE_ACTOR_KIND_USER, ActorRef: "user-1",
    },
    EpisodeBinding: &memoryv1.EpisodeBinding{
        RunRef: "run-1", SourceGroupRef: "group-1",
        Role: memoryv1.EpisodeSourceRole_EPISODE_SOURCE_ROLE_SITUATION,
    },
})
if err != nil {
    return err
}

memoryContext, err := client.SelectMemory(ctx, &memoryv1.SelectMemoryRequest{
    Scope: scope, RunRef: "run-1", SituationSourceEventRefs: []string{"source-1"},
})
if err != nil {
    return err
}

ack, err := client.RecordMemoryDelivery(ctx, &memoryv1.MemoryDeliveryReceipt{
    IdempotencyKey: "delivery-1", Scope: scope, RunRef: "run-1",
    MemoryContextRef: memoryContext.GetContextRef(),
    DeliveredMemoryRefs: []string{"memory-ref-actually-injected"},
})
if err != nil {
    return err
}

_, err = client.ReportOutcome(ctx, &memoryv1.ReportOutcomeRequest{
    IdempotencyKey: "outcome-1", Scope: scope, RunRef: "run-1", SourceGroupRef: "group-1",
    SourceRef: "outcome-source-1", Text: "observed result",
    ActorKind: memoryv1.SourceActorKind_SOURCE_ACTOR_KIND_USER, ActorRef: "user-1",
    DeliveryReceiptRefs: []string{ack.GetReceiptRef()}, RelatedSourceEventRefs: []string{"source-1"},
})
```

The default deadline is applied to token acquisition and the RPC without replacing a shorter caller deadline. The SDK writes exactly one `authorization: Bearer <token>` metadata value.

## Caller-owned transport

Tests and custom transports can inject the generated client directly:

```go
client, err := memorycore.New(generatedClient, memorycore.Config{TokenProvider: provider})
```

`Close` is an idempotent no-op for an injected client or a client created with `NewFromConnection`; callers retain that transport's lifecycle.
