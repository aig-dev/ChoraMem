# 理念与概念：唯识启发，不是唯识实现

## 项目主张

Memory Core 借用唯识学中“潜在因果倾向—遇缘现起—现行反过来改变后续倾向”的结构，设计长期人格 Memory 的读写闭环。

这里的“启发”是严格限缩的：

- 佛学材料用于提出结构假设和约束，不替代工程证据。
- 只有在软件中产生真实因果作用的对应，才进入运行模型。
- 本项目不声称实现八识、阿赖耶识、末那识、人类主体性、业、轮回、解脱或意识。
- 工程对象无法满足原典概念的全部条件，因此不能以同名宣称二者等同。

## 核心对应

| 唯识概念 | V1 工程对象 | 对应等级 | 实际因果作用 | 禁止宣称 |
|---|---|---|---|---|
| 种子（bīja） | `DispositionSeed / SeedVersion` | 部分类比 | 保存可在相似境遇中再次影响反应的潜在生成倾向 | 事实、聊天原文、数据库行或 AI 灵魂就是种子 |
| 种子现行 | `Disposition` 的当前选择 | 部分类比 | 当前 situation 可直接命中 Seed，或命中其 exact canonical support Episode；support 胜过 inhibition 后进入 `MemoryContext`，同窗口兄弟不会连带现行 | 检索命中等于意识或业果成熟 |
| 熏习（vāsanā） | 读写闭环 | 结构启发 | 新经验可直接重固 Recollection；跨 session 重复经验可形成新 Seed；本人明确长期要求可经 ADAPT 纠正既有 Seed；非 Agent Outcome 可作为新倾向初生的效果依据；实际重演追加现行依据，效果驱动的既有 Seed TEXT 修订或 INHIBIT 仍须同链因果资格 | 某个 batch Job 或一次向量相似就是熏习 |
| 阿赖耶识相续 | Seed 的追加式版本与因果连续性 | 部分类比 | 旧版本被保留，新版本从有来源的变化中继续 | 某个数据库、`memoryd` 或某张表就是阿赖耶识 |
| 染污末那识 | 无 V1 运行时对应 | 仅解释边界 | 无 | 把 scope、relationship、SELF 路由或 Soul 改名为末那识 |
| 转识／现行识 | Harness 执行侧 | 仅作隐喻 | Harness 消费 Context，运行模型、工具与行动 | Harness 等同第六意识，或系统实现完整八识 |

《成唯识论》卷二以“谓本识中，亲生自果，功能差别”说明种子的因果功能，并列出待众缘、引自果等条件。V1 因而把 Seed 收窄为“潜在生成倾向”，而不是事实列表。多个来源不重叠的 Episode 可以支持新 Seed 初生；一个尚不存在的 Seed 不需要先被 Activation。

《摄大乘论》关于种子与熏习互为因的论述，支持“既有倾向影响现行，现行又改变后续因果基础”的循环形状，但不支持把软件行为等同于业果。唯识关于阿赖耶识与末那识的讨论也表明，变化相续与自我执取不是同一功能；普通 owner scope 不足以构成末那识。

参考：

- [《成唯识论》卷二](https://deerpark.app/cbeta/T1585/2)
- [BDK《摄大乘论》英译本](https://bdkamerica.org/download/1880)
- [BDK《成唯识论》《唯识三十颂》《唯识二十论》合集](https://bdkamerica.org/download/1861)
- [Stanford Encyclopedia of Philosophy: Yogācāra](https://plato.stanford.edu/entries/yogacara/)

## 工程对象的清晰定义

### SourceEvent

有可信来源身份的不可变文本输入。它回答“这段材料从哪里来、谁产生”，不回答它是否正确。

### Episode

由 situation 与 Agent 实际行为组成的一次具体经历，可附带 Outcome。Episode 保存“发生了什么”，不是 Seed。

SourceEvent 只保存在因果真源；VDB 投影的是由 typed links 确定性生成的 Episode 文本。语义检索可以让当前境遇再次遇到同 owner 的旧 Episode。调用方显式给出有界预算时，完整历史 Episode 也可以作为冻结的引用证据进入 `MemoryContext`，无需先转成 Recollection；它仍不是第三种 learned Memory，不是当前指令，也不获得写入权威。这只是“提供可能相关的缘”的工程手段，不等于唯识学的缘起、种子成熟或因果证明。Core 仍要求当前经验参与每次长期写入。

### Recollection

Core 对有来源经历“如何被记得和理解”的可修订长期文本。它可以被新 Episode 直接形成、保留或修订；无需该 Recollection 此前被选择或投递，也无需 Episode 之后再有 Outcome。Episode 本身仍是已经物化的具体经历。Recollection 不是客观事实、Seed 或 SelfStory。

### DispositionSeed

一种潜在生成倾向：它可能在相似境遇中改变未来 Agent 如何理解、关注与回应，不是过去回复模式的复制。普通聊天中的单次要求或成功经验只是一份 Episode，不能单步初生 Seed；不同 session 的多源经验可以支持推断。显式产品设置由 Harness 的 Soul／Constitution 真源承载，不需要伪装成 learned Seed。Seed 不是 fact、Claim、Episode、聊天摘要或用户档案；已有同义记忆不强制双写。

`SeedVersion` 的权威语义只有一段 `tendency_text`。V1 不把 `WHEN / THEN` 逻辑门、confidence、strength 或学习率写成权威状态。

### Constitution（产品中可称 Soul）

外生、版本化的 Agent 角色规范基准。Harness 把与 Select 相同的可选快照随 Situation 录入；Core 冻结来源身份，同一后台 Worker 将当前有效快照作为只读解释背景。它不是用户隐藏人格档案，也不是 Seed、Target 或 Basis，不参与普通 Memory Job 的自动改写，不对应末那识、阿赖耶识或灵魂实体。

### Relationship

V1 中首先是长期 owner scope：它保证同一 Agent 在不同关系里形成的 Seed 不互相污染。它不是完整的关系心理模型；若未来新增关系状态，必须先说明它会改变什么写入资格或状态迁移。

### MemoryContext

Core 对当前 run 选择出的中立上下文。它可以包含 learned Recollection／Disposition，也可以包含显式启用、固定预算内的历史 Episode 原文快照。后者只是有来源的引用证据；投递它不会激活 Seed 或补足反馈资格。`MemoryContext` 不是 SelfStory，也不拥有最终叙事。Harness 决定如何把它用于 prompt、规划和行动。

## 两类演化

```text
RECOLLECTION FORM
  一个或多个有来源 Episode
  -> 新 Recollection

RECOLLECTION KEEP
  新 Episode 支持当前理解
  -> 只追加 Basis，不创建相同文本的新版本

RECOLLECTION REVISE
  新 Episode 改变当前理解
  -> supersede 旧版本，创建新版本

DISPOSITION FORM
  多个来源不重叠、含非 Agent 情境依据的完整 Episode
  + 推断依赖回应效果时，引用与 Episode 配对的非 Agent Outcome
  -> 新的条件性生成倾向

DISPOSITION ADAPT
  一份当前完整 Episode 中本人明确的长期交互纠正
  -> 只修订同一个既有 Seed，不用于初生
  -> 仅引用这一份直接授权；多 Episode 归纳只能走 TEXT
  -> 不伪造 Delivery、Outcome 或行为成功；同正文为 no-op

DISPOSITION REENACT
  existing Seed 被选择并真正投递
  -> Agent 实际行为再次表现该倾向
  -> 追加正向 Episode Basis

DISPOSITION INHIBIT
  existing Seed 被选择并真正投递
  -> Agent 实际行为后出现同链、非 Agent 的局部反例 Outcome
  -> 追加情境抑制 Basis

DISPOSITION REVISE
  existing Seed 被选择并真正投递
  -> Agent 实际行为后，Episode + 同链、非 Agent 的 Outcome 改变其含义
  -> supersede 旧版本，创建新版本
```

这些操作共享一次 `ConsolidateWindow`，不是多套 Pipeline。AI 只提出浅层 `TARGET / APPLICATION / CHANGE / BASIS` 文本；Core 校验 Target、Basis、scope、版本与因果资格并立即提交或 no-op。新 Seed 的 Outcome 依据不能替代两个不同 session 的完整 Episode，也不能给既有 Seed 补 ADAPT／修订资格；它只让“这种回应曾得到什么结果”成为初生 Seed 的显式因果来源。参考 Worker 确定性拒绝后台聊天中的 `NEW_DISPOSITION ADAPT`；普通表扬、一次成功、Agent 重复输出或无人反对都不是新 Seed 权威。已有 Seed 的直接长期纠正仍由 ADAPT 表达。

## 学习不等于先重复表现

《成唯识论》卷八的名言习气包括“能了境心心所”，不能把唯识启发缩成只有外显语言行为才可留下倾向。这里仅借用结构，不声称完整佛学种子都等于人格倾向。[卷八原文](https://deerpark.app/cbeta/T1585/8)

Pereg 与 Meiran（2019）的实验研究指令驱动、未先练习的任务行为，支持“学习不必以既有表现为前提”的有限动机，不证明自动长期人格巩固。[原始研究](https://pmc.ncbi.nlm.nih.gov/articles/PMC6553735/)
Wimmer 与 Shohamy（2012）发现关联记忆可传播奖励价值并影响后续新选择；这启发经验影响未来选择的设计，不证明本系统与生物记忆机制等价或人格效果优越。[原始研究摘要](https://pubmed.ncbi.nlm.nih.gov/23066083/)

## 关于“事实正确性”和生成自由

Memory Core 不把人格倾向伪装成事实真相，也不以 confidence 数字制造精确性的幻觉。模型可以从有界经历中形成具有解释性和审美张力的倾向文本；Core 只要求它不能伪造来源、越过 owner scope 或把无因文本写成权威因果历史。

最终表达是否允许想象、隐喻或角色化叙事属于 Harness 与产品策略。Memory Core 管理的是长期生成倾向如何连续变化，不是把 Agent 限制成事实数据库。

## 删除测试

若删掉一个“唯识术语”后，存储归属、写入资格、权威边界和状态迁移均不改变，它就不应成为运行时模块。V1 因此没有 `AlayaStore`、`ManasEngine`、`SelfStory` 或 Soul 自动学习器。
