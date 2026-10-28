# 独立审查与修复记录

按 requesting-code-review 技能，独立只读审查代理检查 src、tests、设计和真实 Trace；未读取 .env，未修改代码。审查不属于业务 Agent 的运行链路。

发现：

- P2：Action.tool 未检查类型，畸形 API 工具名 [] 可导致未捕获 TypeError。现在适配器和循环入口都校验，工具路由也防御性处理；test_malformed_tool_name_is_model_error 验证返回 model_error 并保留记录。
- 日期边界问题：UTC 转换与候选时段加法可溢出，导致无结果崩溃。按输入可靠性修复为 ValidationError/invalid_arguments；两个极限日期回归用例已复现失败后通过。
- 审查同时提醒旧草稿的频道变化也应重验，已补 test_changed_material_channel_invalidates_historical_draft。

执行方另外修复并测试：保存后外部冲突/规则变化时重跑、保存失败的待确认项、同批待确认建议冲突。最终完整测试输出在 examples/test-results.txt。审查环境的本地 HTTP 测试被监听端口权限阻止，执行方在允许回环监听的环境运行全套通过。

已作的工程决定：数据文件按每次运行的快照处理，不实现运行中热加载；文档明确边界，跨请求重新运行会读取新文件。不存在尚未披露的已知关键缺陷；模型泛化和生产能力限制在 03-evaluation.md 中说明。

## 本次扩展独立审查

对feat/richer-scheduling相对5999d79的未提交变更另作独立只读审查。发现最终历史草稿重验仅用基础窗口检查，漏掉同批固定目标保护。复现：固定素材最初版权未授权，灵活素材先保存；随后固定素材恢复可排，查询与保存已拒绝灵活素材，最终重验却仍认为有效。新增test_final_recheck_includes_newly_viable_fixed_item先复现失败，再统一调用time_violations修复。另补离线前移查询在datetime最小日期下溢出的回归测试。审查未读取密钥、未参与真实模型调用、未使用验证组调整提示。

真实基线batch再次提前结束后增加结束保护与拒绝finish观测回传；测试test_premature_finish_returns_observation_then_model_can_continue先失败后通过，本机HTTP协议测试确认premature_finish确实被放入下一轮消息。它验证执行终态，不代替模型选工具，也不取消步数限制。

结束保护复审发现旧查询available=true会阻止后来确认无空位的合法结束；新增test_finish_uses_latest_slot_observation先复现失败，改为同一时段最新观测覆盖旧观测，同时结果不再推荐已被更新为不可用的时间。回归后68项通过。
