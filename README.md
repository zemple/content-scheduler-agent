# 素材审核与发布排期 Agent

小影校招 AI Agent 开发考核 · **第 1 题**。帮助内容团队审核合成素材、查询冲突并保存可确认的排期草稿。**所有结果都是草稿，不执行发布。**

采用 Python CLI、真实模型原生工具调用和 SQLite。模型观察工具返回后决定补查时段、保存建议或结束；程序强制审核、确认、幂等和保存约束。离线 Mock 是固定策略，**只证明流程，不证明真实模型效果**。

## 评审入口

1. [需求和全部验收点](docs/01-requirements.md)
2. [设计、动态决策和取舍](docs/02-solution.md)
3. [实际测试、真实运行、失败与限制](docs/03-evaluation.md)
4. [AI 使用和个人贡献边界](docs/04-ai-usage.md)
5. [复盘和下一步](docs/05-retrospective.md)

先看 [真实模型冲突 Trace](examples/live/conflict/trace.jsonl)、[冲突结果](examples/live/conflict/output.json) 和 [真实模型验证汇总](examples/live/summary.json)。对比 [正常路径](examples/live/normal/trace.jsonl) 与 [更换占用时段后的路径](examples/live/changed_occupancy/trace.jsonl)。

本次扩展到 **20条素材、64条已有排期、一周三个频道**。新增“只能提前/推迟、时间窗口、截止时间、固定素材优先”并在保存工具中强制检查。详见 [扩展排期说明](docs/advanced-scheduling.md)，以及 [只能提前的真实Trace](examples/expanded/development/live/earlier_only/trace.jsonl) 和 [固定素材优先Trace](examples/expanded/development/live/fixed_priority/trace.jsonl)。

## 环境和安装

Python **3.11 或以上**，Windows/macOS/Linux。运行和测试没有第三方依赖；联网模式需支持 Chat Completions 原生 function calling 的模型接口。本次实际使用模型名称及响应 ID 记录在 live Trace 中。

从项目根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Windows 激活命令：`.venv\Scripts\activate`。若不安装，也可在仓库根目录以 `PYTHONPATH=src python3 -m scheduling_agent ...` 运行（Windows 用环境变量设置 PYTHONPATH）。

## 无密钥离线运行

```bash
python -m scheduling_agent run --mode mock \
  --input examples/conflict.input.json \
  --db runs/demo.sqlite --out-dir runs/demo
```

默认数据中 18:00 已占用；应建议北京时间 19:00，并保存为 `pending` 草稿。输出 UTC `2026-11-01T11:00:00+00:00` 与北京时间 19:00 是同一时刻。

输出目录包含 `input.json`、`output.json`、`trace.jsonl` 和真实数据库导出的 `drafts.json`。同一输出目录重跑会覆盖这些展示文件；保留历史时请使用不同目录。数据库不会自动清空。

查看实际数据库草稿：

```bash
python -m scheduling_agent inspect --db runs/demo.sqlite
```

重复运行相同请求并使用同一数据库，只会返回同一草稿 ID。相同 `request_id` 换内容会拒绝；修改方案/补充确认时用新的 `request_id`。

## 真实模型运行

```bash
cp .env.example .env
# 在本机 .env 填入 SCHEDULER_API_KEY 和 SCHEDULER_MODEL，不要提交该文件
python -m scheduling_agent run --mode live \
  --input examples/conflict.input.json \
  --db runs/live-demo.sqlite --out-dir runs/live-demo
```

配置项：`SCHEDULER_API_KEY`、`SCHEDULER_MODEL`、`SCHEDULER_BASE_URL`、`SCHEDULER_TIMEOUT`。Shell 中已有变量优先于 `.env`。无配置不自动退回 Mock，明确报告配置错误。每次模型请求默认超时 45 秒；每个请求默认最多 12 个决策步骤（含 finish）。批量最多 10 个素材，可显式增加 `--max-steps`，最高 100。

调用会产生所用服务的 API 费用；业务数据均为合成资料。Trace 不记录 Authorization 请求头或密钥，也不保存模型内部思维。

## 输入与确认

```json
{
  "request_id": "confirmed-followup-002",
  "items": [{
    "material_id": "M01",
    "requested_time": "2026-11-01T18:00:00+08:00",
    "hard_time": true
  }],
  "confirmations": {"M01": "2026-11-01T19:00:00+08:00"}
}
```

`confirmations` 代表运行 CLI 的用户明确同意这个素材的这个替代时段；模型没有写入确认的工具。没有该字段时，改变硬性时间只能保存待确认建议。`hard_time=false` 表示用户允许调整；不代表能绕过规则风险。人工审核风险始终保留 `manual_review`，本项目不实现人工审核通过接口。

每项素材还支持可选 `adjustment`（`earlier_only`/`later_only`/`either`）、`not_before`、`not_after`、`deadline`。前两个窗口边界包含，deadline必须严格早于；这些条件不能通过时间确认绕过。自然语言要求目前需由操作者录入为这些结构化字段，没有自动解析器。

合成规则规定：每频道每小时一个时段，时间必须带时区并对应 UTC 整点；同一批次的待确认建议不能互相冲突。待确认建议不占用其他请求的时段，所以后续明确确认仍需重新检查。`confirmed` 仅表示草稿的确认状态，绝不表示已发布。

## 测试与复现

```bash
python -m unittest discover -s tests -v

# 输出路径必须尚不存在，避免覆盖证据
python scripts/reproduce.py --mode mock --output runs/reproduced-mock
python scripts/reproduce.py --mode live --output runs/reproduced-live

# 更复杂的时间约束和批量竞争；验证组单独执行
python scripts/evaluate_expanded.py --mode live --suite development --output runs/expanded-dev
python scripts/evaluate_expanded.py --mode live --suite validation --output runs/expanded-validation

# 只复现指定场景
python scripts/reproduce.py --mode live --cases normal,conflict,save_failure \
  --output runs/three-live-cases

# 不调用模型，检查提交记录中的数据摘要、状态与 Trace 一致性
python scripts/audit_evidence.py
```

注入持续保存失败：

```bash
python -m scheduling_agent run --mode mock --input examples/normal.input.json \
  --fail-tool save_draft --db runs/failure.sqlite --out-dir runs/failure
```

`--fail-count 1` 只注入第一次失败；默认 `-1` 持续失败。模型观察错误后可决定重试或停止；运行器不自动把失败改成成功。

退出码：`0` 完成或已有待确认草稿；`2` 缺信息/规则阻断等已识别业务问题；`3` 未完成或部分完成（含模型错误、步数上限、保存失败）；`4` 输入/配置/文件错误。业务失败也是应覆盖的验收结果，不等于所有场景都应返回 0。

## 项目结构

```text
src/scheduling_agent/  输入校验、SQLite、业务工具、模型适配、Agent 循环、CLI
tests/                业务不变量、循环、HTTP 协议、跨进程 CLI 测试
data/                 合成素材、审核规则、已有排期
examples/mock/        离线实际运行记录
examples/live/        真实 API 实际运行记录和汇总
examples/expanded/    复杂时间约束的开发/验证组，分别保存 mock/live
examples/failures/    改进前真实失败记录，保留用于复盘
scripts/              批量复现、记录一致性检查
docs/                 五份指定文档、设计与实施记录
```

## 范围与提交

不接真实发布、外部素材平台、生产审核或用户认证。数据文件每次启动读取为一个快照，不热加载；SQLite 草稿占位在查询/保存时实时检查。输入授权基于本地 CLI 用户，不是生产身份认证。模型可能过早停止，程序会如实标记未完成，单次通过不代表泛化成功率。完整限制见评估文档。

提交题号 1、GitHub 仓库 URL 及 `git rev-parse HEAD` 的完整 SHA。最终 SHA 在提交后提供，不把待生成的 SHA 写成已完成。实际自动化协作用时记录见 [work-log.json](docs/work-log.json)；候选人独立理解和修改用时须另行如实记录。

当前增强版本的测试与通过数见docs/03-evaluation.md和examples/test-results.txt。保留验证组在基线结束保护修复后有回归复测，未声称最终是未见盲测；详细过程可查data/evaluation/protocol.json。
