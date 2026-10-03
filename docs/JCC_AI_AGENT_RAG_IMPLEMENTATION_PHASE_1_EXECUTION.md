# AI Agent / RAG 聊天功能：第一阶段“会话和消息持久化”执行记录

> 状态：部分完成
>
> 执行日期：2026-10-03
>
> 总实施方案：[jcc-ai-agent-rag-implementation-plan.md](jcc-ai-agent-rag-implementation-plan.md)
>
> 阶段实现计划：[JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_1_PLAN.md](JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_1_PLAN.md)

## 1. 执行范围与结论

本次根据总方案完成第一阶段“会话和消息持久化”的代码实现、Deploy 回归修复和自动化验证。

阶段结论：会话/消息模型、0003 数据库迁移、用户隔离的 CRUD API、策略模式、分页、sequence、幂等键和归档规则已落地；Deploy concurrency group 回归已修复；PDM 锁文件检查已由用户在本地控制台验证通过。PostgreSQL 专用 migration、advisory lock 和并发 sequence/幂等验证仍待在隔离测试 PostgreSQL 执行，因此阶段仍标记为“部分完成”。

本阶段实际完成：

1. 新增 `agent_conversations`、`agent_messages` ORM 模型及 Alembic revision `0003_agent_conversations`；
2. 新增 `/api/agent` 受 `jcc:agent:chat` 保护的会话创建、列表、详情、修改、归档和消息分页/提交接口；
3. 强制使用 JWT `sub` 作为 `user_id`，消息使用 `user/queued`、有效策略模式和会话内 sequence；
4. 实现同会话 `client_request_id` 幂等重放和冲突 409；归档后允许读取但禁止写入；
5. 修复 `.github/workflows/deploy.yml` 的 test/product 并发组命名；
6. 补充阶段计划、执行记录和 `tests/test_agent_conversations.py`。

本阶段明确未实现或未执行：

- 未实现 LLM、OpenAI/Claude 适配器、SSE、Agent runtime、asyncio 队列、conversation lock consumer、取消和超时；
- 未创建 `agent_runs`、`agent_tool_calls`、`agent_message_sources`、`rag_documents` 或 Embedding；
- 未执行生产迁移、部署、真实模型调用或队列执行；
- PostgreSQL 专用 migration、advisory lock、并发 sequence 和并发幂等验证待完成。

## 2. 实际代码与配置变更

### 2.1 会话和消息模型

- [app/conversations/models.py](../app/conversations/models.py)：新增 `AgentConversation`、`AgentMessage`，包含 UUID 字符串主键、外部 `user_id`、策略/状态约束、归档一致性约束、消息角色/状态约束、8,000 字符长度约束、sequence/request key 唯一约束和后续状态预留字段；
- [app/conversations/__init__.py](../app/conversations/__init__.py)：领域包导出模型；
- [app/models/__init__.py](../app/models/__init__.py)、[alembic/env.py](../alembic/env.py)：显式注册模型，确保 metadata/Alembic 可发现。

### 2.2 Repository、Service 和 API

- [app/conversations/repository.py](../app/conversations/repository.py)：实现 owner 过滤、稳定分页、PostgreSQL 会话 advisory transaction lock、sequence 分配和幂等查询；
- [app/conversations/service.py](../app/conversations/service.py)：实现 active/archived 写保护、策略继承、幂等重放/冲突、savepoint 异常边界和领域异常；
- [app/conversations/schemas.py](../app/conversations/schemas.py)：限制策略模式、消息长度、标题/幂等键和额外字段；响应使用标准 datetime；
- [app/api/agent.py](../app/api/agent.py)：新增 `/api/agent` 路由，所有端点通过 `require_scope("jcc:agent:chat")`，跨用户资源统一 404。

关键链路：

```text
JWT sub/user_id + jcc:agent:chat
  → owner-scoped conversation lookup
  → active conversation transaction lock
  → idempotency lookup
  → next sequence allocation
  → user/queued message persistence
  → 202 response
```

阶段一不会启动任何后台任务；`queued` 仅表示已持久化。

### 2.3 数据、迁移和状态

- [alembic/versions/0003_agent_conversations.py](../alembic/versions/0003_agent_conversations.py)：从 `0002_jcc_structured_data` 创建两张表、外键、索引、策略/状态/归档/长度/sequence 约束；downgrade 仅删除本阶段新表；
- 迁移未修改既有 `jcc_*`、`app_settings` 或 `sample_profiles` 表；
- 消息保留 `error_code`、`started_at`、`completed_at` 等后续阶段字段，但阶段一不产生运行状态转移。

### 2.4 API、Schema 或公共契约

新增：

- `POST /api/agent/conversations`（201）；
- `GET /api/agent/conversations`（分页）；
- `GET/PATCH /api/agent/conversations/{conversation_id}`；
- `POST /api/agent/conversations/{conversation_id}/archive`；
- `GET /api/agent/conversations/{conversation_id}/messages`（分页）；
- `POST /api/agent/conversations/{conversation_id}/messages`（202，返回 `queued`，`run_id=null`）。

未新增 SSE、cancel、run 或 tool API。

### 2.5 配置、依赖和外部服务

- 未新增配置和运行时依赖；
- 未修改 lock 文件；
- `pdm lock --check && echo "PDM lock check passed"` 已由用户本地控制台执行并通过；
- 未调用外部 LLM、CDN 或生产服务；
- 复用现有 JWT 验证、黑名单/session 检查和 Request ID middleware。

## 3. 关键设计结果

1. 所有资源查询都要求 `user_id`，跨用户统一按不存在处理；不复制 main users/session，不建立跨库外键。
2. 会话默认模式为 `gamble`，消息可覆盖且持久化实际生效模式；归档后只读，重复归档幂等。
3. 消息由服务端固定为 `role=user`、`status=queued`，客户端不能注入 role/status/sequence/user_id；阶段一不启动执行。
4. PostgreSQL 使用会话级 advisory transaction lock 串行化归档、修改和消息 sequence/幂等操作，唯一约束作为最终保护；消息插入冲突使用 savepoint 保持外层事务可用。
5. 迁移可在临时 SQLite 完成 upgrade/downgrade/upgrade 往返，未对长期开发数据库执行迁移。

## 4. 与阶段计划的差异

| 差异 | 计划内容 | 实际实施 | 原因 | 影响与处理 |
| --- | --- | --- | --- | --- |
| API 依赖声明 | 路由级 scope 依赖 | 每个 endpoint 显式复用 `require_scope("jcc:agent:chat")` | 便于 FastAPI 测试和明确依赖注入 | 不改变权限契约 |
| 消息幂等异常 | 唯一冲突后读取既有消息 | 使用 nested transaction/savepoint 后读取 | 避免回滚外层事务状态 | 不改变响应契约 |
| 阶段状态 | 计划中 | 部分完成 | PostgreSQL 专用 migration/并发验证仍待执行 | 保留待验证项及其影响，不提前标记完成 |

## 5. 测试与验证结果

### 5.1 验证汇总

| 检查 | 命令或方法 | 结果 | 证据/说明 |
| --- | --- | --- | --- |
| 定向测试 | `./.venv/bin/pytest tests/test_agent_conversations.py -q` | 通过 | 10 passed，2 条第三方 deprecation warnings |
| 定向 Ruff | `./.venv/bin/ruff check app/conversations app/api/agent.py app/main.py app/models/__init__.py alembic/env.py alembic/versions/0003_agent_conversations.py tests/test_agent_conversations.py` | 通过 | All checks passed |
| Python 编译 | `./.venv/bin/python -m compileall -q app alembic/versions/0003_agent_conversations.py tests/test_agent_conversations.py` | 通过 | 无输出 |
| Alembic head/current | `./.venv/bin/alembic heads && ./.venv/bin/alembic current` | 通过/信息 | head 为 `0003_agent_conversations`；长期开发库 current 仍为 `0002_jcc_structured_data`，未擅自迁移 |
| Migration round-trip | 临时 SQLite `upgrade head → downgrade 0002 → upgrade head` | 通过 | 迁移创建/删除/重建成功，临时文件已清理 |
| Deploy 回归 | `./.venv/bin/pytest tests/test_deployment_config.py -q` | 通过 | 5 passed；test/product concurrency group 格式已修复 |
| 锁文件 | `pdm lock --check && echo "PDM lock check passed"` | 通过 | 用户本地控制台输出 `PDM lock check passed` |
| 全量测试 | `./.venv/bin/pytest -q` | 通过 | 132 passed，2 条第三方 deprecation warnings |
| Diff 检查 | `git diff --check` | 通过 | 无空白错误 |

### 5.2 失败与未执行项

- Deploy concurrency group 已修复，定向部署配置测试和全量测试均通过；
- PDM 锁文件检查已由用户本地控制台验证通过；
- 未执行长期开发 PostgreSQL migration，避免改变共享/持久数据；已使用临时 SQLite 验证迁移往返。SQLite 不替代 PostgreSQL 并发和生产 DDL 验证。

### 5.3 真实环境或人工验证

| 验证项 | 环境 | 副作用/授权 | 结果 |
| --- | --- | --- | --- |
| API 会话/消息行为 | SQLite 内存库 + TestClient | 临时数据，测试结束销毁 | 通过 |
| migration round-trip | 临时 SQLite 文件 | 创建/删除临时文件，不触及长期库 | 通过 |
| PostgreSQL 迁移/并发 | 长期开发库/生产 | 未授权执行 | 未执行 |
| LLM/SSE/队列 | 不适用本阶段 | 无外部调用 | 未执行 |

## 6. 阶段验收结果

| 编号 | 验收标准 | 结果 | 验证证据 |
| --- | --- | --- | --- |
| AC-1-01 | conversation/message 表和迁移可用 | 通过（临时 SQLite） | `0003_agent_conversations.py` migration round-trip |
| AC-1-02 | 用户只能读取自己的会话/消息 | 通过 | `tests/test_agent_conversations.py` 用户隔离测试 |
| AC-1-03 | gamble/operation 校验及消息覆盖持久化 | 通过 | 定向 API 测试策略创建/继承/覆盖 |
| AC-1-04 | 会话 CRUD、归档后只读 | 通过 | CRUD/归档测试 |
| AC-1-05 | 消息限制、user/queued 和分页 | 通过 | 消息校验、分页和响应断言 |
| AC-1-06 | sequence 会话内唯一递增 | 通过（单进程验证） | 定向消息 sequence 测试；PostgreSQL 并发未执行 |
| AC-1-07 | client_request_id 幂等及冲突 409 | 通过 | replay/conflict 定向测试 |
| AC-1-08 | 不提前实现 LLM/SSE/Agent/RAG/队列 | 通过 | 阶段文件清单和 OpenAPI 路由范围审查 |

## 7. 安全、兼容性与可观测性核对

### 安全

- JWT 和 `jcc:agent:chat` scope 由现有认证依赖 fail closed 校验；
- `user_id` 从 JWT `sub` 获取，客户端无此字段；跨用户访问返回 404；
- Schema extra forbid，不能注入 role/status/sequence/metadata 等服务端字段；
- 未把 JWT、Secret 或消息全文写入新增日志；
- 消息长度和策略/状态/角色均有应用层及数据库约束。

### 兼容性

- 未修改既有 JCC 结构化表和已有 HTTP 路由；
- 新 migration 为 expand-only，downgrade 仅用于临时隔离验证；
- 长期数据库仍在 0002，应用代码新增路由需要发布前按标准迁移流程执行 0003；
- SQLite 已验证模型和 migration 基本契约，PostgreSQL 专用锁/并发仍待环境验证。

### 可观测性

- 复用 Request ID middleware；API 统一输出标准状态码和非敏感领域错误；
- 没有新增独立指标、后台任务或日志正文记录，符合阶段范围。

## 8. 遗留问题与后续阶段入口

### 8.1 当前阶段遗留问题

| 问题 | 影响 | 负责人/条件 | 处理阶段 |
| --- | --- | --- | --- |
| PostgreSQL migration/并发未执行 | 真实数据库 DDL/锁行为未验证；可能在阶段二并发运行或上线后暴露 sequence 冲突、幂等重复或迁移兼容问题 | 一次性隔离 PostgreSQL | 当前阶段补充验证/阶段二前 |
| 长期开发库尚未升级 0003 | 当前长期库不能使用新 Agent 表，直接访问新 API 可能因表不存在失败 | 按发布流程执行 `alembic upgrade head`，不得对共享库擅自 downgrade | 发布前 |

### 8.2 下一阶段可复用能力

- `AgentConversation`/`AgentMessage` 状态字段可供阶段二 runtime 使用；
- 阶段二必须继续使用 owner-scoped 查询和当前用户权限，不得绕过 API 直接信任 conversation_id；
- 阶段二可在 `queued` 消息上接入 `agent_runs`、队列和 SSE，但不能改变历史消息有效 strategy_mode；
- 在进入阶段二前应完成 0003 的受控 PostgreSQL migration 和全量回归修复。

## 9. 文档同步记录

- [总实施方案](jcc-ai-agent-rag-implementation-plan.md)：已记录阶段一计划/执行链接和“部分完成”状态；
- [第一阶段实现计划](JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_1_PLAN.md)：已创建并记录最终实现契约；
- 本执行记录：已记录代码、验证结果、失败和遗留项，状态为部分完成。

## 10. 阶段结论

第一阶段部分完成：

- 会话/消息持久化、API、策略、用户隔离、分页、sequence、幂等和归档规则已实现；
- 定向测试、Deploy 回归、阶段 lint、编译和临时迁移往返通过；
- PDM 锁文件检查已通过；
- PostgreSQL 专用 migration、advisory lock、并发 sequence/幂等验证待完成，主要影响是尚未证明生产数据库并发语义符合预期；
- 未执行生产或外部服务副作用；
- 完成隔离 PostgreSQL 验证并确认全量回归后，再将阶段状态更新为“已完成”并进入阶段二。
