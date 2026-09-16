# MemoryIndex Provider 合同

`MemoryIndex` 是可选、可丢弃、可重建的语义候选投影。当前配置的关系型 `CoreStore` 始终保存 Episode、Recollection、Disposition、Basis 与因果账；Provider 只扩展候选集合。

Go 进程内 Provider 实现公开包 `github.com/aig-dev/ChoraMem/memoryindex` 的 `Index`：

```go
type Index interface {
    Search(context.Context, Query) ([]Candidate, error)
    Upsert(context.Context, []Document) error
    Delete(context.Context, Scope, []Candidate) error
    Reset(context.Context) error
}
```

唯一 document kinds 是 `EPISODE`、`RECOLLECTION`、`DISPOSITION`。Document 只有 `{kind, ref, owner scope, text}`，Search response 只有有序 `{kind, ref}`。distance、similarity、confidence、strength 与 Provider-specific metadata 都不能跨过 port。

独立进程实现权威 `memoryindex.v1.MemoryIndex` Protobuf service。协议位于 `api/memoryindex/v1/memory_index.proto`，generated Go/gRPC bindings 位于 `gen/memoryindex/v1`，Core client Adapter 位于 `provider/memoryindexgrpc`。共享 token 使用 metadata `x-agent-rpc-token`，每次 RPC 使用调用方配置的 timeout。

## Select 规则

1. Core 对每个 situation query 分别查询 agent baseline 与适用的 exact relationship owner lane。
2. 每个 response 即使忽略 limit，也由 Core 截断到请求 bound。
3. Provider 为空、部分返回或失败时，bounded canonical `CoreStore` lane 保持不变。
4. 所有 refs 都在 owner locks 下回当前 `CoreStore` rehydrate，并重新检查 kind、tenant、owner、active status、application 与 live Basis。
5. Episode ref 可沿 canonical Basis 找到 linked active Recollection／Disposition。Recollection 仍须以自身正文匹配；对 Disposition，只有命中的 Episode 本身属于该版本的 live support Basis 时，才保留这条瞬时语义路径。它不能唤起同窗口兄弟 Memory。请求的 `episode_evidence_max_bytes` 非零时，Episode 还可以经下面的独立规则成为公开 Context 的引用证据。
6. learned Memory 合并去重后仍由 Core ranker 与 policy 决定最终选择；Provider 顺序不是 confidence 或写入资格。Episode evidence 则保留语义 rank 并按 query／owner lane order 稳定交错，不经字符 ranker 再排序。

## Select 的 Episode evidence

Episode evidence 是公开 Select 的可选输出，不是新的 Memory 种类。预算默认为 0；1–16384 启用，Core 最多返回 8 个完整 Episode，正文 UTF-8 总字节数不超过请求预算。首个过大的条目不会阻塞后续较短条目：Core 跳过整条并继续，不截断原文。

每个候选必须回关系库满足 exact owner、已物化且至少具有 Situation + AgentAct；重复 ref、缺失／无效 ref、跨 owner ref，以及包含本次 current Situation source 的 Episode 均被排除。正文由 canonical typed sources 按 `SITUATION -> AGENT_ACT -> OUTCOME` 与真实 actor kind 确定性生成。Provider 不返回正文，也不能绕过真源校验。

入选正文连同 Episode ref、query source ref 和顺序冻结到 `MemoryContext` 派生快照；Episode 后续追加 source 不改变旧 Context。Delivery 只允许并记录该 Context 实际投递的 exact refs，不会创建 Activation 或授予任何 Seed 因果资格。索引不可用时这条 evidence lane 为空，既有 Recollection／Disposition 选择保持可用；整个在线过程不调用生成式 AI。

语义命中 support Episode 所产生的 Seed Activation 与 Episode evidence 是两条独立路径。前者只让 exact linked Seed 成为 Context 条目；后者只提供历史原文。Harness 只投递 Episode evidence 而没有投递 Seed ref 时，Core 不把它算作 Seed Delivery，也不会开放重演、修订或抑制资格。

## ConsolidateWindow 规则

SourceEvent 不单独进入 VDB。它先在关系库中通过 typed links 物化为 Episode；Projector 再从当前真源确定性生成 `EPISODE` document。这样 Provider 不需要理解 source identity、actor 或 Episode binding，也不会出现第四种索引真源。

1. Core 用当前窗口中去重后的 Situation 文本查询普通候选；若最后一个完整当前 Episode 还具有唯一 non-Agent Outcome，则为推断型 Seed 同时查询 `Situation + AgentAct + Outcome` 因果视图和仅 `Situation` 的情境视图。Core 只按两份 Provider ordinal rank 稳定融合，不读取或持久化相似度。所有查询都只访问 exact owner lane，并发生在 owner lock 之前。
2. `EPISODE` ref 必须回关系库校验 exact tenant／agent／relationship 与完整 Situation + AgentAct。通过后，它以 `RELATED_EPISODE` 出现在 Worker 窗口，并沿 canonical Basis 扩展到已有 Memory。
3. `RECOLLECTION` ref 只有在同 owner 且仍为 active version 时，才进入可 `KEEP / TEXT` 的重固候选。
4. `DISPOSITION` ref 只有在同 owner 且仍 active 时，才进入 `ACTIVE_DISPOSITION_HINT`；索引命中本身永远不把它加入 allowed targets。
5. 普通 `RELATED_EPISODE` 可以与当前 Episode 一起支持跨窗口 Recollection 或兼容的 canonical 巩固。outcome-backed 推断型 Disposition 只读取 Core 标记的一个当前 anchor 与最多两份较早 candidates；每个 candidate 都必须被索引返回，并从关系库重取为完整 Situation + AgentAct + 唯一 non-Agent Outcome，且与 anchor／其他 candidate 的 `session_ref` 不同。scheduler overlap 带入冻结批次的较早 Episode 仍可成为 candidate；未命中的窗口兄弟、跨 owner、无／多 Outcome 和 Agent Outcome 均排除。
6. 已有 Disposition 的 `REENACT / REVISE / INHIBIT` 仍只由 canonical `Selection -> Delivery -> AgentAct -> optional non-Agent Outcome` 链授予；文本相似不能补链。当前真实 USER Situation 可独立授予 `ADAPT` 的结构资格，但是否本人长期要求只由同一 Worker 判断；普通效果表扬不能走 ADAPT 绕过反馈链。
7. Provider 为空、因果视图报错或两个视图都没有返回 Episode ref 时，不建立 focused formation group，原 canonical 巩固路径保持可用。情境视图单独报错时保留因果视图旧顺序。Provider 一旦返回过可融合的 Episode ref，Core 就标记 anchor；若所有候选随后因陈旧、跨 owner、同 session 或 Outcome 不完整而被淘汰，本次 focused 形成 no-op，不退回混杂窗口形成 Seed。其他 canonical 变化仍可继续。

两次相近表达的最小流转如下：

```text
第一次 SourceEvents -> Episode A -> EPISODE projection
第二次当前 Episode B 的完整因果视图 + Situation 视图 -> rank fusion -> Episode A ref
Core rehydrate A 及其唯一 non-Agent Outcome -> 标记 B=anchor、A=candidate
focused Worker 只看 B/A 两个无 ID 因果对并判断共同倾向
程序绑定 B/OutcomeB/A/OutcomeA；Core 要求最终 BASIS 序列精确相等后提交
```

## 独立投影生命周期

canonical transaction 会把 `UPSERT`／`DELETE` 操作写入 `memory_index_operations`。操作只冻结 deterministic id、logical stream、连续 sequence、kind/ref、exact owner 与 lease/retry/ack state，不保存 canonical text。Episode materialization 与后续 source link 变化写 `UPSERT EPISODE`；Recollection／Disposition FORM 写 `UPSERT`；revision 在同一 root stream 先写旧版本 `DELETE`，再写新版本 `UPSERT`。ADAPT 改变正文时沿同一 root stream 更新版本投影；同正文 ADAPT 为 no-op，KEEP、REENACT、INHIBIT 不改变被索引文本，因此均不入队。

memoryd 只在配置 Provider 时启动独立 `Projector`。UPSERT 执行时从当前 `CoreStore` 重取 exact active kind/ref/owner/text；missing、stale、superseded 版本直接 ack，不能复活。Provider 失败只重试投影操作，不改变已完成的 consolidation Job。未配置 Provider 时 canonical 写仍然入队等待。

`Projector.Rebuild` 只 reset disposable Provider，并按 `EPISODE -> RECOLLECTION -> DISPOSITION` 与 stable ref 分页枚举 canonical documents；Episode text 从 typed source links 确定性生成。Rebuild 不修改或 reset 关系库真源，结束后保留的 queued deltas 继续对齐并发变化。
