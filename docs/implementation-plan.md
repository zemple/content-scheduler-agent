# Implementation Plan

Goal: 完成题号 1 的可复现交付，不虚报真实模型或 GitHub 状态。
Spec: docs/design.md；完整验收映射见 01-requirements.md。
Architecture: Python CLI -> bounded model/tool loop -> local tools -> SQLite.
Tech stack: Python >=3.11, stdlib unittest/sqlite3/urllib, setuptools packaging.

## Global constraints
- 不发布；硬性时间变更必须用户确认；仅保存草稿。
- 所有状态由实际工具/数据库结果决定；模型不可自行声明已保存。
- 12 步默认上限；Mock 和 live 明确分离。

## Tasks
- [x] 1. tests/test_scheduler.py 先定义业务验收和不变量，运行观察失败；实现 domain.py、store.py、tools.py，运行相关测试。
- [x] 2. tests/test_agent.py 先定义循环、错误、上限、API 协议测试，运行失败；实现 models.py、agent.py，运行全套。
- [x] 3. tests/test_cli.py 先定义 CLI 重复执行、退出码和产物；实现 __main__.py、安装元数据、重现脚本；生成实际例子。
- [x] 4. 写五份指定文档、README、AI 使用边界、评估映射。真实模型接口可用时生成 live Trace，否则明确待验。
- [x] 5. 全套验证、独立审查、修复并复测、干净安装重跑、Git 提交、确认可用 GitHub 交付途径。

## Review focus
用户确认不能由模型伪造；并发保存不能重占时段；同请求重复不能返回别的内容；不同表示同一时区时刻不能绕过冲突；最终结果不能掩盖保存失败或未检查状态。

## Execution ledger

- Task 1: 业务验收先失败（缺少模块），实现后22项通过。
- Task 2: 循环/协议先失败，实现后36项通过；本机HTTP测试须允许回环端口。
- Task 3: CLI五项先失败，实现后41项通过；完成干净虚拟环境安装与入口验证。
- Task 4: 完成指定五份文档和真实运行；保留批量提前结束失败记录，提示改进后复测。
- Task 5: 独立只读审查，边界回归修复；最终51项测试、16+16场景、38份记录审计通过。GitHub上传和公开访问验证见最终交付回复。
- Decision: 用户授权直接完成公开GitHub交付；新建独立子目录，无原仓库或基线分支，不进行不适用的worktree/PR合并。
- Decision: 运行资料采用启动快照，SQLite占位实时读取；生产同步不在范围，已在文档披露。
