# G4 全计划独立审查与一轮修复

计划：[G4候选诊断](../superpowers/plans/2026-10-05-memory-effect-candidates.md)；目标：[五方向goal](../superpowers/specs/2026-09-30-memory-effect-optimization-goal.md)；[完整实测报告](../../eval/reports/2026-10-05-memory-effect-candidates.zh-CN.md)。

一次 fresh-context reviewer `/root/candidate_review`，只读审查 `c7d6a04..5dc61b6`；此前独立审查的 G3/G5 `f0d884e` 是上下文，G4实现重点为 `50e97da/e1b377f/bee69d3/5dc61b6`。未做第二次审查。

审查者报告 Critical0／Important0／Minor1、G4工程范围可合入；独立126项诊断／service测试通过；核对54个模型调用与20个RPC的启动／完成多重集、54个provider stop、source／artifact哈希、c1/c6形成输入／输出／绑定／origin Job／提交Basis与c3失败原文。完整Go、发布包、数据库与远端模型运行均为执行者证据。完整goal仍执行中。

唯一Finding：`candidate_diagnostic.py` 没有启动清单，把实测每例model_calls裁到一条仍接受六条调用与两份目标；已提交的实际54条证据完整，因此原结果有效。执行者依Task2的完整性要求升级为Important；一轮修复要求eval attempt_ref、启动清单、对应RPC／input hash，拒绝缺失／重复／错配。5项行为RED→GREEN，诊断24通过；真实记录以原始启动日志行身份补充只读元数据，重评分目标数量不变；原v1分析与原始calls不变，截断复现现在拒绝。

审查者另列六项 Declined to judge：共同自然行为效果；拥挤候选冲突；跨session物理来源复用；其他主pass坏格式；独立远端／完整DB重跑；总体语义精度和远端权重重现。以下按ledger顺序保留每项决定和代价。

## 完整 Ruling 与代价

1. **Task1保留原候选机制**：给定顺序的真实DB合同首次通过，无具体生产缺陷。代价：尚可能命中话题邻居／遗漏反例，真实语义与共同效果仍需测。
2. **Task2 scorer标签隔离与精确目标**：期待不进入replay/input hash；显式评审绑定确切Target/body/Basis。代价：验证器通过不证明自然语言正确或事务权威，仍须实际Core/模型证据。
3. **Task2仅丢弃缺opcode主结果**：实际正确focused正文被主结果缺TEXT拖成整批no-op，窄检查丢弃整个无效主lane，不猜操作／新增完整parser／扩大K。代价：无效主结果中的其他含义本次丢失；其他坏格式仍可能整批拒绝。
4. **Task2继续有界双视图／K**：两正例所需历史经历到达，失败不按正负筛选。代价：有限合成样本不排除自然候选损失／例外错组，共同gate开放。
5. **Task2人工确切正文与模型别名**：区分早期Target形成与最后窗口，记录未取得server build。代价：评审与远端模型漂移可能改结果，无独立盲评／总体精度／权重版本承诺。
6. **Review completeness Minor→Important并修复**：以启动清单／attempt_ref／RPC与input hash校验完整多重集。代价：旧记录须追加由实际启动日志导出的元数据，调用方须保存starts。
7. **Declined共同效果保持待验收**：四臂、Oracle、实际Delivery/AgentAct、纠正延迟和自然收益是完整goal后续工作。代价：G4形成不能替代行为价值，也不关闭G3/G5所有语义。
8. **Declined拥挤冲突补独立冻结c7**：三历史竞争两槽，失败s1保留、无Seed，继续原K。代价：一例不能证明所有饱和历史；其他改写／负载下仍可漏反例。原六例未改／未调参。
9. **Declined物理来源按权威层验**：Core保留full scope/ref身份，exact同源两run保持原session、不新增独立candidate；ChoraiSOURCE对物理消息拒绝scope/session/body/actor/Constitution替换。代价：通用Core调用者需提供可信物理身份，跨scope同字面ref是不同声明，无法从文本推断同源。初测错误期待Core跨namespace冲突是测试假设错误，未算生产RED；正确Canonical control首次通过，Chorai既有SOURCE guard重跑通过，无生产身份合同变化。
10. **Declined其他坏格式保持窄修复**：Core继续最终整批拒绝剩余错误，不引入广泛salvage。代价：其他无效主结果仍可浪费独立调用／使整批no-op，须实际失败才扩大范围。
11. **Declined独立远端／DB重跑区分证据**：reviewer126只读测试＋artifact核对；执行者实测／完整gates／一轮fix，不二次派reviewer或重复远端调用。代价：操作重现依赖环境，没有第二独立远端／DB运行。
12. **Declined总体精度／权重重现保留限制**：六例和补充一例只做合成诊断，确切正文由执行者评审，模型为配置alias。代价：不估计总体语义精度／不锁远端权重，共同Oracle行为可以不同。

## 单轮修复验证与状态

5项scorer RED→GREEN，完整Python596通过／13跳过（429eval＋167Worker；2可选Chroma＋11可选primary-live），完整uncached Core Go与vet通过。新增canonical-source合同在PostgreSQL25.470s Store＋运行中RPC0.336s、MySQL43.684s Store＋RPC0.449s的完整迁移/runtime脚本通过。Chorai只重跑已有SOURCE替换／Outcome→Situation guard，0.561s，未改代码。

Worker生产代码自 `bee69d3` 未再变；此前最终clean wheel／生成漂移／发布167tests／Go→Python0.331s证据保留，不声称重新跑。补充拥挤c7由执行者实测11/11 stop调用、4/4RPC、0失败重试、0Seed，reviewer未重跑。无deferred Minor，无剩余G4工程阻断；完整goal的共同效果和G3/G5语义仍待完成。

本计划scratch在Task3完成后移入既有Git忽略目录 `.cache/memory-effect-optimizations/g4-verification/`，再删除本计划SDD工作目录。原始模型／索引／Core记录及source snapshots保存在 `.cache/memory-effect-optimizations/g4-live/`。没有改原工作区、push或merge。
