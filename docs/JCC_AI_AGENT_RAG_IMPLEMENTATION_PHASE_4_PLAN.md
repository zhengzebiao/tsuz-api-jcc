# AI Agent / RAG 聊天功能：第四阶段“RAG”实现计划

> 状态：实施中
>
> 总实施方案：[jcc-ai-agent-rag-implementation-plan.md](jcc-ai-agent-rag-implementation-plan.md)
>
> 阶段执行记录：[JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_4_EXECUTION.md](JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_4_EXECUTION.md)
>
> 范围：在阶段三固定 snapshot 和来源持久化基础上实现结构化资料的规范化 RAG 文档、增量 embedding 索引、PostgreSQL 全文/向量混合检索和 `search_knowledge` 工具；不提前实现阶段五能力。

## 1. 阶段基准与目标

阶段三已完成固定 snapshot 的工具循环、工具审计和 `AgentMessageSource` 持久化；基础设施 compose 使用 `pgvector/pgvector:pg16`。当前仓库尚无 RAG 表、文档生成、Embedding、全文/向量检索。阶段四目标：

1. 从 `JccSnapshot` 的英雄、羁绊、装备、强化符文、奇遇和银河资料生成确定性实体文档并计算 SHA-256 `content_hash`；
2. 使用独立索引命令批量生成向量，复用相同内容/模型/维度的旧向量，失败时不切换旧索引；
3. 使用 PostgreSQL `tsvector + GIN`、pgvector 和固定 snapshot/version 过滤提供混合检索；
4. 通过白名单 `search_knowledge` 工具返回可追溯的 `rag_document` 来源。

## 2. 范围与非目标

### 实现

- `rag_documents`、索引 generation/current pointer 表及 0006 migration；
- 确定性规范化文档和内容哈希；
- provider-neutral Embedding 接口、确定性测试 provider 和独立索引命令；
- FTS、vector、混合去重/排序和版本过滤；其中 PostgreSQL `tsvector + ts_rank` 全文排名、pgvector cosine 相似度和 FTS/vector 混合排序仍是本阶段待补实现项，不能以当前关键词排序替代；
- `search_knowledge` 工具与阶段三来源/SSE 路径复用；
- 配置、环境示例、单元测试和隔离 PostgreSQL 契约验证。

### 不实现

- 聊天请求内实时生成 embedding；
- Milvus/Qdrant 等外部向量库；
- 任意 SQL、Shell、写入数据或切换 snapshot 的工具；
- Redis 队列、多 worker、限流、成本统计、长期记忆和生产迁移；
- 人工维护阵容文档。

## 3. 数据与失败契约

`rag_index_runs` 以 generation 作为原子发布边界，`rag_current_indexes` 保存每个 mode 的 active 指针；检索先解析 active generation，再强制过滤 `snapshot_id`/mode。`rag_documents` 保存规范化内容、hash、embedding、来源和版本元数据。新 generation 构建或 provider 失败时标记失败并保留旧 pointer；不完整 generation 不可被检索。

embedding 复用必须同时满足 content hash、模型和维度一致。RAG 来源使用 `source_type=rag_document`，excerpt、document_id、rank/score 和 snapshot/version 有界保存。精确数值问题仍由结构化工具优先处理，RAG 仅提供描述、术语和模糊意图资料。

## 4. 关键文件与步骤

1. 新增 `app/rag/models.py`、`app/rag/document_builder.py`、`app/rag/embedding.py`、`app/rag/indexer.py`、`app/rag/retriever.py`，并在 Alembic 注册；
2. 新增 `alembic/versions/0006_rag_documents_and_indexes.py`，扩展来源约束；
3. 新增 `app/agent/tools/retrieval.py`，扩展 `schemas.py` 与 `registry.py`；
4. 增加 RAG settings、`scripts/index_rag.py` 和 PDM 命令；
5. 增加规范化/hash/index/retrieval/tool 测试；PostgreSQL 专属 JSONB/TSVECTOR/GIN/vector 只在隔离 pgvector 环境验证。

## 5. 验收标准

| 编号 | 标准 | 验证 |
| --- | --- | --- |
| AC-4-01 | 检索仅返回 run 固定的当前 snapshot/version | RAG retriever/tool 测试 |
| AC-4-02 | 未变化文档复用兼容向量 | indexer + fake provider 测试 |
| AC-4-03 | 索引失败不替换旧 active generation | indexer transaction/failure 测试 |
| AC-4-04 | 精确问题保留结构化优先边界 | tool/prompt/retrieval 测试 |
| AC-4-05 | 引用包含 document、版本、排序/分数和有界 excerpt | source persistence/SSE 回归 |

## 6. 验证与环境边界

普通测试使用 Fake embedding，不访问外部服务。迁移、TSVECTOR/GIN、pgvector vector(1024) 和真实 SQL 混合检索需要一次性隔离 `pgvector/pgvector:pg16` 数据库；当前 CI 的 `postgres:16-alpine` 不具备 vector 扩展，不能将 SQLite 或普通 PostgreSQL 结果当作 pgvector 验收。共享长期数据库、真实 embedding provider 和生产部署不在本阶段执行。
