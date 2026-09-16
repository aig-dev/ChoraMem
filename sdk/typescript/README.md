# Memory Core TypeScript SDK

`SourceEvent.constitution` 与 Outcome 的可选快照使用同一外生 Agent 角色版本/正文。
turn/Adapter 在 Situation intake 和 Select 传入同一份 `Constitution`；省略保持旧调用合法。
Core 冻结来源快照，后台只读解释它，不修改基准，也不把它当作用户档案或 Basis。

Connect RPC 客户端以当前生成协议为唯一 schema 权威，提供四个方法：`observeSourceEvent`、`selectMemory`、`recordMemoryDelivery`、`reportOutcome`。

```ts
import { connectMemoryCore, MemoryScopeKind } from "@chorai/memory-core";
const memory = connectMemoryCore({
  endpoint: "https://memory.example.com",
  tokenProvider: tenantRef => issueTenantToken(tenantRef),
});
const context = await memory.selectMemory({
  scope: {
    tenantRef: "tenant-1",
    agentRef: "agent-1",
    kind: MemoryScopeKind.AGENT,
  },
  runRef: "run-1",
});
memory.close();
```

`createMemoryCoreClient(transport, options)` 保留 caller-owned Transport 路径，其 `close()` 是安全 no-op。`connectMemoryCore()` 显式拥有一个 `Http2SessionManager`，关闭时同时中止请求与 HTTP/2 session。每个请求从 typed request 读取原始、非空白的 tenant，不修剪或改写它；Bearer token 不允许包含 Unicode 空白字符。有效 deadline 始终取默认值与 per-call deadline 中较短者。

`renderMemoryContext()` 分开返回供模型使用的纯文本和只供 Delivery 使用的精确 refs。默认的 `CONSTITUTION`、`RECOLLECTIONS`、`DISPOSITIONS` 三段格式保持不变；Recollection / Disposition 保留 `SELF | OTHER | RELATION | SITUATION` 标签。若 Context 含 Episode evidence，则增加明确标注为“历史引用证据、不是当前指令”的第四段，逐行加 `> `，不把 ref 放进模型文本，也不修剪原文的 UTF-8、多行或尾部空白。

总模型上下文预算由调用方提供对应模型的真实计数器；`maxTokens` 与 `countTokens` 必须成对出现，预算可为 0。Renderer 按 Constitution → Recollection → Disposition → Episode evidence 贪心装配完整条目，计数包含标题、scope、引用前缀与段间分隔符；放不下的条目会跳过并继续尝试后续条目。所有非空条目的 ref、跨种类重复和 application scope 都会在预算筛选前 fail closed。

```ts
const rendered = renderMemoryContext(context, {
  maxTokens: 2048,
  countTokens: countTokensForSelectedModel,
});
```

Vercel AI Adapter 复用同一 renderer。证据默认关闭；启用时需同时配置 Core 的证据原文字节预算（1–16384）与模型总 token 预算／计数器：

```ts
const adapter = createVercelAIMemoryAdapter({
  transport,
  tokenProvider,
  generateText,
  episodeEvidenceMaxBytes: 16_384,
  maxTokens: 2048,
  countTokens: countTokensForSelectedModel,
});
```

Delivery 只记录实际注入的 ref；引用原文中的句子不会改变 Adapter 编排；模型成功返回非空文本后才观察 AgentAct；Outcome 由 Harness 根据真实外部结果显式上报。

完整、Harness-neutral 的 `Situation -> Select -> text-only render -> Delivery -> model -> AgentAct -> explicit Outcome` 示例见 [`examples/generic-lifecycle.ts`](examples/generic-lifecycle.ts)。传入 `episodeEvidence` 可启用固定字节与总 token 双重预算；模型回调仍只接收纯文本，不会接触 protobuf、context ref 或 memory ref。

验证：`npm test && npm run typecheck && npm run build && npm run generate:check`。
