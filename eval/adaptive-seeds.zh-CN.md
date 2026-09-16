# 适应性 Seed：有界机制诊断

本诊断检查同一个后台 `ConsolidateWindow` 是否从经历学习未来倾向，不衡量完整人格成长。
Core 只校验来源、owner、版本及资格；长期要求的语义只由同一个 Worker 判断。没有额外 judge、
分类 Job、人工运行时审批或在线生成式 Select。

## 当前结论：fix round 2 的有限诊断通过

在不改prompt/model的显式32768输出/300秒请求/330秒RPC/retries0包络下，原15例、4条角色
确认和6次双库live调用全部完整返回并通过生产语法/引用边界。相同经历下，伙伴侧重倾听后的
情绪回应，教练侧重用户自主选择下一步，该功能差异在确认组再次出现；其中一条教练文本未
显式复述提问形式，不声称每条Seed复制全部基准。PostgreSQL/MySQL均验证known→changed→
cleared基准进入实际模型输入，同Seed纠正/Select及无链反馈不改Seed/Basis。共25次调用，
报告116883 tokens。这只支持该有界诊断，不证明长期人格效果、普适模型可靠性或stock
`memoryd`两分钟调度预算。非thinking仍未接受；原公开历史仍是旧prompt下3/4窗口完成、0Seed。
下文保留并标明此前轮次的失败与未完成结论，不将它们改写成成功。

## 受控真实模型用例

从仓库根运行，密钥和 endpoint 只从环境读取，不写入脚本或报告：

```bash
PYTHONPATH=worker/python/src:eval/python/src:sdk/python/src \
python -m memory_core_eval.adaptive_semantics \
  --thinking --fixtures worker/python/tests/adaptive_cases.py \
  --max-output-tokens 32768 --request-timeout 300 --rpc-timeout 330 \
  --output .cache/adaptive-seeds/my-new-run
go run ./eval/adaptivecheck .cache/adaptive-seeds/my-new-run/windows.jsonl
```

使用 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`MEMORY_WORKER_OPENAI_MODEL`，复用既有
`ChatTextModel`。输出目录必须不存在；保留 prompt/fixture hashes、实际输入/输出、finish reason
和用量。每个请求真正跨越 inference gRPC 和 Python Worker，仍只有一次模型调用。
`adaptivecheck` 使用生产 Go parser 和允许引用校验，不是语义评分器，也不证明 DB 提交资格。

固定用例覆盖首次明确要求、同 Seed 直接纠正、当前加独立相关旧经历、两种角色基准、真实 USER
中性提问加 Agent 自我认可、纯事实、帖子引用、无链效果报告、既有同义 Recollection 去重、
基准正文伪标签。必须逐条读生成正文：操作/引用正确不能证明正文意义正确。

引用或反应的来源忠实 Recollection 不等于用户自己的长期要求，也不等于 Seed 生效。
无链反馈验收重点是 Seed 版本、Basis 和状态不变，而非禁止所有 Recollection。
“用户说这次回答很好”不能改写为“某 Seed 已被证实有效”。

适应性语义验收限定为显式 `deepseek-v4-flash` thinking/high 配置；非 thinking 已发现间接
经历误走 ADAPT，属于未获语义验收的实验配置，不宣称普适 TextModel 保证。CLI 的兼容默认
仍 disabled，运行该验收必须显式加 `--thinking`，不修改生产配置、不换 provider/model，
也不加 judge。该模式不传无效的 temperature，固定用例 runner 禁用自动请求重试。
`--max-output-tokens`必须为正整数，两个timeout必须为有限正数；manifest记录实际资源参数。
兼容默认仍为8192/110秒/180秒，较大的上述预算仅用于显式诊断，不改变生产Worker或Core默认。
必须分别报告各组，不能选择最高结果或重跑直到碰巧全过。

## 真 Core、数据库与模型链

启动同一参考 Worker 的评测 adapter：

```bash
PYTHONPATH=worker/python/src:eval/python/src:sdk/python/src \
python -m memory_core_eval.personamem_live --listen 127.0.0.1:18882 \
  --thinking --max-output-tokens 32768 --request-timeout 300 --max-retries 0 \
  --output .cache/adaptive-seeds/my-live-worker
```

使用新建、一次性的 PostgreSQL 或 MySQL 8 容器，传入对应
`MEMORY_TEST_DATABASE_URL` / `MEMORY_TEST_MYSQL_DATABASE_URL`，以及
`MEMORY_TEST_ADAPTIVE_WORKER_ADDR=127.0.0.1:18882`，运行相应包的
`TestCoreStoreContract/adaptive_live_model_lifecycle`。合同工厂经 migration 创建并清理独立
schema/database。测试走 Core→推理 gRPC→Python Worker→真实模型，验证形成→Select→
同一 Seed 纠正→再 Select；SQL 比较无链表扬前后的 Seed/Basis，且 Delivery/Outcome 为零。
幂等重试不增加模型调用。普通无 key 发布门默认不执行这组可选外部调用。
三个窗口还分别携带已知、已更改及清空的 Constitution；核对原 Source 的不可变冲突与重放，
并从实际 model input 保存值核实完整路径，而不把仅到 Go Worker 接口的证据称为 live 模型证据。

该可选live合同使用330秒诊断上下文。原版 `memoryd` 的scheduler WorkerTimeout为2分钟、
LeaseDuration为5分钟，当前没有这两个时长的运行参数。单独330秒诊断或有界history driver
取得结果，只证明该显式资源包络下的机制，不证明stock服务的延迟可用性；已保存长历史响应
约138–318秒，更不能当作原版两分钟调度预算的成功证据。本轮不新增运行时timeout配置。

## 冻结公共历史抽取

`adaptive_history` 使用既有 `load_history` 和 `PersonaMemHarness.ingest`，只导入已观察的
user/assistant 配对。固定 PersonaMem-v2 的 persona 977、486，各前 56 个完整 turn、每批 28；
源 SHA-256 写在代码中并在模型运行前校验。所有 baseline 为 UNKNOWN，不使用 background、
隐藏人格、问题、选项、答案或 evidence hints。只允许新输出目录与未使用过的 namespace，
必须显式提供 `--disposable-database`；禁止指向产品或已冻结评测数据库。

```bash
PYTHONPATH=worker/python/src:eval/python/src:sdk/python/src \
python -m memory_core_eval.adaptive_history \
  --data <PersonaMem-v2-cache> --output .cache/adaptive-seeds/my-history \
  --endpoint <disposable-Core-endpoint> --database-url <disposable-database-url> \
  --evaluation-ref <fresh-name> --disposable-database
```

需要 SDK、eval 的 live 依赖、数据库驱动与运行中的 Core scheduler/Worker。
保存每批 canonical memory、来源/Basis/版本及 Job 数，不追求非零 Seed 数，不重跑静态 QA 争取高分。
这是公开 benchmark 会话的有界抽取诊断，不是人类用户纵向试验或人格成长效果证明。

## 历史证据与失败边界（截至 fix round 1）

本节按实际轮次保留旧结果；其中“仍未完成”描述当时的证据状态，不替代顶部fix2有限结论。

2026-09-07 的实际评测中，非 thinking 多轮 prompt 修订仍出现错误路由、同义重复及角色侧重
不稳定，不能声称默认低推理配置已可靠通过。显式同模型 thinking 组与一次同 prompt 确认
均为 11/11 操作形状及生产 parser/引用通过；原文的角色功能差异仅首轮出现，确认轮未稳定
复现，因此不能声称已证明稳定角色因果差异。其他受控适应含义与双库直接纠正闭环有实际证据。
这些有限结果不是总体准确率、模型保证或生物/哲学理论的验证。

随后预先固定、同 prompt/model 的四次对比探针使用更有区分度的基准：伙伴倾听后反映/命名
情绪，不主动推进目标/行动；教练倾听后问一个澄清问题，帮助用户自主选一个下一步。
仍使用完全相同的当前与相关旧经历，每种基准两次，不改原有成绩。实际结果为 4/4 操作形状
通过但 1/4 重复 BASIS 标签、生产 parser 拒绝，功能差异也未稳定出现。因此角色基准的
可重复因果效果仍是未完成验收项，不能将标签通过改称语义通过。

长历史的 thinking 运行曾因评测 adapter 固定 8192 输出上限被推理 token 耗尽而返回 length。
这不是 Core 或参考 Responses Worker 的默认能力声明。允许为同一冻结 Job 显式使用
`--max-output-tokens 32768 --request-timeout 300 --max-retries 0`，配合
`go run ./eval/adaptivehistory --database-url <同一次性PG库> --disposable-database`：该独立
评测驱动沿用真实 scheduler.ProcessOne，最多四个窗口，330 秒 Worker timeout、360 秒 lease，
遇到下一次错误立即停止，不后台重试。默认 Core 与 adapter 配置均不改变。
失败/取消前先保存 attempt ID 与公开输入，终态保存错误类型；有返回时保存完整 final content、
finish reason 和用量。不保存 reasoning content、凭证或请求头；无响应的用量标记为不可得，不能算零。

原公开诊断的实际终态（prompt SHA-256
`a7264962ed9e54e7f496c503ff50516398433453aced2f8697ce5982f4a14c0a`）：
112turn/224Sources全部录入且匹配冻结源；4 Jobs只有3个完成，第四次DeadlineExceeded，
recorder为CancelledError且无返回usage，不是成功NO_CHANGE。最终2 Recollections、2版本、
4 Basis、0 Seed、0 Delivery/Outcome。原3次length响应及两次取消保留，后续修复不重跑该cohort。

检查器区分缺失/空值 output 与显式成功空文本，拒绝 `error`、`error_type` 和非成功status。
旧检查器曾将取消行误判为空changes；历史错误报告保留，修复后的退出码不能改写旧实验结果。

修复轮1只调整通用角色条件化说明及浅层边界，原11用例与4条角色探针逐字不变；新prompt SHA
`bf7412b72c1253825e72233e072c25207dcdae4d2d3f22940e1620c99e4921c1`。
唯一一轮15请求中只有前三例返回：直接形成、同Seed纠正及当前+旧经历TEXT，正文与依据相符。
第三例raw text重复BASIS，经同一Worker内的机械归一后生产parser/refs通过，未丢失e1/e0。
其后1次gRPC DeadlineExceeded、11次APIConnectionError，无返回usage；不能算语义负例或成功。
不运行确认轮或有条件授权的双库live组，不重试、不换模型。角色功能差异及新基准全链证据
仍未完成；新增known→changed→cleared测试代码不等于已经取得对应live结果。

随后控制器在只读确认同配置当前可达后，另行允许一次仅针对12个无文本观察的传输恢复。
每次先比对原失败input逐字相等，原fixture/prompt/model配置不改，已返回前三例不重跑。
实际恢复到第10个请求即停止：9例完整返回并通过parser，教练探针finish_reason=length，
8192输出token全部用于reasoning，final为空；最后两个探针未调用。该失败有实际usage，
不是此前的连接错误，也不是成功no-op，不再恢复。混合attempt可覆盖原11例及1条伙伴探针，
后者体现倾听后反映情绪、不推进动作，但缺少完整可重复角色对照，故不运行确认或双库live。
原失败和此次10次响应分别保留；没有修改原公开历史或将传输恢复称为一次顺畅通过。
