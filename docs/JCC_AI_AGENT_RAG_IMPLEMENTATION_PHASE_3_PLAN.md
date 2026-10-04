# AI Agent / RAG 聊天功能：第三阶段“工具调用和阵容推导”实现计划

> 状态：已完成（核心验收通过；生产发布与长期数据库运行验证不在本阶段）
>
> 总实施方案：[jcc-ai-agent-rag-implementation-plan.md](jcc-ai-agent-rag-implementation-plan.md)
>
> 阶段执行记录：[JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_3_EXECUTION.md](JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_3_EXECUTION.md)
>
> 范围：在阶段二文本 Agent 基础上增加受控结构化工具契约、工具注册/输入校验、审计来源模型和系统阵容推导；不提前实现阶段四 RAG。

## 1. 阶段基准与目标

阶段二已提供 `AgentRun`、单进程 runtime、取消/超时生命周期、SSE event sink 和 provider-neutral LLM 基础接口；阶段二执行记录明确未实现工具调用、工具审计、结构化工具、阵容推导和来源记录。本阶段复用这些边界，不改变 JWT scope、owner 查询、202 消息接口、SSE 断开语义或单 worker 限制。

目标：

1. 以固定白名单和 Pydantic `extra="forbid"` 输入模型提供只读 JCC 工具；
2. 为工具调用和结构化/system-derived 来源建立独立、有限、可审计的持久化记录；
3. 提供 provider-neutral tool-call DTO 和 OpenAI-compatible 多轮调用基础；
4. 固定一次 run 使用的 snapshot，阵容结果明确标记 `system_derived`；
5. 保持阶段二文本流回归，并补充非法参数、危险能力排除和迁移验证。

## 2. 范围与非目标

实现：工具注册表、输入 schema、结构化英雄/羁绊/装备/强化符文/奇遇/银河查询、确定性阵容候选工具、`agent_tool_calls`、最小 `agent_message_sources` provenance 表、LLM tool-call DTO/适配器入口、工具循环配置基础。

不实现：`rag_documents`、文档生成、Embedding、PostgreSQL FTS/pgvector、`search_knowledge`、外部向量库、任意 SQL/shell/写入/snapshot 切换工具、多 worker/多容器队列。

## 3. 数据与安全契约

0005 migration 新增 `agent_tool_calls` 和 `agent_message_sources`。输入/输出必须有界并脱敏；tool call 状态限定为 `requested/running/succeeded/failed/timeout/rejected/cancelled`。来源只允许阶段三的 `official_structured_data`、`system_derived`、`model_explanation`。工具永远接收后端固定 snapshot context，不接受模型传入的 snapshot 覆盖值；所有查询使用显式 snapshot 条件和参数化 SQLAlchemy 查询。

阶段三注册工具：`get_snapshot_metadata`、`get_hero`、`search_heroes`、`get_trait`、`search_traits`、`get_equipment`、`search_equipment`、`search_augments`、`search_adventures`、`search_galaxies`、`derive_lineup_candidates`。`search_knowledge` 留到阶段四。

## 4. 修改文件与实施步骤

1. 扩展 `app/agent/models.py`、`app/models/__init__.py`，新增 tool/source ORM；新增 `alembic/versions/0005_agent_tools_and_sources.py`。
2. 扩展 `app/conversations/repository.py`，提供有限输入、条件状态更新和来源写入入口。
3. 新增 `app/agent/tools/{schemas,registry,structured,lineup}.py`，复用 JCC ORM 的只读实体查询，固定 snapshot 元数据并输出来源。
4. 扩展 `app/agent/llm/base.py` 和 OpenAI-compatible client 的统一 `ToolCall/LLMResponse/complete_with_tools` 契约；无工具文本流保持兼容。
5. 增加非敏感配置：最大工具循环、工具超时、输入/输出和来源摘要上限。
6. 后续实施中接入 orchestrator tool loop、tool/source SSE 事件和固定 snapshot 持久化；本计划不得把未完成内容写成已完成。

## 5. 测试与验证

定向测试应覆盖 registry 白名单、未知工具/额外字段拒绝、固定 snapshot 查询、阵容 `system_derived`、tool-call 状态条件更新、LLM tool-call JSON 解析和阶段二 runtime/API 回归。普通测试使用 SQLite/Fake LLM；JSON/外键/约束的真实契约使用一次性隔离 PostgreSQL，不操作共享或生产库。

```bash
./.venv/bin/pytest tests/test_agent_llm.py tests/test_agent_runtime.py tests/test_agent_conversations.py -q
./.venv/bin/ruff check <本阶段新增或修改的 Python 文件>
./.venv/bin/python -m compileall -q app alembic/versions/0005_agent_tools_and_sources.py
./.venv/bin/alembic heads
./.venv/bin/alembic check
pdm lock --check
./.venv/bin/pytest -q
git diff --check
```

## 6. 验收追踪

| 编号 | 验收标准 | 当前状态 | 证据 |
| --- | --- | --- | --- |
| AC-3-01 | 非法工具参数不会执行 | 部分满足 | schema/registry 与 loop 已有拒绝路径；专项测试待补 |
| AC-3-02 | 不允许任意 SQL | 满足基础边界 | 固定 registry 和显式 SQLAlchemy 查询；专项测试待补 |
| AC-3-03 | 工具调用可审计 | 部分满足 | 0005 ORM/migration/repository 与 orchestrator 写入已建立；状态专项测试待补 |
| AC-3-04 | 阵容结果明确标记系统推导 | 满足工具契约 | `lineup.py` 返回 `is_system_derived`/`source_type`；端到端测试待补 |
| AC-3-05 | 固定 snapshot，避免混合版本 | 部分满足 | run 已保存 snapshot 元数据并向工具传递 context；双 snapshot 集成测试待补 |

## 7. 风险与交付边界

当前长期数据库可能落后于 0004/0005，禁止未经授权执行共享库迁移。阶段状态保持“实施中”，直到 orchestrator 多轮 tool loop、SSE 事件、固定 run snapshot 和完整测试证据补齐。阶段四只能在本阶段验收闭环后开始。
