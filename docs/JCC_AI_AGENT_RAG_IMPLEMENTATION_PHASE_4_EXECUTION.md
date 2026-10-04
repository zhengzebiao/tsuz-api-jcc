# AI Agent / RAG 聊天功能：第四阶段“RAG”执行记录

> 状态：部分完成
>
> 执行日期：2026-10-04
>
> 总实施方案：[jcc-ai-agent-rag-implementation-plan.md](jcc-ai-agent-rag-implementation-plan.md)
>
> 阶段实现计划：[JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_4_PLAN.md](JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_4_PLAN.md)

## 1. 执行范围与结论

本阶段已完成 RAG 核心代码契约和离线索引/检索骨架，但 PostgreSQL/pgvector 专项 migration 与真实 SQL 验证尚未执行，因此不能标记为完全完成。现有结构化 Agent 能力和测试回归通过；可进入隔离数据库专项验收，不应据此宣称生产可用。

实际完成：

1. 新增 RAG index generation/current pointer ORM、规范化实体文档和 SHA-256 hash；
2. 新增确定性测试 embedding provider、向量校验、增量复用索引器和 `scripts.index_rag` 命令；
3. 新增 snapshot-scoped `search_knowledge` 工具、来源类型 `rag_document` 和 RAG 配置示例；
4. 新增阶段四计划文档及核心单元测试。

明确未执行或未完成：

- 未对共享长期数据库执行 `alembic upgrade head`；
- 未完成隔离 pgvector PostgreSQL 的 0006 upgrade/downgrade、TSVECTOR/GIN/vector 查询验证；
- 未调用真实 embedding provider；
- 当前 retriever 已提供 snapshot/index 过滤和确定性词项排序骨架，PostgreSQL FTS/vector 混合 SQL 尚待专项实现/验证；
- 未执行生产部署、生产索引或外部服务长期运行。

## 2. 实际代码与配置变更

### 2.1 RAG 数据与索引

- `app/rag/models.py`：新增 `RagIndexRun`、`RagCurrentIndex`、`RagDocument`；向量类型在 PostgreSQL 编译为 `VECTOR(1024)`，非 PostgreSQL 测试可导入。
- `alembic/versions/0006_rag_documents_and_indexes.py`：新增 generation、current pointer、documents 表、来源约束扩展、GIN/向量索引 DDL。
- `alembic/env.py`：注册 RAG 模型。

关键链路：

```text
固定 JccSnapshot → building generation → 规范化文档/hash → 复用兼容向量或批量生成 → 完整性检查 → 同事务切换 current pointer
```

### 2.2 文档、Embedding 和检索

- `app/rag/document_builder.py`：对英雄、羁绊、装备、强化符文、奇遇、银河生成确定性实体文档；内容 NFC/空白规范化，数据库主键不进入文档，hash 包含 schema version。
- `app/rag/embedding.py`：provider-neutral 接口、确定性 fake provider 和数量/维度/有限数值校验。
- `app/rag/indexer.py`：按 document/hash/model/dimension 复用旧向量，失败回滚并保留旧 current pointer。
- `app/rag/retriever.py`：按 active index、mode、固定 snapshot 过滤结果并做有界确定性词项排序；真实 PostgreSQL FTS/vector 查询仍待专项补齐。
- `scripts/index_rag.py`、`pyproject.toml`：提供显式 fake provider 的离线索引命令 `rag-index`；不在聊天请求生成 embedding。

### 2.3 Agent、配置和测试

- `app/agent/tools/retrieval.py`、`app/agent/tools/schemas.py`、`app/agent/tools/registry.py`：加入严格有界 `search_knowledge`，不接受模型传入 snapshot 覆盖；返回 `rag_document` 来源，复用既有 orchestrator 审计/source SSE 路径。
- `app/agent/models.py`：允许 `rag_document` 来源类型。
- `app/core/config.py`、`.env.test.example`、`.env.product.example`：加入 RAG 开关、embedding 模型/维度、批量和检索上限配置；未写入 Secret。
- `tests/test_rag_core.py`：覆盖 fake embedding 稳定性、向量校验和规范化 hash；更新 `tests/test_agent_tools.py` 的阶段四白名单断言。

## 3. 关键设计结果

1. 检索使用 `RagCurrentIndex` 指向完整 generation，避免通过半成品文档状态直接发布；
2. embedding 只有 hash、模型和维度同时兼容才复用；provider 失败不会改变旧 pointer；
3. `search_knowledge` 只能从固定 `ToolContext.snapshot` 检索，引用保存有界 excerpt、document id、rank/score 和版本；
4. 精确数值/关系问题仍应调用阶段三结构化工具，不把 RAG 文本当作官方精确事实。

## 4. 与阶段计划的差异

| 差异 | 计划内容 | 实际实施 | 原因 | 影响与处理 |
| --- | --- | --- | --- | --- |
| PostgreSQL 混合 SQL | 阶段四计划要求 FTS/vector 混合检索 | 当前先落地 snapshot 过滤和确定性检索骨架，未完成真实 FTS/vector 查询 | 当前环境未执行隔离 pgvector 专项，避免伪造验收 | AC-4-01/部分工具链可验证；AC-4-04、PostgreSQL 专项待补，不标记完成 |
| 生产 embedding | 计划保留 provider 边界 | 实际仅提供 fake provider 和显式 fake CLI | 未配置/未授权真实 embedding 服务 | 生产索引前必须实现并配置真实 provider |

## 5. 测试与验证结果

| 检查 | 命令或方法 | 结果 | 证据/说明 |
| --- | --- | --- | --- |
| RAG/Agent 定向测试 | `./.venv/bin/pytest tests/test_rag_core.py tests/test_agent_tools.py tests/test_agent_tool_orchestrator.py -q` | 通过 | 8 passed |
| 全量测试 | `LLM_MODEL= LLM_BASE_URL= LLM_API_KEY= ./.venv/bin/pytest -q` | 通过 | 149 passed，2 条既有第三方 deprecation warnings |
| 定向 Ruff | `./.venv/bin/ruff check` 修改/新增 Python 文件 | 通过 | All checks passed |
| 编译 | `./.venv/bin/python -m compileall -q app alembic/versions/0006_rag_documents_and_indexes.py scripts/index_rag.py` | 通过 | 无编译错误 |
| 锁文件 | `pdm lock --check` | 通过 | 无输出、退出成功 |
| Diff 检查 | `git diff --check` | 通过 | 无 whitespace 错误 |
| Alembic heads | `./.venv/bin/alembic heads` | 通过 | 唯一 head `0006_rag_documents_and_indexes` |
| Alembic check | `./.venv/bin/alembic check` | 未完成 | 当前长期数据库未升级到 0006，工具报告 `Target database is not up to date`；未执行共享库迁移 |
| 隔离 pgvector migration/SQL | 临时 PostgreSQL | 未执行 | 当前阶段未启动/授权临时数据库；不能以 SQLite 替代 |

## 6. 阶段验收结果

| 编号 | 验收标准 | 结果 | 验证证据 |
| --- | --- | --- | --- |
| AC-4-01 | 当前版本过滤正确 | 部分通过 | `app/rag/retriever.py` 强制 active index/mode/snapshot 条件；真实 PostgreSQL 查询待补 |
| AC-4-02 | 未变化文档复用向量 | 部分通过 | `app/rag/indexer.py` 有 hash/model/dimension 兼容复用；完整 DB round-trip 待验证 |
| AC-4-03 | 索引失败不影响旧索引 | 部分通过 | indexer 异常回滚且不更新 current pointer；隔离数据库故障演练待验证 |
| AC-4-04 | 精确问题优先走结构化查询 | 部分通过 | tool description 明确 structured-first，保留原结构化工具；完整意图路由/真实混合 SQL 待补 |
| AC-4-05 | 引用来源能够追溯 | 通过代码契约 | `rag_document` SourceRecord 包含 document/version/rank/score/excerpt，并复用 orchestrator source persistence；数据库专项待验证 |

## 7. 安全、兼容性与可观测性核对

### 安全

- 工具使用静态白名单、Pydantic `extra=forbid`，不接受 snapshot/version 覆盖或任意 SQL；
- embedding 配置只从环境读取，测试与执行记录未写入真实 Secret；
- content、excerpt、工具输出均有边界；真实 provider 未调用。

### 兼容性

- 阶段二/三回归全量 149 tests 通过；
- 默认 `.env` 含真实 LLM 配置，未清空时隔离 API 测试会按既有设计返回 runtime unavailable（503）；验证命令显式清空 LLM 配置后通过；
- 0006 尚未应用到共享长期数据库，未宣称现有数据库已升级。

### 可观测性

- 新索引返回有限统计，失败保存有限 error code；
- RAG source 沿用既有 tool/source SSE 和审计路径；
- 尚未增加 metrics、成本统计或 readiness，这些属于阶段五。

## 8. 遗留问题与后续入口

| 问题 | 影响 | 条件 | 处理阶段 |
| --- | --- | --- | --- |
| 完成 PostgreSQL FTS/pgvector SQL 及 vector(1024) DDL round-trip | 真实 RAG 检索尚不能验收 | 临时 `pgvector/pgvector:pg16` 环境 | 本阶段补齐 |
| 实现并配置真实 embedding provider | fake provider 不能用于生产语义质量 | 外部 provider、凭证和授权 | 本阶段/发布前 |
| CI 服务仍可能使用无 pgvector 镜像 | CI 无法验证 vector migration | 更新 CI 或增加隔离 pgvector job | 本阶段发布前 |
| 共享开发库落后于 0006 | `alembic check` 当前失败 | 按备份和发布流程授权迁移 | 部署阶段 |

下一阶段可复用：RAG 工具/source 契约、generation pointer、固定 snapshot 过滤和离线命令。阶段五仍需遵守不在聊天路径生成 embedding、旧索引保留和来源脱敏约束。

## 9. 文档同步记录

- 总方案：已更新阶段四为“部分完成”，链接本阶段计划和执行记录，并记录 generation/fake provider/fixed snapshot 事实；
- 阶段四计划：已创建并标记“实施中”，列出范围、约束和验收标准；
- 本执行记录：记录实际代码、测试结果、未执行的 PostgreSQL/生产验证和遗留问题。

## 10. 阶段结论

第四阶段部分完成：核心模型、文档规范化、hash、离线索引骨架、RAG 工具、来源契约和回归测试已落地；149 项测试通过。由于隔离 PostgreSQL/pgvector migration、真实 FTS/vector 查询和真实 embedding provider 尚未验证，当前不能标记阶段完全完成，也不应执行生产迁移或生产索引。