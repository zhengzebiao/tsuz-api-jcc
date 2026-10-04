# AI Agent / RAG 聊天功能：第三阶段“工具调用和阵容推导”执行记录

> 状态：已完成（阶段三核心验收与受控环境验证完成；生产部署和长期运行验证不在本记录范围）
>
> 执行日期：2026-10-04
>
> 总实施方案：[jcc-ai-agent-rag-implementation-plan.md](jcc-ai-agent-rag-implementation-plan.md)
>
> 阶段实现计划：[JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_3_PLAN.md](JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_3_PLAN.md)

## 1. 执行范围与结论

本阶段完成白名单结构化工具、输入校验、工具调用循环、工具调用与来源持久化、系统阵容推导、固定 snapshot、SSE 工具/source 事件及受控 PostgreSQL migration 验证。用户随后通过实际接口提交“奥恩的护甲是多少”，获得了依据当前 S19 官方 snapshot 的回答，并确认 `agent_tool_calls`、`agent_message_sources`、`agent_runs` 的 snapshot/version 关联一致。阶段三核心验收通过，可以进入阶段四；生产部署、生产 migration、持续负载和真实 provider 长期运行验收仍未执行。

## 2. 实际完成内容

- 新增 `AgentToolCall`、`AgentMessageSource` ORM 和 0005 Alembic migration，工具调用独立审计，来源记录只覆盖结构化官方数据及系统推导，不包含 RAG 文档能力。
- 新增静态工具 registry、严格 Pydantic schema 和只读结构化查询工具；未开放任意 SQL、shell、写数据或 snapshot 切换工具。
- 新增确定性阵容候选工具，结果显式标记 `system_derived`。
- 扩展 provider-neutral LLM tool-call DTO 和 OpenAI-compatible 工具请求/响应解析。
- Orchestrator 实现多轮 tool-call → tool-result → final answer，错误安全回传，支持最大迭代、工具超时/取消、工具输出上限、审计记录和 source 记录。
- run 启动时固定 JCC 当前 snapshot 元数据（mode、season、version、revision、content hash），工具只使用 run context 中的 snapshot；SSE 增加 `tool.started`、`tool.completed`、`tool.failed` 和 `source` 事件。
- 修复 OpenAI-compatible 上游错误诊断，400 响应提供有限且脱敏的上游错误摘要；工具调用 assistant 消息无文本时序列化为 `content: null`，适配拒绝空字符串内容的兼容代理。
- 修复 runtime factory 到 `ConversationRuntimeManager` 的工具参数转发。
- 新增工具 schema/registry、审计持久化、tool-call adapter、orchestrator 多轮测试。

## 3. 验证结果

| 检查 | 命令/环境 | 结果 |
| --- | --- | --- |
| 全量测试 | `LLM_MODEL= LLM_BASE_URL= LLM_API_KEY= ./.venv/bin/pytest -q` | 146 passed，2 条既有第三方 deprecation warnings |
| 定向 Ruff | 本阶段修改/新增 Python 文件 | 通过 |
| 编译 | `python -m compileall`（app/tests/migration） | 通过 |
| Diff | `git diff --check` | 通过 |
| 依赖锁 | `pdm lock --check` | 通过 |
| Alembic heads | `.venv/bin/alembic heads` | 唯一 head：`0005_agent_tools_and_sources` |
| 隔离 PostgreSQL migration | 一次性 pgvector/PostgreSQL 16 容器 | `upgrade head → downgrade 0004_agent_runs → upgrade head` 成功，最终 revision 为 0005；测试容器已清理 |
| 长期数据库 `alembic check` | 当前长期开发库 | 未通过：目标库落后于 0005 head；没有在长期库执行迁移。此结果不替代隔离 migration 验证 |
| 实际接口主链路 | 用户受控测试环境，问题“奥恩的护甲是多少” | 成功返回当前 S19 snapshot 答案；用户确认 tool call/source/run 的 snapshot/version 一致 |
| 真实 provider 长期运行/生产部署 | 生产环境 | 未执行，不属于本阶段核心验收 |

## 4. 阶段验收映射

| 编号 | 总方案验收标准 | 结果 | 证据 |
| --- | --- | --- | --- |
| AC-3-01 | 非法工具参数不会执行 | 通过 | 严格 Pydantic 输入 schema、unknown/invalid 参数 rejected 路径及工具测试 |
| AC-3-02 | 不允许任意 SQL | 通过 | 静态白名单、固定 SQLAlchemy 只读查询；registry 测试确认无 SQL/shell/写入/snapshot 切换工具 |
| AC-3-03 | 工具调用可审计 | 通过 | `agent_tool_calls` ORM/migration/repository/orchestrator 终态写入；持久化专项测试及用户实际 DB 核对 |
| AC-3-04 | 阵容结果明确标记系统推导 | 通过 | 阵容工具返回 `result_type=system_derived_lineup`、`is_system_derived=true` 和 `source_type=system_derived`；专项测试通过 |
| AC-3-05 | 固定 snapshot，避免混合版本 | 通过 | run 固定 snapshot 元数据、tool context/source/tool-call 使用同版本；隔离 PostgreSQL migration 和用户实际请求数据核对通过 |

## 5. 未执行项及边界

- 未对长期共享开发库或生产数据库执行迁移；用户需按备份/发布流程自行在目标环境执行 `alembic upgrade head`，再执行 `alembic check` 和接口验证。
- 未执行生产部署、生产流量/负载、安全渗透或 provider 长期可靠性验证。
- 未实现阶段四 `rag_documents`、Embedding、全文/向量索引、混合检索和 `search_knowledge`。
- 全仓库 Ruff 若扫描旧文件会发现既有 B008/TRY004 问题；本阶段新增/修改文件定向 Ruff 检查通过。

## 6. 文档同步与阶段结论

- [总方案](jcc-ai-agent-rag-implementation-plan.md)：阶段三状态已更新为“已完成”，链接本阶段计划和执行记录；阶段四范围未提前实现。
- [阶段三计划](JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_3_PLAN.md)：状态更新为“已完成”，保留验收契约和边界。
- 本执行记录：记录实际变更、测试/migration/API 结果、未执行项和下一阶段边界。

阶段三核心验收完成，可开始阶段四规划/实施。长期数据库升级是部署操作，应先备份并确认目标环境后再执行。
