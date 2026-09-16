# OmniMemEval 接入

这条入口只用于与其他 Memory backend 做同 harness 横评。它不替代本仓库的六组机制诊断，也不能把 Oracle evidence 当作 Memory Core 成绩。

当前兼容并锁定 OmniMemEval commit：

```text
0b1ea8d28aa2d3e03ac4a6aee17b3006a131da7d
```

## 安装 Adapter

先安装本仓库 Python SDK 与 eval 包，再准备锁定的上游 checkout：

```bash
python -m pip install ./sdk/python './eval/python[personamem,local-index]'
git clone https://github.com/MemTensor/OmniMemEval.git /path/to/OmniMemEval
git -C /path/to/OmniMemEval checkout 0b1ea8d28aa2d3e03ac4a6aee17b3006a131da7d
memory-core-eval-install-omnimemeval /path/to/OmniMemEval
```

安装器只增加 `memory_core` Client bridge 与 generic text search 注册；第二次运行是 no-op。上游 commit 或代码结构不符合锁定版本时直接拒绝，不猜测修改位置。

复制 [`memory-core.env.example`](./memory-core.env.example) 到上游仓库根目录的私有 env 文件并补齐凭证。MemoryIndex 可使用任意遵守公共合同的 Provider；仓库自带的可复现评测实现可这样启动：

```bash
export MEMORY_EVAL_INDEX_TOKEN='eval-index-token'
memory-core-eval-local-index \
  --listen 127.0.0.1:18083 \
  --database .cache/omnimemeval-index/chroma \
  --log .cache/omnimemeval-index/calls.jsonl
```

让 `memoryd` 使用同一地址和 token，启动独立 MySQL/PostgreSQL、Worker 与 `memoryd` 后运行：

```bash
cd /path/to/OmniMemEval
./scripts/run_pmv2_eval.sh \
  --lib memory_core \
  --env .env.memory-core \
  --version memory-core-full-01 \
  --workers 1 \
  --top-k 20 \
  --wait-after-ingest 180
```

使用非 streaming 模式。Omni 的 version 已进入 `user_id`，可隔离重复实验；Memory Core V1 没有删除整个 owner 的公共 RPC，因此不要用 `--streaming 1` 或把数据库直删包装成 Adapter 能力。后台在固定等待后仍未完成时，该次运行不得作为完整成绩，须用 Core 的本地评测探针核验 pending jobs 为 0 后再从 Search step 恢复。

MySQL 在并发写入时可以按公开合同返回 gRPC `ABORTED`，表示本次事务没有提交、应以同一幂等请求重试。Adapter 默认最多尝试 5 次并做短退避；`MEMORY_CORE_OMNI_RPC_ATTEMPTS` 必须写入实验环境记录。其他错误不会被这层吞掉。

## 可比较性条件

- 完整 benchmark：200 personas、共 5,000 题；每个 persona 的题数并不固定，必须让 Omni 跑完整行集，不能按“每人 25 题”截样本。
- Answer model 固定 `gpt-4.1-mini-2025-04-14`，temperature 和 prompt 由 OmniMemEval 管理。
- Adapter 不读取 `preference`、`related_conversation_snippet`、正确答案或 distractor 标签。
- Omni 先对包含 system 的原始历史按 20 条切批，Adapter 会跨 `add()` 拼合被批次边界切开的 user/assistant 对；末尾没有真实 assistant 回复的 user 消息不伪造成 Episode。
- query 作为未绑定 SourceEvent，只参与 Select；答案阶段不回写 Core。
- Adapter 将实际返回给 Omni 的全部稳定 refs 记录为 exact Delivery；没有 Outcome，因此这不会被解释成回答成功或反馈学习。
- 报告同时保存 Omni commit、Memory Core commit、Worker/index 模型与服务配置。不同模型或不同上游 commit 的结果单独报告，不放入同一排名表。
