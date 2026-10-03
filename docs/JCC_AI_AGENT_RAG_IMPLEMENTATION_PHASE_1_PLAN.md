# AI Agent / RAG 聊天功能：第一阶段“会话和消息持久化”实现计划

> 状态：部分完成
>
> 总实施方案：[jcc-ai-agent-rag-implementation-plan.md](jcc-ai-agent-rag-implementation-plan.md)
>
> 阶段执行记录：[JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_1_EXECUTION.md](JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_1_EXECUTION.md)
>
> 范围：实现用户隔离的会话/消息持久化、会话配置、分页、序号和幂等；不提前实现 LLM、SSE、Agent、RAG 或队列执行。

## 1. 背景与阶段基准

### 1.1 前置阶段状态

总方案中的阶段零已完成，JCC PostgreSQL 16 与 pgvector 基础设施和现有结构化资料已落地。本阶段复用其 PostgreSQL/Alembic 基础，不修改 `jcc_*` 结构化资料表。

### 1.2 当前仓库事实

- FastAPI 入口为 `app/main.py`，现有路由通过 `include_router` 注册。
- 数据库使用同步 SQLAlchemy `Base`、`SessionLocal` 和 `get_db`；Alembic 通过 `alembic/env.py` 显式导入模型。
- `app/deps/auth.py` 已验证 JWT、黑名单和会话撤销，并提供 `CurrentUser.user_id` 与 `require_scope`。
- 当前工作区在实施前干净；现有 JCC 结构化数据模型和 API 不应回归或改名。

### 1.3 本阶段目标

1. 创建 `agent_conversations`、`agent_messages` 表及可回滚的 Alembic migration。
2. 提供受 `jcc:agent:chat` 保护的会话 CRUD、归档、消息分页和消息提交 API。
3. 强制按 JWT `sub` 隔离数据，保存有效策略模式、会话内递增序号和幂等请求结果。
4. 补充隔离、权限、校验、分页、幂等、归档和迁移相关测试，并同步三类阶段文档。

## 2. 范围与约束

### 2.1 本阶段实现

- 会话状态 `active/archived`，默认策略 `gamble`；允许 `gamble`、`operation`。
- 消息角色数据库兼容 `user/assistant/tool/system`，但本阶段 HTTP 入口只能创建 `user` 消息；消息状态本阶段持久化为 `queued`，不启动执行。
- 会话列表/详情、标题和策略更新、归档、消息列表和消息提交。
- 消息长度上限 8,000 字符；最近消息分页由 `limit`/`offset` 控制。
- 会话内 sequence 唯一递增；`client_request_id` 在同一会话内幂等。
- 用户隔离、归档后可读但禁止修改和提交。

### 2.2 本阶段明确不实现

- LLM、OpenAI/Claude 适配器和任何真实模型调用；
- SSE、Agent runtime、`asyncio.Queue`、conversation lock、取消和超时；
- `agent_runs`、`agent_tool_calls`、来源记录及工具调用；
- RAG、Embedding、全文/向量检索；
- 多 worker、持久队列、真实生产迁移和部署。

### 2.3 已确认约束

- 所有会话和消息查询必须同时使用 `conversation_id` 与当前 JWT `user_id`；对其他用户资源返回 404，避免枚举。
- 会话默认 `strategy_mode` 为 `gamble`；消息可覆盖，消息行保存最终生效模式。
- 消息提交返回 HTTP 202，仅表示消息已持久化为 queued，不表示已开始 Agent 执行。
- 不复制 main 的用户或认证表，不建立跨数据库外键；`user_id` 作为外部字符串保存。
- 标题、消息正文和幂等键均执行长度及空白校验；客户端不能提交 assistant/tool/system 消息。

### 2.4 临时数据与隔离测试规则

普通测试使用 SQLite 内存库和测试 JWT，不连接长期开发或生产数据库。需要 PostgreSQL migration/约束验证时只使用本机随机一次性数据库，完成后可靠清理；不对共享数据库执行 downgrade 或清库。

### 2.5 前置依赖与环境条件

| 依赖 | 所需状态 | 当前状态 | 不满足时的处理 |
| --- | --- | --- | --- |
| Alembic/SQLAlchemy | 可创建并迁移新表 | 已存在 | 执行定向 migration 检查和 SQLite 模型测试 |
| JWT 验证与 Scope | `CurrentUser.user_id` 可用 | 已存在 | 复用 `require_scope('jcc:agent:chat')`，不新增认证逻辑 |
| PostgreSQL | 验证 advisory lock、唯一约束和 migration | 运行时确认 | 无隔离实例时如实记录，SQLite 仅作为单元测试替身 |

## 3. 详细设计与修改文件

### 3.1 会话和消息模型

新增：

- `app/conversations/models.py`：定义会话和消息 ORM、状态/策略 CheckConstraint、索引、唯一约束和时间字段；ID 使用应用生成的 UUID 字符串，`user_id` 不设跨库外键。
- `app/conversations/__init__.py`：领域包导出模型。
- `app/models/__init__.py`、`alembic/env.py`：显式导入新模型，确保 metadata 和 Alembic 可发现。

关键约束：

- `agent_conversations(user_id, updated_at, id)` 支持用户列表稳定分页；
- `agent_messages(conversation_id, sequence)` 唯一；
- `agent_messages(conversation_id, client_request_id)` 唯一，NULL 请求键可重复；
- 会话归档时间与 `archived` 状态一致；消息 `content` 非空且不超过 8,000 字符。

### 3.2 Repository、Service 和 Schema

新增：

- `app/conversations/repository.py`：按 owner 过滤查询和分页；在 PostgreSQL 事务中使用 conversation advisory transaction lock 后生成下一个 sequence；执行幂等查找和持久化。
- `app/conversations/service.py`：封装创建/更新/归档/提交消息规则，将资源不存在、归档写入、幂等冲突转换为明确领域异常。
- `app/conversations/schemas.py`：请求/响应 Pydantic 契约，`Literal` 限制模式、状态和角色，响应禁止泄露内部字段。

幂等规则：同一 owner、会话和 `client_request_id` 重试且正文/策略一致时返回原消息；正文或策略不同返回 409。没有幂等键时每次创建新消息。

### 3.3 API

新增：

- `app/api/agent.py`：统一前缀 `/api/agent`，所有端点依赖 `require_scope("jcc:agent:chat")`。
- `app/main.py`：注册 Agent router。

接口：

- `POST /api/agent/conversations` → 201；
- `GET /api/agent/conversations?limit=&offset=` → 200；
- `GET /api/agent/conversations/{conversation_id}` → 200；
- `PATCH /api/agent/conversations/{conversation_id}` → 200，支持 title、strategy_mode；状态通过专用归档接口改变；
- `GET /api/agent/conversations/{conversation_id}/messages?limit=&offset=` → 200；
- `POST /api/agent/conversations/{conversation_id}/messages` → 202，创建或幂等返回 user/queued 消息，`run_id` 暂为 null；
- `POST /api/agent/conversations/{conversation_id}/archive` → 200，重复调用幂等。

归档会话允许读取历史，但所有写操作返回 409。请求参数错误由 FastAPI/Pydantic 返回 422，未授权返回 401，缺 Scope 返回 403，资源不属于当前用户按 404 处理。

### 3.4 数据库迁移

新增 `alembic/versions/0003_agent_conversations.py`，从 `0002_jcc_structured_data` 创建两张表、约束和索引；downgrade 仅删除本阶段新表，按外键依赖先删消息再删会话，不修改既有 `jcc_*` 表。

### 3.5 安全与可观测性

- 不信任客户端 user_id，始终从 `CurrentUser.user_id` 获取；
- 不在日志中记录消息正文、JWT、请求键或数据库凭证；
- 统一通过现有 Request ID middleware 关联请求；
- 归档和幂等状态更新使用事务及条件校验，数据库唯一约束作为并发兜底。

## 4. 实施步骤

1. 新增阶段计划和执行记录入口，并在总方案增加阶段文档链接；
2. 新增会话/消息 ORM、migration 和 metadata 注册；
3. 新增 schemas、repository、service 和 API router；
4. 注册 router，补充阶段配置/README 中的接口边界说明（不添加 LLM 配置）；
5. 增加定向测试，覆盖权限、隔离、策略、分页、序号、幂等、归档和状态码；
6. 执行定向 Ruff、测试、Alembic 状态/迁移检查、锁文件和 diff 检查；
7. 根据真实结果更新总方案、阶段计划和执行记录。

## 5. 测试与验证计划

### 5.1 定向测试

| 测试范围 | 覆盖行为 | 预期结果 |
| --- | --- | --- |
| `tests/test_agent_conversations_api.py` | CRUD、Scope、用户隔离、策略校验、归档、消息提交/分页 | 状态码和响应字段符合契约 |
| `tests/test_agent_conversations_repository.py` | sequence、幂等重放/冲突、事务约束 | 不重复创建且 sequence 唯一 |
| `alembic` 临时数据库 | upgrade/downgrade 或 head 状态 | 只影响 0003 新表 |
| 现有测试目录 | 用户 JWT、JCC API、内部 API 回归 | 既有行为不变 |

### 5.2 回归与质量检查

```bash
pdm run pytest tests/test_agent_conversations_api.py tests/test_agent_conversations_repository.py -q
pdm run ruff check app/conversations app/api/agent.py app/main.py app/models/__init__.py alembic/env.py alembic/versions/0003_agent_conversations.py tests/test_agent_conversations_api.py tests/test_agent_conversations_repository.py
pdm run alembic-current
pdm lock --check
pdm run test
pdm run alembic check
 git diff --check
```

Ruff 只检查本阶段新增或修改的 Python 文件，不执行全仓 lint。

### 5.3 真实环境验证

隔离 PostgreSQL migration round-trip 可执行时进行；真实生产迁移、部署、LLM、SSE 和队列均不属于本阶段，不执行。

## 6. 验收标准与追踪

| 编号 | 验收标准 | 实现位置 | 验证方式 | 状态 |
| --- | --- | --- | --- | --- |
| AC-1-01 | 会话和消息表迁移可升级/回滚，不影响既有表 | migration/models | 临时 SQLite upgrade/downgrade/upgrade | 已满足（PostgreSQL 待验证） |
| AC-1-02 | 用户只能读取和修改自己的会话/消息 | service/api/auth | API 隔离测试 | 已满足 |
| AC-1-03 | gamble/operation 校验，消息模式覆盖会话默认并持久化 | schemas/service/models | API 测试 | 已满足 |
| AC-1-04 | 会话 CRUD 与归档后只读行为正确 | api/service | API 测试 | 已满足 |
| AC-1-05 | 消息长度、user role、queued 状态和分页契约正确 | schemas/api | API 测试 | 已满足 |
| AC-1-06 | sequence 在会话内唯一递增 | repository/model | API 测试 | 已满足（PostgreSQL 并发待验证） |
| AC-1-07 | client_request_id 重放幂等，payload 冲突返回 409 | repository/service/api | API 测试 | 已满足 |
| AC-1-08 | 不调用 LLM、SSE、Agent、RAG 或队列 | 阶段范围 | 代码审查/回归测试 | 已满足 |

## 7. 风险、回滚与异常处理

| 风险或失败场景 | 影响 | 预防/检测 | 回滚或恢复 |
| --- | --- | --- | --- |
| sequence 并发生成冲突 | 消息提交失败或重复 | PostgreSQL advisory lock、唯一约束 | 捕获并重试/返回安全错误，后续阶段保留约束 |
| 幂等键 payload 不一致 | 客户端误将不同请求复用同键 | 比较正文和策略，返回 409 | 使用新 client_request_id |
| 归档与提交竞态 | 归档后产生新消息 | 同一会话事务锁和 active 条件检查 | 失败请求不写入 |
| migration 失败 | 应用无法使用新功能 | 隔离库验证、expand-only | 保留旧 revision，修复后前向迁移 |

## 8. 阶段交付物

代码与配置：

- 会话/消息 ORM、repository/service/schema、Agent API router、0003 migration。

测试：

- 会话 API、repository、权限和回归测试。

文档：

- 更新总方案阶段状态及链接；
- 更新本阶段计划的最终状态和设计调整；
- 创建本阶段执行记录。

## 9. 计划调整记录

实施前暂无调整。若实现中发现影响公共契约、数据兼容或后续阶段边界的变化，必须同步本计划和总方案。
