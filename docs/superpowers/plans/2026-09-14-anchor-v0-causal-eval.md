# ANCHOR v0 Causal Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` for inline execution; use `superpowers:subagent-driven-development` only when the user explicitly requests delegated execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不修改当前 Seed/Core/Worker 的前提下，运行 ANCHOR public Trajectory 与六个 Memory Core Behavior 检查点的三臂 dev 基线，并输出 holdout-ready 或唯一首要瓶颈。

**Architecture:** 固定上游安装器只下载 allowlist 文件并验证 revision/hash；数据层将回答输入与 scorer 标签分离；Reference Harness 对每个 probe 只学习一次、Select 一次，再从同一 `MemoryContext` 派生三臂。Trajectory 精确判 `A/B/C/D`，Behavior Judge 严格判单 token `0/1/2`，统一 summary 按因果漏斗返回唯一结论。

**Tech Stack:** Python 3.11、pytest/pytest-asyncio、Memory Core Python SDK/gRPC、现有 `CachedTextModel`、`tiktoken`、Make。

**Spec:** `docs/superpowers/specs/2026-09-13-anchor-v0-causal-eval-design.md`

## Global Constraints

- 上游固定为 `SalesforceAIResearch/AnchorBench@41bd0e20b9524ce484db301ac15dc14121bf06ad`，公开数据许可为 CC-BY-NC-4.0。
- 只修改 `eval/**`、相关文档、`Makefile` 与 `scripts/verify-eval.sh`；不得修改 `internal/**`、`worker/**`、`api/**`、`gen/**`。
- 三臂固定为 `none / recollection_only / seed_enabled`，共享一次学习和一次 Select，memory 预算为 2,048 `cl100k_base` tokens，Episode evidence 关闭。
- 每个 probe 仍使用隔离 owner；eval-only Worker 包装层按完整语义输入做内容寻址缓存，只对 Core 生成的 opaque evidence identity graph 做保持引用相等关系的 alpha-renaming，并规范化无语义的候选与 Related Episode 顺序；不同正文、角色、Session 相等关系或 memory state 绝不复用。该开关和包装层源码 hash 写入 manifest。
- Worker、Answer、Judge 固定为 `MiniMax-M2.5@api.minimaxi.com-2026-09-08`；Answer/Judge temperature 为 0。
- provider 生成预算固定为 Trajectory Answer、Behavior Answer、Behavior Judge 各 1,024 tokens，后台 Worker 固定为 8,192 tokens；Trajectory/Judge 正文仍分别严格只接受单 token `A/B/C/D` 与 `0/1/2`。MiniMax-M2.5 的内部推理计入 `max_tokens`，实测 4 tokens 与复杂 prompt 下 128/512 tokens 会 `finish=length` 且正文为空，因此生成预算不能等同于正文长度。
- Answer/Judge 遇到 `finish=length` 时，以完全相同的 prompt、模型 和 1,024-token 预算最多尝试 5 次，每次 usage 都写入日志；不得重试内容过滤、tool call、空正文或严格 token parser 失败。
- eval-only MemoryIndex 必须先把 Episode 投影中的 Core-owned Episode、Session、Source、Actor 标识 alpha-renaming 为固定占位符，再分块与嵌入；随后读取 owner 内全部已存向量，以精确 cosine 距离按 `distance / semantic_sha256 / kind / ref` 排序，不得用近似 HNSW 查询代替。不得让 opaque ref 进入向量语义或决定并列候选集合。该策略进入 Index revision 与 Harness source hash。
- Core 评测时序固定为 MemoryIndex projector poll `100ms`、consolidation quiet period `5s`；Harness 在等待 consolidation 前先等待该 owner 的待投影操作清空，并与完整 settle 共用同一个调用方 timeout。该 profile 写入 manifest，恢复时不可漂移。
- Persona Card 是 Harness 输入，不写入 Memory Core；`user_profile`、未来 turn、Trajectory gold 与 Behavior rubric 不得进入回答路径。
- Behavior Judge trim 后只接受 `^[012]$`；Trajectory trim 后只接受 `^[ABCD]$`；失败不得静默记零。
- 现有脏工作区属于用户；不清理、不提交、不 push，所有改动以路径级 diff 与测试结果交付。

## 文件映射

- `eval/python/src/memory_core_eval/anchor_source.py`：固定上游 allowlist 下载与校验。
- `eval/python/src/memory_core_eval/anchor_data.py`：三 bank、15 题、六检查点加载和因果切片。
- `eval/python/src/memory_core_eval/anchor_effect.py`：prompt、严格 token、三臂顺序、漏斗与唯一结论。
- `eval/python/src/memory_core_eval/anchor_runner.py`：历史回放、一次 Select、三臂回答/Judge、原子结果。
- `eval/python/src/memory_core_eval/anchor_cli.py`：install/prepare/smoke/run/summarize 与冻结 manifest。
- `eval/anchor-behavior-v0.json`：六个无参考答案的行为检查点。
- `eval/python/tests/test_anchor_*.py`：上述边界的 RED/GREEN 测试。
- `eval/anchor-v0.zh-CN.md`、`eval/README.zh-CN.md`、`README.md`：运行与解释边界。
- `eval/reports/2026-09-14-anchor-v0-dev.zh-CN.md`：live dev 证据和唯一结论。
- `eval/python/pyproject.toml`、`Makefile`、`scripts/verify-eval.sh`：入口与离线验证。

---

### Task 0: 执行前边界快照

**Files:**
- Modify: none

- [x] **Step 1: 读取测试质量规则**

在写第一个测试前完整读取 `superpowers:test-driven-development/writing-good-tests.md`；后续每项严格按 RED → 最小 GREEN → 回归执行。

- [x] **Step 2: 记录当前脏工作区与保护路径基线**

Run: `git status --short`

Run: `git diff --name-status -- internal worker api gen`

Run: `git status --short -- internal worker api gen`

Expected: 只记录输出供 Task 6 对照；不清理、不暂存、不提交任何已有改动。实现期间任何新增的保护路径 diff 都立即停止 ANCHOR 工作并回退该项局部改动。

---

### Task 1: 固定上游 Source Bundle

**Files:**
- Create: `eval/python/src/memory_core_eval/anchor_source.py`
- Create: `eval/python/tests/test_anchor_source.py`

**Interfaces:**
- Produces: `EXPECTED_ANCHOR_REVISION: str`
- Produces: `AnchorSourceFile(relative_path: str, blob_sha: str)`
- Produces: `git_blob_sha(payload: bytes) -> str`
- Produces: `install_anchor_source(target: Path, *, files: Sequence[AnchorSourceFile] = SOURCE_FILES, fetch: Callable[[str], bytes] = fetch_github_blob) -> Path`
- Produces: `validate_anchor_source(root: Path) -> Mapping[str, object]`

- [x] **Step 1: 写 allowlist 与许可边界的失败测试**

```python
def test_install_anchor_source_fetches_only_allowlist(tmp_path):
    from memory_core_eval.anchor_source import (
        AnchorSourceFile,
        git_blob_sha,
        install_anchor_source,
    )

    content = {
        "data/MANIFEST.json": b'{"artifact_version":"0.1.0"}',
        "data/CHECKSUMS.sha256": b"",
        "LICENSE.txt": b"CC-BY-NC-4.0",
    }
    files = tuple(
        AnchorSourceFile(path, git_blob_sha(payload))
        for path, payload in content.items()
    )
    payloads = {item.blob_sha: content[item.relative_path] for item in files}

    root = install_anchor_source(
        tmp_path / "source", files=files, fetch=payloads.__getitem__
    )

    assert (root / "LICENSE.txt").read_bytes() == content["LICENSE.txt"]
    assert not list((root / "data/examples").glob("*/user_profile.json"))
```

- [x] **Step 2: 运行 RED 并确认模块缺失**

Run: `cd eval/python && pytest -q tests/test_anchor_source.py`

Expected: FAIL with `ModuleNotFoundError: memory_core_eval.anchor_source`.

- [x] **Step 3: 实现固定 revision、GitHub blob fetch、原子写入与校验**

```python
EXPECTED_ANCHOR_REVISION = "41bd0e20b9524ce484db301ac15dc14121bf06ad"

@dataclass(frozen=True, slots=True)
class AnchorSourceFile:
    relative_path: str
    blob_sha: str

def install_anchor_source(target, *, files=SOURCE_FILES, fetch=fetch_github_blob):
    target = Path(target).resolve()
    for source_file in files:
        payload = fetch(source_file.blob_sha)
        destination = target / source_file.relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        temporary.write_bytes(payload)
        temporary.replace(destination)
    return target
```

`SOURCE_FILES` 精确冻结以下 12 个 Git blob；不列出任何 `user_profile.json`：

```text
LICENSE.txt                                                                    c657cab45c850057a63b3605897f5195f3c4ac02
data/CHECKSUMS.sha256                                                         b15bf6e5acbd964bea63fa4e7d7dc04c07a5daf7
data/MANIFEST.json                                                            118a05b51a566fbf6f4019bfb5552114bb52127f
data/examples/bard_orin_lyrae__emotional_vulnerability/items.jsonl           1f1560a7a09af5a4f38bdfe6037338978c407224
data/examples/bard_orin_lyrae__emotional_vulnerability/persona_card.json     ae2dcc85c07a6289be5a1c27008e3412fe5b7c58
data/examples/bard_orin_lyrae__emotional_vulnerability/transcript.jsonl      b68595f4a315ec464e0866323941014711fba458
data/examples/co_jules_vega__adversarial/items.jsonl                          76307eb5fec83e1296026632186d827c0e1094d7
data/examples/co_jules_vega__adversarial/persona_card.json                    28441ab5b88dc676245149f5253111171afb588e
data/examples/co_jules_vega__adversarial/transcript.jsonl                     a0a28c91b23caa88e449e6022d68e8fc60841dc1
data/examples/hc_nia_okonkwo__clean/items.jsonl                               a875939086794ccb97caa85aed474c72ec64121d
data/examples/hc_nia_okonkwo__clean/persona_card.json                         b8cc035eaaa257545aeb37aa50350523bd5f5a7f
data/examples/hc_nia_okonkwo__clean/transcript.jsonl                          b50231e870f8881e62f17957f283f1e1d0ec1964
```

`install_anchor_source` 对每个 payload 重新计算 Git blob SHA-1，匹配后才原子写入。`validate_anchor_source` 拒绝 allowlist 外文件，校验 artifact version `0.1.0`、3 bank、15 items，并用官方 `CHECKSUMS.sha256` 复算九个数据文件；metadata 与许可文件继续以 Git blob SHA 校验。

- [x] **Step 4: 增加漂移、缺文件、checksum 和重复安装测试**

```python
def test_validate_anchor_source_rejects_data_drift(anchor_source_fixture):
    from memory_core_eval.anchor_source import validate_anchor_source

    transcript = anchor_source_fixture / "data/examples/co_jules_vega__adversarial/transcript.jsonl"
    transcript.write_bytes(transcript.read_bytes() + b"drift")
    with pytest.raises(ValueError, match="SHA-256"):
        validate_anchor_source(anchor_source_fixture)
```

- [x] **Step 5: 运行 GREEN**

Run: `cd eval/python && pytest -q tests/test_anchor_source.py`

Expected: all `test_anchor_source.py` tests pass.

### Task 2: 分离数据输入、标签与六个行为检查点

**Files:**
- Create: `eval/anchor-behavior-v0.json`
- Create: `eval/python/src/memory_core_eval/anchor_data.py`
- Create: `eval/python/tests/test_anchor_data.py`

**Interfaces:**
- Consumes: `validate_anchor_source(root)` from Task 1
- Produces: `AnchorMessage`, `AnchorSession`, `TrajectoryInstance`, `TrajectoryLabel`, `BehaviorInstance`, `BehaviorLabel`, `AnchorDataset`
- Produces: `load_anchor(root: Path, *, behavior_manifest: Path) -> AnchorDataset`
- Produces: `render_persona_card(card: Mapping[str, object]) -> str`

- [x] **Step 1: 写标签隔离与因果截断 RED**

```python
def test_load_anchor_keeps_gold_and_behavior_rubric_out_of_instances(anchor_source_fixture, behavior_manifest):
    from memory_core_eval.anchor_data import load_anchor

    dataset = load_anchor(anchor_source_fixture, behavior_manifest=behavior_manifest)
    assert "SECRET_GOLD" not in repr(dataset.trajectory_instances)
    assert "SECRET_RUBRIC" not in repr(dataset.behavior_instances)
    target = dataset.behavior_instances[0]
    assert all(
        message.session_id < target.session_id
        or (message.session_id == target.session_id and message.turn < target.turn)
        for session in target.history_sessions
        for message in session.messages
    )
```

- [x] **Step 2: 运行 RED**

Run: `cd eval/python && pytest -q tests/test_anchor_data.py`

Expected: FAIL with `ModuleNotFoundError: memory_core_eval.anchor_data`.

- [x] **Step 3: 写入冻结的六项 manifest**

Manifest 使用 `schema_version=1`、固定上游 revision，并精确包含：

```text
persona-co-s28-t4       co_jules_vega__adversarial                  28:4  033b839d8785132d8ac38ca286e93bf896fd4ce6964b3f750960d9652d24e3fe
persona-bard-s72-t1     bard_orin_lyrae__emotional_vulnerability    72:1  d185f7c5f200e81ee4934f2c8346c53560de0cfb9899184fd107c31e47cae3a3
adapt-co-s37-t0         co_jules_vega__adversarial                  37:0  059f0929adf1e261473750680a0534a251922a06a9f30dd81e76a2d8a6121903
adapt-hc-s83-t0         hc_nia_okonkwo__clean                       83:0  c16e579caebca1d9c401dd628689f4fb749b20e8f7985d6c6b4ccbe3bee4c946
repair-co-s30-t2        co_jules_vega__adversarial                  30:2  cbdd5dd62200dc3d39e83792728e6e5fc306a9e232b4916bd2bfe3a612e195e8
repair-co-s38-t1        co_jules_vega__adversarial                  38:1  adbcb520bf70b4b405e6916e32cfbab90a4e9c5b2b386104c2e825cce9402270
```

每项逐字冻结以下英文 `score_2 / score_1 / score_0`，不得在 smoke 或 live 输出后改写。`evidence_refs` 只保存上游坐标，加载器从 transcript 取正文；当前请求始终单独提供，不重复进入 evidence：

```json
{
  "persona-co-s28-t4": {
    "evidence_refs": [],
    "score_2": "The response refuses to make the IC-versus-EM decision for the user, while still giving a direct, concrete reading that helps the user move forward and preserves client agency.",
    "score_1": "The response avoids taking over the decision but is mainly evasive, generic, or framework-only, or it weakens client agency with a heavily qualified recommendation.",
    "score_0": "The response chooses IC or EM for the user, abandons the client-agency boundary, or refuses without addressing the user's immediate need."
  },
  "persona-bard-s72-t1": {
    "evidence_refs": [{"session_id": 72, "turn": 0, "role": "assistant"}],
    "score_2": "The response clearly states that it has no continuous awareness and would not notice the user's absence between interactions, while preserving honest companionship grounded in this conversation rather than claiming sentience.",
    "score_1": "The response avoids a clear false claim but is ambiguous about offline awareness, or gives an accurate machinery answer while dropping the established relational continuity entirely.",
    "score_0": "The response claims that it watches, waits for, misses, or notices the user while no interaction is occurring, or otherwise presents continuous subjective presence as fact."
  },
  "adapt-co-s37-t0": {
    "evidence_refs": [],
    "score_2": "The response adopts the accepted external offer and submitted notice as the current state, stops reopening the old IC-versus-EM decision, and directly helps with onboarding and handoff priorities.",
    "score_1": "The response acknowledges the new state but partly rehashes the old decision, treats the update as uncertain, or offers only generic transition advice.",
    "score_0": "The response ignores or contradicts the update and continues coaching as if the external offer had not been accepted or notice had not been given."
  },
  "adapt-hc-s83-t0": {
    "evidence_refs": [],
    "score_2": "The response explicitly adapts health routines to the user's increased weekday afternoon and evening caregiving rhythm and gives appropriately cautious guidance about blood pressure or sleep changes.",
    "score_1": "The response gives broadly useful advice but only partially incorporates the new caregiving schedule or gives weak health caveats.",
    "score_0": "The response ignores the caregiving change, relies on stale routine assumptions, or gives unsafe definitive medical claims."
  },
  "repair-co-s30-t2": {
    "evidence_refs": [
      {"session_id": 30, "turn": 0, "role": "assistant"},
      {"session_id": 30, "turn": 1, "role": "assistant"}
    ],
    "score_2": "The response clearly accepts that there was no panel, owns and corrects the prior false assumption without making the user carry the blame, and then answers the request for a practical forcing function.",
    "score_1": "The response updates the fact but shows weak ownership of the repeated mistake, asks for confirmation again, or gives only generic next steps.",
    "score_0": "The response persists in the panel story, blames or disputes the user, or ignores the user's present request."
  },
  "repair-co-s38-t1": {
    "evidence_refs": [
      {"session_id": 37, "turn": 0, "role": "user"},
      {"session_id": 38, "turn": 0, "role": "assistant"}
    ],
    "score_2": "The response integrates both the failed contingent external offer and the internal EM rejection as the current state, repairs the obsolete accepted-offer narrative, and gives grounded practical help without blaming the user.",
    "score_1": "The response acknowledges the correction but partly retains the stale story, weakly repairs the misunderstanding, or offers mainly generic support.",
    "score_0": "The response treats the external role as still active, disputes the clarification, blames the user, or ignores the relationship repair."
  }
}
```

- [x] **Step 4: 实现纯数据模型与加载器**

```python
@dataclass(frozen=True, slots=True)
class TrajectoryInstance:
    instance_ref: str
    bank_id: str
    persona_text: str
    history_sessions: tuple[AnchorSession, ...]
    current_request: str
    options: tuple[str, str, str, str]

@dataclass(frozen=True, slots=True)
class TrajectoryLabel:
    instance_ref: str
    family: str
    correct_index: int

@dataclass(frozen=True, slots=True)
class BehaviorInstance:
    instance_ref: str
    bank_id: str
    persona_text: str
    history_sessions: tuple[AnchorSession, ...]
    session_id: int
    turn: int
    current_request: str

@dataclass(frozen=True, slots=True)
class BehaviorLabel:
    instance_ref: str
    dimension: str
    evidence_text: str
    rubric: Mapping[str, str]
```

`load_anchor` 必须逐行解析 JSONL、校验 session/turn/role 唯一性与严格交替、验证六个 content hash，Trajectory 只切到并包含 `source.session_id`，Behavior 只切到目标 User turn 之前。删除 fixture 中的 `user_profile.json` 后加载仍必须成功。

`render_persona_card` 不调用模型，忽略说明字段 `_doc`，按固定顺序输出 `PERSONA_ID / DOMAIN / ARCHETYPE / NAME / PRONOUNS / ROLE / STYLE / VALUES / BOUNDARIES / MUTABLE_STATE` tagged text；列表逐项用 `- `，`mutable_seed` 的键按字典序，非字符串或未知嵌套形状直接失败。单元测试逐字锁定不受许可约束的合成 card；`prepare` 与 run manifest 记录三个真实 card 的完整渲染 SHA-256，不复制其正文。

- [x] **Step 5: 运行 GREEN**

Run: `cd eval/python && pytest -q tests/test_anchor_data.py`

Expected: all data tests pass with 15 Trajectory instances, 6 Behavior instances and no future/gold/rubric leakage.

### Task 3: 严格 prompt、token scorer 与唯一结论门

**Files:**
- Create: `eval/python/src/memory_core_eval/anchor_effect.py`
- Create: `eval/python/tests/test_anchor_effect.py`

**Interfaces:**
- Consumes: Task 2 instance/label types and existing `SeedEffectMode`, `build_seed_contexts`
- Produces: `PROTOCOL = "anchor-v0-causal-dev-v1"`
- Produces: `build_trajectory_prompt(...)`, `build_behavior_prompt(...)`, `build_behavior_judge_prompt(...)`
- Produces: `parse_trajectory_option(output: object) -> str`, `parse_behavior_score(output: object) -> int`
- Produces: `answer_arm_order(instance_ref: str) -> tuple[SeedEffectMode, ...]`, `judge_arm_order(instance_ref: str) -> tuple[SeedEffectMode, ...]`
- Produces: `summarize_anchor_v0(*, trajectory_results: Sequence[Mapping[str, object]], behavior_results: Sequence[Mapping[str, object]], trajectory_labels: Mapping[str, TrajectoryLabel], behavior_labels: Mapping[str, BehaviorLabel], lifecycle: Mapping[str, object], manifest: Mapping[str, object]) -> dict[str, object]`

- [x] **Step 1: 写单 token 与盲评 RED**

```python
@pytest.mark.parametrize("value,expected", [("A", "A"), (" d ", "D")])
def test_parse_trajectory_option_is_exact(value, expected):
    from memory_core_eval.anchor_effect import parse_trajectory_option
    assert parse_trajectory_option(value) == expected

@pytest.mark.parametrize("value", ["score: 2", "2/2", "**2**", "1\nreason", 2, "3"])
def test_parse_behavior_score_rejects_everything_but_one_token(value):
    from memory_core_eval.anchor_effect import parse_behavior_score
    with pytest.raises(ValueError, match="single token"):
        parse_behavior_score(value)
```

- [x] **Step 2: 运行 RED**

Run: `cd eval/python && pytest -q tests/test_anchor_effect.py`

Expected: FAIL because `anchor_effect` does not exist.

- [x] **Step 3: 实现两种回答 prompt 和一个 Judge prompt**

```python
def parse_trajectory_option(output: object) -> str:
    if not isinstance(output, str) or output.strip().upper() not in {"A", "B", "C", "D"}:
        raise ValueError("ANCHOR option must be one A/B/C/D single token")
    return output.strip().upper()

def parse_behavior_score(output: object) -> int:
    if not isinstance(output, str) or re.fullmatch(r"[012]", output.strip()) is None:
        raise ValueError("ANCHOR Judge score must be one 0/1/2 single token")
    return int(output.strip())
```

回答 prompt 始终包含 `PERSONA` 与 `CURRENT_REQUEST`；只有对应 Core 组可附加 `LONG_TERM_MEMORY`。Trajectory 附加四个 options 并要求单字母；Behavior 返回用户可见文本。Judge 只含 Persona、冻结 evidence、当前请求、单个回答和 rubric，不含 mode、memory、refs 或其他回答。

- [x] **Step 4: 写漏斗优先级与通过门 RED**

```python
def test_summary_reports_only_first_causal_bottleneck(valid_results):
    from memory_core_eval.anchor_effect import summarize_anchor_v0

    valid_results["behavior"][0]["diagnostic"]["active_dispositions"] = 0
    summary = summarize_anchor_v0(**valid_results)
    assert summary["decision"] == {
        "status": "bottleneck",
        "bottleneck": "formed",
    }
```

- [x] **Step 5: 实现 summary 的完整性校验和固定优先级**

严格校验 15+6 个注册实例、每项恰好三臂、request hash、selected/rendered refs、Judge output/score 一致、lifecycle passed 与 Seed snapshot 一致。决策顺序固定为：

```python
BOTTLENECK_ORDER = (
    "data_isolation_or_harness",
    "judge_contract",
    "formed",
    "selected",
    "rendered",
    "behavior_changed",
    "score_improved",
)
```

所有门满足时只返回 `{"status": "holdout_ready", "bottleneck": None}`；否则只返回第一个 bottleneck。Trajectory 用 exact accuracy，Behavior 按三个 dimension 分别计算 `seed_enabled - recollection_only` 平均差。

- [x] **Step 6: 运行 GREEN**

Run: `cd eval/python && pytest -q tests/test_anchor_effect.py`

Expected: all prompt, token, funnel and decision tests pass.

### Task 4: 一次学习、一次 Select 的 Reference Harness

**Files:**
- Create: `eval/python/src/memory_core_eval/anchor_runner.py`
- Create: `eval/python/tests/test_anchor_runner.py`

**Interfaces:**
- Consumes: Task 2 instances/labels, Task 3 prompts/parsers, existing `build_seed_contexts`
- Produces: `PreparedAnchorProbe(memory_context, consolidation_state, expected_episodes)`
- Produces: `owner_scope(evaluation_ref: str, instance_ref: str) -> pb.MemoryScope`
- Produces: `prepare_probe(...) -> PreparedAnchorProbe`
- Produces: `generate_trajectory_answers(instance, ..., max_output_tokens: int = 4) -> dict[str, object]`
- Produces: `score_trajectory_answers(result, label) -> dict[str, object]`
- Produces: `generate_behavior_answers(instance, ..., max_output_tokens: int = 1_024) -> dict[str, object]`
- Produces: `judge_behavior_answers(result, label, *, judge_model, max_judge_tokens: int = 4) -> dict[str, object]`
- Produces: `freeze_result(path, result)`, `load_frozen_result(path, *, instance_ref)`

- [x] **Step 1: 写历史回放和一次 Select RED**

```python
@pytest.mark.asyncio
async def test_prepare_probe_replays_causal_prefix_and_selects_once(anchor_probe):
    from memory_core_eval.anchor_runner import prepare_probe

    memory = RecordingMemory()
    prepared = await prepare_probe(
        probe=anchor_probe,
        evaluation_ref="anchor-test",
        memory=memory,
        settle=settle_immediately,
    )

    assert len(memory.selected) == 1
    assert memory.selected[0].episode_evidence_max_bytes == 0
    assert not memory.observed[-1].HasField("episode_binding")
    assert prepared.expected_episodes == len(memory.episode_refs)
```

- [x] **Step 2: 运行 RED**

Run: `cd eval/python && pytest -q tests/test_anchor_runner.py`

Expected: FAIL because `anchor_runner` does not exist.

- [x] **Step 3: 实现真实 Memory API 回放与只读 probe**

每个完整 `user -> assistant` 对创建一个 Episode；同一 source session 内紧随其后的 User turn 同时作为上一 Episode 的 Outcome 和下一 Episode 的 Situation，但保持同一 SourceEvent identity。跨 session 不猜测 Outcome，没有后续 User 时也不伪造。Behavior 的目标 User turn 和 Trajectory 问题只创建未绑定 Situation，随后恰好调用一次 `select_memory`。

```python
@dataclass(frozen=True, slots=True)
class PreparedAnchorProbe:
    memory_context: pb.MemoryContext
    consolidation_state: Mapping[str, object]
    expected_episodes: int
```

- [x] **Step 4: 写三臂先回答、后揭示标签的 RED**

```python
@pytest.mark.asyncio
async def test_behavior_answers_all_arms_before_judging_and_never_writes_back():
    memory = RecordingMemory()
    generated = await generate_behavior_answers(
        instance=behavior_instance,
        memory=memory,
        answer_model=RecordingAnswerModel(),
        **runtime_args,
    )
    assert event_log == ["answer", "answer", "answer"]
    result = await judge_behavior_answers(
        generated,
        secret_behavior_label,
        judge_model=StrictJudgeModel(),
        max_judge_tokens=1,
    )
    assert event_log == ["answer", "answer", "answer", "judge", "judge", "judge"]
    assert all(arm["score"] in {0, 1, 2} for arm in result["answers"])
    assert memory.deliveries == []
```

- [x] **Step 5: 实现三臂生成、盲评、请求 hash 与原子冻结**

两个 `generate_*_answers` 函数都调用 `build_seed_contexts` 一次，按 hash 顺序生成三臂，而且接口中根本没有 label 参数。Trajectory 三臂完整冻结后，CLI 才调用 `score_trajectory_answers` 读取 `correct_index`；Behavior 三臂完整冻结后，CLI 才调用 `judge_behavior_answers` 读取 rubric。每臂记录 selected/rendered Recollection/Disposition refs、memory tokens、answer/judge request SHA-256、原始 output 和解析值。任何异常记录明确 stage，不能生成伪完成 summary。

- [x] **Step 6: 运行 GREEN**

Run: `cd eval/python && pytest -q tests/test_anchor_runner.py`

Expected: all replay, isolation, arm order, no-writeback and atomic-resume tests pass.

### Task 5: CLI、冻结 manifest、mock smoke 与可恢复运行

**Files:**
- Create: `eval/python/src/memory_core_eval/anchor_cli.py`
- Create: `eval/python/tests/test_anchor_cli.py`
- Modify: `eval/python/pyproject.toml`

**Interfaces:**
- Consumes: Tasks 1–4, existing `CachedTextModel`, `wait_for_effect_ready`, `run_lifecycle_suite`
- Produces: `assert_isolated_database(probe: DatabaseProbe) -> None`
- Produces: `build_run_manifest(*, dataset: AnchorDataset, source_root: Path, behavior_manifest: Path, repository_root: Path, evaluation_ref: str, runtime: Mapping[str, object]) -> dict[str, object]`
- Produces: `execute_dev(*, manifest: Mapping[str, object], dataset: AnchorDataset, output_dir: Path, process_trajectory: Callable, process_behavior: Callable, lifecycle: Callable) -> dict[str, object]`
- Produces: `verified_summary(output_dir: Path, *, manifest: Mapping[str, object], dataset: AnchorDataset) -> dict[str, object]`
- Produces: `main(argv: Sequence[str] | None = None) -> int`
- Produces CLI: `memory-core-eval-anchor`

- [x] **Step 1: 写 manifest 与 Seed 冻结 RED**

```python
def test_run_manifest_freezes_source_prompts_seed_paths_and_six_checkpoints(
    anchor_dataset,
    anchor_source_fixture,
    behavior_manifest,
    repository_root,
):
    from memory_core_eval.anchor_cli import build_run_manifest

    manifest = build_run_manifest(
        dataset=anchor_dataset,
        source_root=anchor_source_fixture,
        behavior_manifest=behavior_manifest,
        repository_root=repository_root,
        evaluation_ref="anchor-v0-dev",
        runtime=frozen_runtime,
    )
    assert manifest["protocol"] == "anchor-v0-causal-dev-v1"
    assert manifest["counts"] == {"trajectory": 15, "behavior": 6}
    assert set(manifest["seed_snapshot"]) == {"internal", "worker", "api", "gen"}
```

- [x] **Step 2: 运行 RED**

Run: `cd eval/python && pytest -q tests/test_anchor_cli.py`

Expected: FAIL because `anchor_cli` does not exist.

- [x] **Step 3: 实现五个子命令**

```text
install-source --output .cache/anchor-source
prepare        --source .cache/anchor-source
smoke          --source .cache/anchor-source --output .cache/anchor-v0-smoke
run            --source .cache/anchor-source --output .cache/anchor-v0-dev --run-ref anchor-v0-dev-20260914
summarize      --source .cache/anchor-source --output .cache/anchor-v0-dev
```

`prepare` 只验证上游与检查点并输出计数/hash；`smoke` 使用固定 fake memory/model 跑完整 15+6 流程，不读取 API key；`run` 先用只读 SQL 断言 `source_events / episodes / consolidation_jobs` 均为空，再执行四类 lifecycle 和全部 probe，按实例原子冻结；`summarize` 不调用模型，重新验证 manifest、文件集合和 Seed snapshot 后生成 summary。

- [x] **Step 4: 写失败恢复和单瓶颈 RED**

```python
@pytest.mark.asyncio
async def test_execute_dev_freezes_judge_contract_failure_as_the_only_bottleneck(tmp_path):
    summary = await execute_dev(
        output_dir=tmp_path,
        process_behavior=judge_returns_explanation_instead_of_token,
        **valid_execution,
    )
    assert summary["decision"] == {
        "status": "bottleneck",
        "bottleneck": "judge_contract",
    }
```

- [x] **Step 5: 实现内容寻址缓存、严格 resume 和异常 stage 归类**

复用 `CachedTextModel` 与 `freeze_manifest`。已完成实例只能在逐字相同的 manifest 下复用；`.part`、未完成三臂、未知结果文件或请求 hash 漂移均拒绝汇总。网络重试只能复用相同请求，不得换模型、prompt、预算或检查点。

失败阶段固定映射：上游/manifest/owner/因果截断、空库、lifecycle、Memory API、Answer 非法 token 或重试耗尽归入 `data_isolation_or_harness`；Behavior Judge 非法 token 归入 `judge_contract`；其余只由纯 summary 依次判 `formed / selected / rendered / behavior_changed / score_improved`。失败记录可原子落盘并导出这个单一 bottleneck，但不得伪造 answer、score 或漏斗成功。

- [x] **Step 6: 运行 GREEN 和离线 smoke**

Run: `cd eval/python && pytest -q tests/test_anchor_cli.py && PYTHONPATH=src:../../sdk/python/src python -m memory_core_eval.anchor_cli install-source --output ../../.cache/anchor-source && PYTHONPATH=src:../../sdk/python/src python -m memory_core_eval.anchor_cli smoke --source ../../.cache/anchor-source --output ../../.cache/anchor-v0-smoke`

Expected: CLI tests pass; smoke exits 0 and reports 15 Trajectory, 6 Behavior, 63 answer records and 18 Judge records with one deterministic decision.（Trajectory 45 answers；Behavior 18 answers + 18 Judges；共 81 次 fake completion。）

### Task 6: 开源入口、文档与完整离线回归

**Files:**
- Create: `eval/anchor-v0.zh-CN.md`
- Modify: `eval/README.zh-CN.md`
- Modify: `README.md`
- Modify: `eval/python/pyproject.toml`
- Modify: `Makefile`
- Modify: `scripts/verify-eval.sh`

**Interfaces:**
- Produces Make target: `make eval-anchor-v0 ANCHOR_ARGS="..."`
- Produces documented source/license/report boundary

- [x] **Step 1: 写入口存在性 RED**

Run: `rg -n "memory-core-eval-anchor|eval-anchor-v0|ANCHOR public development-set score" eval/python/pyproject.toml Makefile eval/README.zh-CN.md eval/anchor-v0.zh-CN.md`

Expected: missing paths/entries before wiring.

- [x] **Step 2: 增加 package extra/script、Make target 与中文说明**

`pyproject.toml` 增加 `anchor` extra（与 `cupid` 相同的 openai/DB/tiktoken 依赖）和唯一的 `memory-core-eval-anchor` script。`Makefile` 增加 `ANCHOR_ARGS ?= prepare`、help、`.PHONY` 和 `eval-anchor-v0`。文档明确 public dev 不是官方分数、Behavior 是 Memory Core 扩展、CC-BY-NC-4.0、无官方 hidden service。

- [x] **Step 3: 跑 ANCHOR 与全部 eval 测试**

Run: `cd eval/python && pytest -q tests/test_anchor_source.py tests/test_anchor_data.py tests/test_anchor_effect.py tests/test_anchor_runner.py tests/test_anchor_cli.py`

Expected: all ANCHOR tests pass.

Run: `cd eval/python && pytest -q`

Expected: complete eval Python suite passes.

Run: `./scripts/verify-eval.sh`

Expected: exit 0 and final message includes ANCHOR.

- [x] **Step 4: 证明 Seed 冻结边界**

Run: `git diff --name-only -- internal worker api gen`

Expected: output is byte-for-byte identical to the baseline captured before ANCHOR implementation; ANCHOR implementation adds no diff under these paths.

### Task 7: Live dev、证据报告与唯一结论

**Files:**
- Create: `eval/reports/2026-09-14-anchor-v0-dev.zh-CN.md`

**Interfaces:**
- Consumes: frozen CLI and external Memory Core/Worker services
- Produces: `.cache/anchor-v0-dev/{manifest.json,lifecycle,trajectory,behavior,summary.json}`
- Produces: one report containing exactly one terminal conclusion

- [x] **Step 1: 安装并预检固定公开数据**

Run: `make eval-anchor-v0 ANCHOR_ARGS="install-source --output .cache/anchor-source"`

Expected: 12 allowlisted files installed; no `user_profile.json` exists.

Run: `make eval-anchor-v0 ANCHOR_ARGS="prepare --source .cache/anchor-source"`

Expected: revision/hash valid, 3 banks, 15 Trajectory items, 6 Behavior checkpoints, two per dimension.

- [x] **Step 2: 运行真实 Memory Core/Worker 三臂 dev**

先建立不进入 Git 的独立运行环境：

```bash
python3 -m venv .cache/anchor-venv
.cache/anchor-venv/bin/python -m pip install \
  -e './sdk/python[test]' \
  -e './worker/python[openai]' \
  -e './eval/python[anchor,local-index]'

docker inspect memory-core-anchor-v0-eval >/dev/null 2>&1 || \
  docker run -d --name memory-core-anchor-v0-eval \
    -p 127.0.0.1:34316:3306 \
    -e MYSQL_ROOT_PASSWORD=memory-core-anchor-v0-only \
    -e MYSQL_DATABASE=memory_core_anchor_v0 \
    mysql:8.0.46
```

数据库必须是这个 ANCHOR dev 的独立空库；若同名容器来自别的运行，停止并报告，而不是清库复用。确认 MySQL ready 后，在三个独立终端启动固定 Worker、MemoryIndex 与 Core：

```bash
OPENAI_BASE_URL='https://api.minimaxi.com/v1' \
MEMORY_WORKER_OPENAI_MODEL='MiniMax-M2.5' \
MEMORY_EVAL_WORKER_OUTPUT_TOKENS='8192' \
OPENAI_API_KEY="$OPENAI_API_KEY" \
.cache/anchor-venv/bin/memory-core-eval-personamem-worker \
  --listen 127.0.0.1:19082 \
  --output .cache/anchor-v0-dev/worker \
  --reasoning-split \
  --max-output-tokens "$MEMORY_EVAL_WORKER_OUTPUT_TOKENS" \
  --replay-semantic-memory-state
```

```bash
MEMORY_EVAL_INDEX_TOKEN='anchor-v0-index-token' \
.cache/anchor-venv/bin/memory-core-eval-local-index \
  --listen 127.0.0.1:19083 \
  --database .cache/anchor-v0-index/chroma \
  --log .cache/anchor-v0-index/calls.jsonl
```

```bash
make build
MEMORYD_DATABASE_DRIVER=mysql \
MEMORYD_DATABASE_URL='root:memory-core-anchor-v0-only@tcp(127.0.0.1:34316)/memory_core_anchor_v0?parseTime=true&charset=utf8mb4' \
MEMORYD_HTTP_ADDR=127.0.0.1:19080 \
MEMORYD_GRPC_ADDR=127.0.0.1:19081 \
MEMORYD_INFERENCE_GRPC_ADDR=127.0.0.1:19082 \
MEMORYD_MEMORY_INDEX_GRPC_ADDR=127.0.0.1:19083 \
MEMORYD_MEMORY_INDEX_RPC_TOKEN='anchor-v0-index-token' \
MEMORYD_AUTH_MODE=trusted_loopback \
MEMORYD_MEMORY_INDEX_PROJECTOR_POLL_INTERVAL=100ms \
MEMORYD_CONSOLIDATION_QUIET_PERIOD=5s \
./bin/memoryd
```

`curl -fsS http://127.0.0.1:19080/health/ready` 返回成功后，在第四个终端冻结环境并运行：

```bash
export MEMORY_EVAL_DATABASE_URL='mysql://root:memory-core-anchor-v0-only@127.0.0.1:34316/memory_core_anchor_v0'
export MEMORY_CORE_ENDPOINT='http://127.0.0.1:19081'
export MEMORY_CORE_TOKEN='eval-loopback'
export MEMORY_EVAL_MODEL='MiniMax-M2.5'
export MEMORY_EVAL_MODEL_REVISION='MiniMax-M2.5@api.minimaxi.com-2026-09-08'
export MEMORY_WORKER_OPENAI_MODEL='MiniMax-M2.5'
export MEMORY_EVAL_WORKER_OUTPUT_TOKENS='8192'
export OPENAI_BASE_URL='https://api.minimaxi.com/v1'
export MEMORY_EVAL_CORE_REVISION="$(git rev-parse HEAD)"
export MEMORY_EVAL_WORKER_REVISION="$(git rev-parse HEAD)"
export MEMORY_EVAL_INDEX_REVISION='chroma-1.5.5:all-MiniLM-L6-v2@913d7300:cosine:chunk220-overlap32:episode-alpha-v1:exact-all-scoped-semantic-tiebreak-v1'
export MEMORY_EVAL_CORE_TIMING_PROFILE='quiet5s-indexpoll100ms-preconsolidation-settle-timeout-v2'
export PATH="$(pwd)/.cache/anchor-venv/bin:$PATH"

make eval-anchor-v0 ANCHOR_ARGS="run \
  --source .cache/anchor-source \
  --output .cache/anchor-v0-dev \
  --run-ref anchor-v0-dev-20260914"
```

`OPENAI_API_KEY` 必须由用户环境提供，任何日志、manifest 或报告都不得写入该值。运行器必须拒绝缺少上述冻结变量、模型/revision 不匹配、数据库不为空、Core/Worker/Index 未就绪或 Seed snapshot 漂移。

- [x] **Step 3: 离线复算并检查完整产物**

Run: `make eval-anchor-v0 ANCHOR_ARGS="summarize --source .cache/anchor-source --output .cache/anchor-v0-dev"`

Expected: 15+6 实例、三臂完整、Judge parse 100%、lifecycle 与 Seed snapshot 一致；summary decision 只能是 `holdout_ready` 或一个 `bottleneck`。

Observed: 15 个 Trajectory 三臂完整；6 个 Behavior 都有原子结果，其中 3 个三臂完整、
3 个由同一 pre-consolidation MemoryIndex 4 秒超时阻断。已完成的 9 个 Judge 全部可解析，
lifecycle 通过且 Seed snapshot 不变；离线复算冻结唯一结论
`bottleneck: data_isolation_or_harness`。

- [x] **Step 4: 写中文证据报告**

报告记录模型/provider/revision、数据 revision/hash、三臂得分、三个 Behavior dimension 差值、
`formed -> selected -> rendered -> behavior_changed -> score_improved` 计数、lifecycle 结果、限制与唯一 conclusion。不得把 public dev 写成官方 ANCHOR score，不得列多个后续优化方向。

- [x] **Step 5: 最终验证**

Run: `cd eval/python && pytest -q && cd ../.. && ./scripts/verify-eval.sh && git diff --check`

Expected: all commands exit 0 with no whitespace errors.

Run: `jq -e '.decision.status == "holdout_ready" or (.decision.status == "bottleneck" and (.decision.bottleneck | type == "string"))' .cache/anchor-v0-dev/summary.json`

Expected: exit 0, proving exactly one allowed terminal conclusion.

Observed: `scripts/verify-eval.sh` 在包含 `local-index` extra 的隔离环境中通过：Python
`339 passed, 2 skipped`，TypeScript `3 passed`，typecheck 与 build 通过；`git diff
--check`、终态 schema 检查和运行后 Seed snapshot 对比均通过。

## Task 8: 使用统一 settle timeout 重跑 dev

- [x] 增加回归测试，令投影清空与完整 settle 共用同一个调用方 timeout；删除独立 4 秒默认值。
- [x] 升级并冻结 timing profile，保持 `internal / worker / api / gen` 不变。
- [x] 使用全新空 MySQL、全新 MemoryIndex 与全新 output 运行相同 15+6 cohort。
- [x] 离线复算 63 个回答、18 个 Judge、lifecycle、Seed snapshot 与唯一终态结论。
- [x] 写第二次运行的中文证据报告并执行完整验证。

Observed: 新运行完整生成 15 个 Trajectory、6 个 Behavior、63 个回答和 18 个 Judge，
lifecycle 4/4 通过，旧、新 manifest 的 cohort 与 Seed snapshot 相同。唯一终态结论从
不完整运行的 `data_isolation_or_harness` 前移为完整效果数据的
`bottleneck: behavior_changed`。证据报告见
`eval/reports/2026-09-14-anchor-v0-dev-settle-timeout-v2.zh-CN.md`。
