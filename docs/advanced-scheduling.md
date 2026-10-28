# 扩展排期：可解释的约束与数据

## 数据规模

默认资料现有20条素材、64条已有排期，覆盖2026-11-01至11-07，video/social/community三个频道。M01..M05保留基础验收素材，M06..M20增加预告、社区活动、海报、教程、合作测评、版权未授权、缺资料等组合。所有内容合成；不表示20条都被单独调用模型测试过，实际测试覆盖以输入记录为准。

默认排期并非每个时段都占满，而是包含不同日期、频道、连续占用与直播节点，模型必须结合每条请求的限制查可用时间。每次执行用独立数据库，避免场景之间污染。场景对排期的增删明确记录在套件定义中，实际数据快照随结果保存。

## 新输入字段

| 字段 | 含义 | 边界 |
|---|---|---|
| requested_time | 期望时间 | 保留原始要求的规范化时刻 |
| hard_time | 是否必须由用户确认时间变化 | true时替代时间只能待确认，除非明确确认 |
| adjustment | either / earlier_only / later_only | earlier_only允许原时刻或更早；later_only相反 |
| not_before | 最早允许时间 | 包含边界 |
| not_after | 最晚允许时间 | 包含边界 |
| deadline | 必须在此之前 | **不包含边界** |

这些约束取交集；用户确认替代时间不能覆盖窗口、方向或截止时间。矛盾窗口会在输入阶段拒绝。格式仍是整点、带时区的JSON，并未实现任意自然语言约束的自动抽取；运营人员需把要求录入这些字段。不能声称本次新增了视频理解或自然语言解析器。

示例：期望周五18:00，只能提前，16:00起可发，必须在20:00直播前。输入为 hard_time=false、adjustment=earlier_only、not_before=16:00、deadline=20:00。如果18:00占用，17:00是合理候选；19:00即使空闲也违反只能提前，20:00又违反严格截止时间。工具会拒绝这两种保存，而不是只靠提示词约束模型。

## 模型与工具分工

query_slots按start_time向后返回最多6个连续整点；如果要查更早时段，模型必须主动把查询起点前移。每个时段同时返回occupied（实际占位）和constraint_violations（违反哪个约束）。available仅在两者都允许时为true。

模型观察这些结果后选择查询和保存动作；工具没有自动替模型保存其他时段。save_draft和最终历史草稿重验共用约束检查，不允许绕过。若查过的窗口都不可用，应返回未完成及已查窗口的限制，不能宣称未来所有时间都不可用。

同批固定时段优先：模型提示要求先处理固定素材；同时工具保护其他尚未保存、审核可行的固定目标时段，灵活素材不能抢占。同批已有草稿仍按数据库占位检查。这个策略是保守启发式，不是全局最优排程器，也不提前预约其他请求的空位。

## 两组扩展场景

开发组8个：只能提前、窗口与截止、截止前无空位、灵活素材排在输入前但固定素材应优先、三个频道同时间独立、人工审核与只能提前、缺版权字段、用户明确确认提前。

验证组3个：换日期和期望时刻的只能提前、只能推迟且窗口较窄、同批社交频道时段竞争。验证前冻结业务源代码和模型提示，执行记录保存source_sha256和suite_sha256；验证组不用于调整提示。首次3项验证通过后，基线批量测试推动新增结束保护，因此最终验证是同组数据的回归复测，不能再称全新未见盲测。它由同一开发方设计，因此只是保留验证集，不是独立第三方盲测或统计泛化证明。

评估器从套件中提取request送给CLI。expected目标与答案单独保存在expectation.json，**不进入模型请求或业务工具返回**。Mock与live对应输入、资料快照一致，结果/动作可以不同；独立审计重新检查每条保存是否满足窗口、方向、截止、确认和冲突条件。

## 运行

```bash
python scripts/evaluate_expanded.py --mode mock --suite development --output runs/dev-mock
python scripts/evaluate_expanded.py --mode live --suite development --output runs/dev-live
python scripts/evaluate_expanded.py --mode mock --suite validation --output runs/validation-mock
python scripts/evaluate_expanded.py --mode live --suite validation --output runs/validation-live
```

输出目录必须不存在，保留每次运行的真实证据。记录位于examples/expanded，最终通过与失败情况以各summary.json及03-evaluation.md为准。

## 结束保护

模型若在已审核通过的素材尚未查询/保存时提前调用finish，结束工具返回premature_finish和遗漏ID。模型会收到这条真实观测，自行选择查询、保存或在工具失败后停止；程序不自动执行下一步。保护仍受同一步数上限限制，持续无法完成就返回incomplete。缺资料、风险阻断、查询窗口无可用时段、已出现工具错误等可以结束并保留结果。
