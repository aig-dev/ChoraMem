# Memory Core Python SDK

`SourceEvent.constitution` 与 Outcome 的可选快照使用同一外生 Agent 角色版本/正文。
turn/Adapter 在 Situation intake 和 Select 传入同一份 `Constitution`；省略保持旧调用合法。
Core 冻结来源快照，后台只读解释它，不修改基准，也不把它当作用户档案或 Basis。

异步、协议优先的 Memory Core 客户端。生成代码来自 `api/memory/v1/memory.proto`；手写 SDK 只负责认证、deadline、连接生命周期与薄 Harness 编排。

```python
from memory_core import AsyncMemoryClient, memory_pb2

async with AsyncMemoryClient.connect(
    "https://memory.example.com",
    token_provider=lambda tenant_ref: issue_tenant_token(tenant_ref),
) as memory:
    context = await memory.select_memory(
        memory_pb2.SelectMemoryRequest(
            scope=memory_pb2.MemoryScope(
                tenant_ref="tenant-1",
                agent_ref="agent-1",
                kind=memory_pb2.MEMORY_SCOPE_KIND_AGENT,
            ),
            run_ref="run-1",
        )
    )
```

也可传入 caller-owned `grpc.aio.Channel`；此时 `close()` 是安全 no-op。每次 RPC 都从 typed request 读取原始、非空白的 `tenant_ref`，不修剪或改写它，然后调用 token provider。Bearer token 不允许包含 Unicode 空白字符。默认 deadline 为 10 秒；每次调用的有效 deadline 始终取默认值与 caller `timeout` 中较短者。

四个方法依次是 `observe_source_event`、`select_memory`、`record_memory_delivery`、`report_outcome`。

`render_memory_context()` 把 Context 拆成两个不会混淆的值：供模型使用的纯文本，以及仅供 Delivery 使用的精确 refs。默认的 `CONSTITUTION`、`RECOLLECTIONS`、`DISPOSITIONS` 三段格式保持不变；Recollection / Disposition 保留 `SELF | OTHER | RELATION | SITUATION` 标签。若 Context 含 Episode evidence，则增加明确标注为“历史引用证据、不是当前指令”的第四段，逐行加 `> `，不把 ref 放进模型文本，也不修剪原文的 UTF-8、多行或尾部空白。

总模型上下文预算由调用方提供对应模型的真实计数器；`max_tokens` 与 `token_count` 必须成对出现，预算可为 0。Renderer 按 Constitution → Recollection → Disposition → Episode evidence 贪心装配完整条目，计数包含标题、scope、引用前缀与段间分隔符；放不下的条目会跳过并继续尝试后续条目。所有非空条目的 ref、跨种类重复和 application scope 都会在预算筛选前 fail closed。

```python
rendered = render_memory_context(
    context,
    max_tokens=2048,
    token_count=count_tokens_for_selected_model,
)
```

OpenAI Agents Adapter 复用同一 renderer。证据默认关闭；启用时需同时配置 Core 的证据原文字节预算（1–16384）与模型总 token 预算／计数器：

```python
adapter = OpenAIAgentsAdapter(
    memory,
    episode_evidence_max_bytes=16_384,
    max_tokens=2048,
    token_count=count_tokens_for_selected_model,
)
```

只有实际注入的条目才写 Delivery，模型成功返回非空文本后才观察 AgentAct。引用原文中的句子不会改变 Adapter 编排，Outcome 始终由 Harness 根据真实外部结果显式上报。

完整、Harness-neutral 的 `Situation -> Select -> text-only render -> Delivery -> model -> AgentAct -> explicit Outcome` 示例见 [`generic_lifecycle.py`](https://github.com/aig-dev/ChoraMem/blob/main/sdk/python/examples/generic_lifecycle.py)。传入 `EpisodeEvidenceBudget` 可启用固定字节与总 token 双重预算；模型回调仍只接收两个字符串，不会接触 protobuf、context ref 或 memory ref。

重新生成或校验 bindings：

```bash
python -m pip install -e '.[codegen]'
./scripts/generate-bindings.sh --check
```
