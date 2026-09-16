# Seed-essential Delayed Eval v1

这是一组用于定位问题的合成开发实验，不是公开 benchmark，也不宣称统计显著。它只回答一个
更窄的问题：当当前请求没有再次说出用户偏好时，跨 session 形成的 `Disposition` 是否比
Recollection RAG 更能延续用户特有的互动方式。

## 最小实验

固定 4 种长期互动模式，每种 2 个延迟探针，共 8 个相互隔离的实例。每个实例先按时间顺序
提交 3 个完整因果 session：

```text
Situation -> AgentAct -> 非 Agent Outcome -> 等待巩固
```

三次巩固后，提交一条不包含目标行为的只读 Situation，并且只调用一次 Select。随后从同一份
`MemoryContext` 派生四臂：

| Arm | 相同 Recollection | Disposition |
|---|---:|---|
| `rag` | 是 | 无 |
| `learned_seed` | 是 | Core 实际形成并选中的 Seed |
| `oracle_seed` | 是 | 冻结的理想 Seed，不使用 Learned Seed |
| `anti_seed` | 是 | 冻结的反向 Seed，不使用 Learned Seed |

四臂共用 2,048-token memory cap、Persona、当前请求、MiniMax-M2.5、temperature 0 和回答预算。
Episode evidence 固定为 0。探针不写 Delivery、AgentAct 或 Outcome，因此答案不会反向污染学习。

四个答案全部完成后才加载 scorer target。每个固定比较交换 A/B 位置各判一次；只有两个位置
指向同一逻辑赢家才算 clear win。

## 结论规则

- Oracle 支配 Anti 是操纵有效性的前提；否则实验本身无效。
- Oracle 支配 RAG，但 Learned 不支配 RAG：架构注入有潜力，瓶颈在形成、选择、渲染或
  Learned Seed 文本效果。
- Oracle 也不能支配 RAG：当前架构或 Harness 注入没有检测到额外价值。
- Learned 支配 RAG，且至少 6/8 完成 `formed -> selected -> rendered`：得到初步架构优势证据。
- 其余结果为 `inconclusive`。

“支配”固定为 8 例中至少 6 个 clear win，且对手 0 个 clear win。这个门是预先冻结的开发门，
不能在看完答案后修改并仍称为 v1。

## 运行

数据和无密钥协议检查：

```bash
make eval-seed-essential-delayed DELAYED_SEED_ARGS=prepare
make eval-seed-essential-delayed \
  DELAYED_SEED_ARGS='smoke --output .cache/seed-essential-delayed-smoke'
```

Live 运行要求一个全新的空数据库及已经启动的 Memory Core、Worker 和 MemoryIndex。除通常的
Core endpoint/token 外，需要冻结以下公开元数据；API key 不会写入 manifest：

```bash
export MEMORY_EVAL_DATABASE_URL='mysql://.../memory_core_seed_delayed_v1'
export MEMORY_CORE_ENDPOINT='http://127.0.0.1:19091'
export MEMORY_CORE_TOKEN='...'
export MEMORY_EVAL_MODEL='MiniMax-M2.5'
export MEMORY_EVAL_MODEL_REVISION='MiniMax-M2.5@api.minimaxi.com-2026-09-08'
export MEMORY_WORKER_OPENAI_MODEL='MiniMax-M2.5'
export MEMORY_EVAL_WORKER_OUTPUT_TOKENS='8192'
export OPENAI_BASE_URL='https://api.minimaxi.com/v1'
export OPENAI_API_KEY='...'
export MEMORY_EVAL_CORE_REVISION='...'
export MEMORY_EVAL_WORKER_REVISION='...'
export MEMORY_EVAL_INDEX_REVISION='chroma-1.5.5:all-MiniLM-L6-v2@913d7300:cosine:chunk220-overlap32:episode-alpha-v1:exact-all-scoped-semantic-tiebreak-v1'
export MEMORY_EVAL_CORE_TIMING_PROFILE='quiet5s-indexpoll100ms-preconsolidation-settle-timeout-v2'

make eval-seed-essential-delayed \
  DELAYED_SEED_ARGS='run --run-ref seed-delayed-v1 --output .cache/seed-essential-delayed-v1'
make eval-seed-essential-delayed \
  DELAYED_SEED_ARGS='summarize --output .cache/seed-essential-delayed-v1'
```

产物包括冻结 manifest、8 个逐实例结果、内容寻址模型缓存、用量日志和 `summary.json`。已有结果
只有在逐字一致时才可续跑；发现 `.part`、输入、prompt、受保护 Core/Worker/API/生成代码或结果
集合漂移时失败关闭。
