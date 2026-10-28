# 03 · 评估、实际结果与限制

## 执行环境与方法

本机 Python 3.13.7；README 干净虚拟环境安装后运行标准库 unittest。结果：[test-results.txt](../examples/test-results.txt)，**68 项测试全部通过**。其中本机 HTTP 服务器验证模型协议；在禁止监听回环端口的沙箱中该项会因环境权限失败，需要允许本地端口；不应把它当作业务测试失败或静默跳过。

业务工具是本地实际函数，草稿实际保存到 SQLite。故障注入模拟业务工具失败；素材和规则全为合成数据。离线模型是固定策略；真实模型实际请求 `gpt-4.1-mini`，服务返回版本 `gpt-4.1-mini-2025-04-14`，逐步响应 ID、token usage 和耗时可查 Trace。

原有16个基线场景保留，复杂排期另设8个开发场景和3个保留验证场景。三项基线场景各执行两遍，每种模式合计30次执行，两种模式共60份最终记录。基线汇总：[Mock](../examples/mock/summary.json) / [live](../examples/live/summary.json)；扩展汇总：[开发Mock](../examples/expanded/development/mock/summary.json) / [开发live](../examples/expanded/development/live/summary.json) / [验证Mock](../examples/expanded/validation/mock/summary.json) / [验证live](../examples/expanded/validation/live/summary.json)。最终结果与通过数以下方扩展表和这些实际文件为准，不用开发试跑代替最终证据。

## 验收点与实际材料

每个目录有 `input.json`、`output.json`、`trace.jsonl`、`drafts.json`、`execution.json` 和数据快照 `data/`。重复场景另分 first/second 子目录；可以直接比较两次。

| 场景 | 测试预期 | 实际结果 | 真实记录 |
|---|---|---|---|
| 正常排期 | M01 在空闲 19:00 保存 | completed，1 份 confirmed 草稿 | [输入](../examples/live/normal/input.json) / [输出](../examples/live/normal/output.json) / [Trace](../examples/live/normal/trace.jsonl) |
| 信息缺失 | M02 缺版权确认，不保存 | needs_input，缺失 rights_confirmed，0 份 | [输出](../examples/live/missing/output.json) / [Trace](../examples/live/missing/trace.jsonl) |
| 规则风险 | M03 命中演示禁用声明，阻断 | blocked，风险 unsupported_claim，0 份 | [输出](../examples/live/risk/output.json) / [Trace](../examples/live/risk/trace.jsonl) |
| 时段冲突 | 18:00 被占用，补查并待确认 | 模型查替代时段，建议 19:00，pending 草稿 | [输出](../examples/live/conflict/output.json) / [Trace](../examples/live/conflict/trace.jsonl) |
| 重复提交 | 相同请求只产生一份草稿 | 两次同 ID，第二次 duplicate=true，仍 1 份 | [第一次](../examples/live/duplicate/first/output.json) / [第二次](../examples/live/duplicate/second/output.json) / [数据库导出](../examples/live/duplicate/second/drafts.json) |
| 保存失败 | 保存工具持续失败，不报成功 | incomplete，draft_status=failed，0 份 | [输出](../examples/live/save_failure/output.json) / [Trace](../examples/live/save_failure/trace.jsonl) |
| 读取工具失败 | 停止或有限重试，不猜资料 | incomplete，审核 not_checked，0 份 | [输出](../examples/live/tool_failure/output.json) / [Trace](../examples/live/tool_failure/trace.jsonl) |
| 更换占用时段 | 18:00/19:00 都占用 | 建议变为 20:00，仍待确认 | [输出](../examples/live/changed_occupancy/output.json) / [Trace](../examples/live/changed_occupancy/trace.jsonl) |
| 用户明确确认 | 用户确认改为 19:00 | confirmed 草稿，原要求保留 18:00 | [输入](../examples/live/confirmed/input.json) / [输出](../examples/live/confirmed/output.json) |
| 人工审核 | 时间可用但含合作标签 | needs_review，pending，manual_review | [输出](../examples/live/manual_review/output.json) / [Trace](../examples/live/manual_review/trace.jsonl) |
| 无可用时段 | 搜索窗口已占用 | incomplete，无可保存建议；不声称所有未来永远无空位 | [输出](../examples/live/no_slots/output.json) / [Trace](../examples/live/no_slots/trace.jsonl) |
| 步数上限 | 只允许 1 步 | incomplete/step_limit，已有观测保留 | [输出](../examples/live/step_limit/output.json) / [Trace](../examples/live/step_limit/trace.jsonl) |
| 多素材部分成功 | M01 保存，M03 阻断 | partial，已成功的 1 份草稿保留 | [输出](../examples/live/batch/output.json) / [Trace](../examples/live/batch/trace.jsonl) |
| 同请求重跑时排期变化 | 历史草稿不能继续称有效 | 同 ID、仍 1 份历史草稿，needs_revalidation/incomplete | [第一次](../examples/live/repeat_changed_occupancy/first/output.json) / [第二次](../examples/live/repeat_changed_occupancy/second/output.json) |
| 同请求重跑时规则变化 | 新风险应阻断已有方案 | 历史草稿保留，needs_revalidation/blocked | [第一次](../examples/live/repeat_changed_rules/first/output.json) / [第二次](../examples/live/repeat_changed_rules/second/output.json) |
| 同批两个待确认建议 | 建议时段不能互相冲突 | 两份 pending 草稿，分别建议 19:00/20:00 | [输出](../examples/live/batch_pending/output.json) / [Trace](../examples/live/batch_pending/trace.jsonl) |

时间输入以北京时间举例；JSON 输出规范化为 UTC，19:00 对应 11:00+00:00，20:00 对应 12:00+00:00。

## 动态决策证据

对比正常和冲突 Trace：正常在第一次 query_slots 返回可用后调用 save_draft；冲突在第一次 query_slots 返回不可用后选择再次 query_slots，再选择可用时段保存为待确认草稿。变更占用后，工具返回不同、模型建议随之变化。它们来自真实 API 响应，不是预设决策日志。

保存失败 Trace 中，模型观察错误后尝试有限重试，失败仍明确输出 failed；实际数据库导出为空。验证同时核对保存工具返回和数据库行，不能只看模型说了什么。

## 开发期间真实失败及改进

保留 [失败输出](../examples/failures/batch-premature-finish/output.json) 与 [失败 Trace](../examples/failures/batch-premature-finish/trace.jsonl)：模型发现 M03 阻断后提前结束，M01 虽已审核但未保存，数据库 0 行，未达到预期 1 行。不是所有开发试跑都通过。

改进系统提示，明确每素材独立、finish 是全局结束、正常素材也应处理到终态。最终相同 batch 场景保存 M01 并阻断 M03，实际步数以Trace为准。一次修正后通过不意味着模型永远不会提前结束，程序仍如实报告遗漏。

回归测试还复现并修复：旧草稿被错误当作当前有效、保存失败丢失待确认项、同批 pending 推荐冲突、畸形工具名崩溃、日期溢出。独立审查过程见 [review.md](review.md)。

## 复现与证据检查

```bash
python -m unittest discover -s tests -v
python scripts/reproduce.py --mode mock --output runs/evaluation-mock
python scripts/reproduce.py --mode live --output runs/evaluation-live
python scripts/audit_evidence.py
```

复现会真实调用工具并生成新输出，不会拷贝旧 Trace。live 需要自己的有效配置。audit_evidence 不调用模型，检查提交的 60 份记录：源数据摘要、状态、步骤/next_action、数据库导出、确认边界、重复 ID；不把 summary 的 pass 字段单独作为证据。源文件摘要见 examples/source-manifest.json。

## 已知限制

- 仅小规模合成场景，最终各一次（重复场景两次）；无统计置信度、真实运营效果或跨模型质量承诺。
- 规则检查是结构化字段和关键词，不是视频/音频内容审核；未命中规则不代表真实平台合规。
- 模型可能漏做、过早结束或给出不佳的简短理由；程序保证状态真实，不能保证每次最优决策。
- JSON 文件是启动快照；SQLite 保证同数据库内事务，不同步外部系统、不覆盖多数据库分布式冲突。
- 待确认建议跨请求不占位，后续确认需重验；没有取消/修改历史草稿的完整生命周期。
- 用户确认信任本地 CLI 操作者，没有生产身份认证；人工审核标签不能在本项目中自动批准。
- 不接真实发布、线上部署或界面；这些不属于题目必需范围。没有声称做过未执行的候选人个人修改。


## 扩展的复杂时间表验收

最终两种模式均通过：基线16/16、开发8/8、保留验证回归3/3；每种模式共27个场景，累计60次实际执行。

默认20条素材、64条已有排期、一周三个频道。Mock与live对应请求、资料快照一致；expected验收答案不进入模型请求。新增输入约束均由保存工具强制检查。

| 组别/场景 | 预期结果 | 真实记录 |
|---|---|---|
| 开发：earlier_only | 周五18:00占用，只能提前，建议17:00 | [输出](../examples/expanded/development/live/earlier_only/output.json) / [Trace](../examples/expanded/development/live/earlier_only/trace.jsonl) |
| 开发：window_deadline | 18:00..19:00允许、20:00前，选择19:00 | [输出](../examples/expanded/development/live/window_deadline/output.json) |
| 开发：deadline_no_slot | 截止前允许时段均占用，明确未完成 | [输出](../examples/expanded/development/live/deadline_no_slot/output.json) |
| 开发：fixed_priority | 灵活素材排在输入前，仍为固定素材保留18:00，灵活素材19:00 | [输出](../examples/expanded/development/live/fixed_priority/output.json) / [Trace](../examples/expanded/development/live/fixed_priority/trace.jsonl) |
| 开发：three_channels | 三个频道可在同一19:00各保存一份 | [输出](../examples/expanded/development/live/three_channels/output.json) |
| 开发：manual_earlier | 合作素材提前到17:00，仍待人工审核 | [输出](../examples/expanded/development/live/manual_earlier/output.json) |
| 开发：missing_rights | 新素材缺版权字段，不保存 | [输出](../examples/expanded/development/live/missing_rights/output.json) |
| 开发：confirmed_earlier | 硬性要求18:00，用户确认17:00后可确认草稿 | [输出](../examples/expanded/development/live/confirmed_earlier/output.json) |
| 验证：shifted_clock | 换到周六15:00占用，只能提前，选择14:00 | [输出](../examples/expanded/validation/live/shifted_clock/output.json) |
| 验证：later_window | 换日期/频道，12:00占用，只能推迟，选择13:00 | [输出](../examples/expanded/validation/live/later_window/output.json) |
| 验证：batch_collision | 社交频道固定素材19:00、灵活素材20:00 | [输出](../examples/expanded/validation/live/batch_collision/output.json) |

每份verification.json由独立检查代码重核窗口、严格截止、方向、确认边界、已有排期与同批冲突；不是只相信汇总里的passed字段。最终source_sha256一致性由audit_evidence重新检查。

保留验证组首次在实现/提示冻结后运行3/3通过，后来基线批量场景促成结束保护和最新观测修复，因此最终同组验证属于回归复测，**不是全新未见盲测**。未用其结果调整提示，协议与源摘要在data/evaluation/protocol.json。由同一开发方设计，不能作为独立泛化证明。

扩展期间发现并保留：[旧回归说明](../examples/failures/upgrade-legacy-regression/notes.md)。部分早期失败来自测试未明确允许时间范围，修正了请求定义，而非把合法更早建议硬说成模型错误；批量过早结束是真实漏做，新增premature_finish结束保护让模型观察遗漏后自行选择下一步。一次实际恢复记录见[Trace](../examples/recovery/premature-finish/trace.jsonl)。没有通过自动替模型查询/保存来掩盖失败。
