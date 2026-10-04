# AI Agent / RAG 聊天功能：第三阶段“工具调用和阵容推导”执行记录

> 状态：实施中
>
> 执行日期：2026-10-04
>
> 总实施方案：[jcc-ai-agent-rag-implementation-plan.md](jcc-ai-agent-rag-implementation-plan.md)
>
> 阶段实现计划：[JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_3_PLAN.md](JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_3_PLAN.md)

## 1. 执行范围与当前结论

本次开始实施第三阶段基础契约和持久化边界。已完成工具/source ORM 与 0005 migration、严格工具输入模型、静态白名单、结构化只读工具、确定性系统推导工具、工具审计 repository 方法、LLM tool-call DTO/非流式兼容入口及非敏感配置。尚未完成 orchestrator 多轮工具循环、run 级 snapshot 持久化、tool/source SSE 事件和完整阶段验收，因此阶段不能标记为完成，也未进入阶段四。

## 2. 实际变更

- `app/agent/models.py`：新增 `AgentToolCall`、`AgentMessageSource` 及状态/来源类型约束和索引。
- `alembic/versions/0005_agent_tools_and_sources.py`：从 0004 创建两张审计/provenance 表，包含外键、唯一 tool-use 约束和索引。
- `app/models/__init__.py`：注册新增模型供 metadata/migration 发现。
- `app/conversations/repository.py`：新增 tool call 创建、条件状态更新和 source 创建操作。
- `app/agent/tools/`：新增严格 Pydantic schemas、静态 registry、固定 snapshot 的结构化 JCC 查询和 `system_derived` 阵容候选工具；未注册 `search_knowledge` 或任意 SQL/shell 工具。
- `app/agent/llm/base.py`、`openai_compatible_client.py`：新增 provider-neutral `ToolCall`/`LLMResponse` 和 OpenAI-compatible `complete_with_tools` JSON 解析入口；既有文本 stream 保留。
- `app/core/config.py`、`.env.test.example`、`.env.product.example`：新增工具循环/超时/输入输出/来源摘要上限配置。
- 阶段三计划和本执行记录：已创建并明确未完成项。

## 3. 已验证结果

- `ruff` 对已修改 agent/conversation/model/migration 文件通过。
- Python compileall 对 app 和 0005 migration 通过。
- 默认 registry smoke check 通过，工具名称稳定且不包含阶段四 `search_knowledge`。
- LLM 关闭配置下阶段二定向回归：16 passed；随后修复阶段二显式取消状态可见性竞态后，全量回归为 138 passed（2 条既有第三方 deprecation warnings）。
- `ruff check`、`compileall` 和 `git diff --check` 通过。
- 默认 registry smoke check 通过，工具名称稳定且不包含阶段四 `search_knowledge`。
- `alembic heads` 显示唯一 head `0005_agent_tools_and_sources`。
- 尚未执行 `alembic check`、`pdm lock --check` 或隔离 PostgreSQL migration；长期数据库状态未被修改。

## 4. 未完成或未执行

- orchestrator 尚未接入完整 `LLM → tool → tool_result → LLM` 多轮循环；当前新增 LLM 完整调用入口尚未成为 runtime 默认路径。
- 尚未在 `AgentRun` 保存 snapshot/version/content hash，也未完成显式 snapshot-scoped repository 全量 API；工具函数要求后端传入 snapshot context，但 context 构建与 run 一致性仍待接入。
- 尚未发送/回放 `tool.started`、`tool.completed`、`tool.failed`、`source` SSE 事件。
- 尚未新增阶段三专用自动化测试文件；非法参数不执行、审计状态、双 snapshot、一轮/多轮 tool call、阵容来源和工具取消专项证据待补。
- 未执行长期共享数据库或生产 migration；未执行真实 provider/生产部署；未执行隔离 PostgreSQL 0005 round-trip。

## 5. 当前验收映射

| 编号 | 验收标准 | 结果 | 证据 |
| --- | --- | --- | --- |
| AC-3-01 | 非法工具参数不会执行 | 部分完成 | schema/registry 已拒绝未知字段和边界值；executor/orchestrator 证据待补 |
| AC-3-02 | 不允许任意 SQL | 基础边界通过 | registry 仅静态只读工具，查询使用固定 SQLAlchemy 表达式；专项测试待补 |
| AC-3-03 | 工具调用可审计 | 部分完成 | ORM/migration/repository 已建立；运行时写入和终态测试待补 |
| AC-3-04 | 阵容结果明确标记系统推导 | 工具层通过 | `result_type=system_derived_lineup`、`is_system_derived=true`、`source_type=system_derived` |
| AC-3-05 | 使用固定 snapshot 避免混合版本 | 未完成 | ToolContext 字段已定义；run 固定与双 snapshot 验证待补 |

## 6. 文档与后续入口

总方案的阶段三状态和链接将在本阶段代码闭环、测试及真实迁移验证结果明确后同步；当前不能宣称阶段完成。下一步应先完成 provider-neutral 多轮工具循环和 run snapshot 持久化，再补 tool/source SSE、Fake LLM 分层测试、隔离 PostgreSQL 验证及全量回归。阶段四 RAG 能力继续保持非本阶段范围。
