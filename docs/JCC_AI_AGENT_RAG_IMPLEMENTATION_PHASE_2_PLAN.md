# AI Agent / RAG 聊天功能：第二阶段“队列、LLM 和 SSE”实现计划

> 状态：已完成（多 worker/多容器及生产迁移、生产部署按范围不执行）
>
> 总实施方案：[jcc-ai-agent-rag-implementation-plan.md](jcc-ai-agent-rag-implementation-plan.md)
>
> 阶段执行记录：[JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_2_EXECUTION.md](JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_2_EXECUTION.md)
>
> 范围：在第一阶段会话/消息持久化基础上实现单进程 latest-wins Agent runtime、OpenAI-compatible 异步文本流和 SSE；不提前实现工具调用、阵容推导或 RAG。

## 1. 背景与阶段基准

### 1.1 前置阶段状态

阶段零的 PostgreSQL/pgvector 基础设施已在总方案中记录为完成。阶段一已实现：

- `agent_conversations`、`agent_messages` 模型和 `0003_agent_conversations` migration；
- JWT `sub` 用户隔离、`jcc:agent:chat` scope、会话 CRUD、消息分页、sequence、幂等和归档只读；
- 消息提交返回 202 并持久化为 `user/queued`，但尚未创建 run、启动后台执行或提供 SSE。

阶段一仍有隔离 PostgreSQL migration/并发验证遗留项；本阶段不对共享或生产数据库执行迁移，验证条件在执行记录中如实记录。

### 1.2 当前仓库事实

- FastAPI 入口为 `app/main.py`，数据库为同步 SQLAlchemy `SessionLocal`；
- 认证依赖为 `app/deps/auth.py`，资源查询必须继续按 JWT `sub` 过滤；
- JCC 结构化查询集中在 `app/jcc_data/repository.py`，阶段二只加载会话上下文，不调用阶段三工具；
- Docker 默认 Gunicorn 曾配置多 worker，阶段二必须改为 `WEB_CONCURRENCY=1`；Nginx 目前没有 SSE 专用缓冲配置；
- 总方案已确认使用通用 OpenAI-compatible LLMClient，通过配置注入模型和 endpoint；本阶段不引入 Anthropic SDK。

### 1.3 本阶段目标

1. 新增 `agent_runs` 持久化记录和消息/run 状态转换；
2. 建立 FastAPI lifespan 管理的单进程 `ConversationRuntimeManager`，实现有界队列、会话锁、latest-wins、取消、恢复和关闭；
3. 接入 provider-neutral 异步 LLM 接口及 OpenAI-compatible 流式文本适配器；
4. 提供带心跳和终态事件的 SSE，以及显式取消接口；
5. 用 Fake LLM 覆盖并发、异常、取消和 SSE，不调用真实模型。

## 2. 范围与约束

### 2.1 本阶段实现

- `agent_runs` 表、ORM、migration、repository/service 状态操作；
- 202 消息提交返回非空 `run_id`，幂等重放不重复创建 run；
- `asyncio.Queue(maxsize=1)`、conversation `asyncio.Lock`、不同会话并行；
- 新消息取消当前运行和旧排队消息，只执行最新消息；
- OpenAI-compatible 异步普通文本流、超时和 provider 错误转换；
- `message.queued`、`run.started`、`text.delta`、`message.completed`、`message.failed`、`message.cancelled` SSE 事件；
- 显式 cancel API，SSE 断开仅取消订阅；
- lifespan 启动恢复未终态任务、优雅关闭和单 worker 配置。

### 2.2 本阶段明确不实现

- 阶段三工具注册、tool calling、结构化 JCC 查询工具和阵容推导；
- 阶段四 `rag_documents`、Embedding、全文/向量检索和来源表；
- 多 worker、多容器、Redis/数据库持久队列和分布式锁；
- 多 worker/多容器验证、生产迁移和生产部署；真实 LLM 受控测试调用已在执行记录中完成；
- 上下文摘要、长期记忆、限流和成本优化（阶段五）。

### 2.3 已确认约束

- LLM 采用通用 OpenAI-compatible 适配层，provider/model/base URL/API key 通过环境配置注入；
- 运行时只在单个 Python 进程内提供串行保证，首期 `WEB_CONCURRENCY=1`；
- 单会话最多一条 running/streaming 和一条 queued；新消息采用 latest-wins；
- 取消保留已经生成的部分回答，消息/run 进入 cancelled；LLM 失败进入 failed；
- 同步 SQLAlchemy 短操作不得阻塞异步流，使用受控线程执行；
- API、SSE、cancel 均继续要求 `jcc:agent:chat` 和 owner-scoped 查询。

### 2.4 临时数据与隔离测试规则

普通测试使用 SQLite 内存库、Fake LLM 和临时 JWT，不连接共享数据库或真实 LLM。需要 PostgreSQL migration/条件更新验证时，只使用随机一次性隔离实例并可靠清理；不执行共享库 downgrade、清库或生产迁移。

### 2.5 前置依赖与环境条件

| 依赖 | 所需状态 | 当前状态 | 不满足时的处理 |
| --- | --- | --- | --- |
| 第一阶段表/migration | 可创建消息并保存 queued 状态 | 已存在；PostgreSQL 并发已验证 | 结果记录于阶段执行记录 |
| OpenAI-compatible endpoint | 测试可替换为 Fake，真实 endpoint 非必需 | 受控环境真实调用已验证 | 生产配置仍通过 Secret 注入 |
| 单 worker | `WEB_CONCURRENCY=1` | 测试示例已为 1，product 示例需调整 | 配置测试和启动约束，禁止把多 worker 标为支持 |
| PostgreSQL 0004 | 可在受控库 upgrade | 已执行，`agent_runs` 表存在 | 生产迁移仍按范围不执行 |

## 3. 详细设计与修改文件

### 3.1 Run 持久化和状态

新增 `app/agent/models.py` 或会话模型中的 `AgentRun`，字段包括 UUID、message/conversation 外键、状态、provider/model/request_id、cancel_requested、error_code、token 统计、时间和 duration。新增 Alembic revision（当前 head 为 0003 时使用 0004），注册到 `app/models/__init__.py` 和 `alembic/env.py`。

扩展 conversations repository/service：创建消息时同事务创建 run；幂等重放返回已有 run；提供按 owner 获取 run、条件状态更新、取消 queued/running/streaming、恢复未终态和创建 assistant 回复的方法。客户端仍只能创建 user 消息。

### 3.2 LLM 和编排

新增 `app/agent/llm/base.py`、`openai_compatible_client.py` 和 `app/agent/orchestrator.py`。base 定义异步文本事件和安全的内部异常；适配器解析 OpenAI-compatible `/chat/completions` SSE；orchestrator 使用最近上下文和 gamble/operation prompt，累积 delta，完成时保存 assistant 消息，异常/取消时保存安全状态。阶段二不执行工具调用。

### 3.3 Runtime

新增 `app/agent/runtime.py`、`events.py`、必要的 prompts/包初始化。管理每个会话的有界 queue、lock、consumer、execution task、cancel event 和订阅者。入队时在内存中取消旧 execution task，并在数据库中将旧 queued/running/streaming 条件转换为 cancelling/cancelled；consumer 取出后再次检查数据库状态。不同会话使用不同 runtime，可并行。

启动通过 lifespan 扫描 queued 和重启遗留的 running/streaming run，安全重置或重新排队；关闭时停止接收、取消内存任务并保留数据库可恢复状态。所有同步数据库操作通过线程执行。

### 3.4 API 和 SSE

修改 `app/api/agent.py`：

- 消息提交创建 run 后调用 lifespan runtime；无 runtime 的纯测试应用仍只验证持久化契约；
- `GET /api/agent/conversations/{conversation_id}/messages/{message_id}/events` 返回 `text/event-stream`，回放当前状态，推送 delta/终态和 heartbeat；
- `POST /api/agent/conversations/{conversation_id}/messages/{message_id}/cancel` 实现 owner-scoped、幂等取消。

设置 `Cache-Control: no-cache` 和 `X-Accel-Buffering: no`；客户端断开不取消执行。

### 3.5 配置和部署

修改 `app/core/config.py`、`.env.test.example`、`.env.product.example`、`Dockerfile`、必要的 compose 和 `nginx/default.conf`：增加 LLM/队列/超时/heartbeat/上下文配置，API key 只描述环境 Secret；默认单 worker；Nginx 关闭 SSE buffering/cache 并提高 read timeout。仅增加实现所需依赖并同步锁文件，不引入 Anthropic SDK。

## 4. 实施步骤

1. 新增阶段二模型、migration 和状态 repository/service，先保证 run_id、幂等和条件更新；
2. 定义事件协议、LLM 抽象、OpenAI-compatible adapter、Fake client；
3. 实现 orchestrator 和 runtime 的锁、队列、latest-wins、取消、恢复、shutdown；
4. 接入 lifespan、提交消息、SSE 和 cancel API；
5. 更新配置、单 worker 和 Nginx 流式配置；
6. 补充 Fake LLM 定向测试、migration/配置测试和全量回归；
7. 按真实结果更新总方案、本计划状态和阶段执行记录。

## 5. 测试与验证计划

- `tests/test_agent_runtime.py`：同会话串行、不同会话并行、latest-wins、队列满、恢复和 shutdown；
- `tests/test_agent_llm.py`：Fake 流、adapter SSE 解析、超时、限流/认证/provider 错误、取消；
- `tests/test_agent_sse.py`：delta、heartbeat、终态回放、headers、断开不取消、owner 隔离和 cancel 幂等；
- `tests/test_agent_conversations.py`：run_id、幂等 run、归档拒绝和状态响应回归；
- 临时 migration：upgrade/downgrade/upgrade 及 PostgreSQL 专用约束在有隔离环境时执行；
- 真实模型调用、生产数据库、部署和多 worker 验证只记录为待执行，不作为 CI 通过证据。

建议检查：

```bash
./.venv/bin/pytest tests/test_agent_runtime.py tests/test_agent_llm.py tests/test_agent_sse.py tests/test_agent_conversations.py -q
./.venv/bin/ruff check <本阶段新增或修改的 Python 文件>
./.venv/bin/alembic heads
./.venv/bin/alembic check
./.venv/bin/python -m compileall -q <本阶段新增或修改的 Python 文件>
pdm lock --check
./.venv/bin/pytest -q
git diff --check
```

## 6. 验收标准与追踪

| 编号 | 验收标准 | 实现位置 | 验证方式 | 状态 |
| --- | --- | --- | --- | --- |
| AC-2-01 | 同一会话最多一条运行，不同会话可并行 | runtime | `tests/test_agent_runtime.py`；已验证单会话执行与取消，跨会话并行未单独覆盖 | 部分满足 |
| AC-2-02 | 新消息取消当前和旧 queued，仅最新执行 | runtime/service | runtime 条件状态和 superseded 事件实现；完整替换竞态未单独覆盖 | 部分满足 |
| AC-2-03 | 202 返回非空 run_id，幂等不重复 run | conversations/api | 阶段一回归通过；run persistence 定向单测尚未覆盖 API 幂等 | 部分满足 |
| AC-2-04 | 文本 delta、heartbeat 和终态可经 SSE 发送 | events/api | 已完成真实请求的提交→订阅→接收终态基本链路；heartbeat 专项未验证 | 基本满足 |
| AC-2-05 | 超时/provider 错误/取消和部分回答安全落库 | orchestrator/models | `tests/test_agent_llm.py` 覆盖 Fake/adapter；超时和完整日志断言待补 | 部分满足 |
| AC-2-06 | 显式 cancel 幂等，SSE 断开不取消执行 | runtime/api | runtime cancel 定向测试通过；API/SSE 断开测试待补 | 部分满足 |
| AC-2-07 | 启动恢复未终态 queued/running 状态 | lifespan/runtime | recovery 实现；受控数据库迁移和 `agent_runs` 表已确认 | 满足 |
| AC-2-08 | 单 worker、SSE 代理和 migration 检查符合约束 | config/Docker/Nginx/migration | 单 worker 约束、SSE 基本链路和受控 PostgreSQL migration 已验证；生产迁移部署按范围不执行 | 满足 |
| AC-2-09 | 不提前实现工具、RAG、多 worker/持久队列 | 阶段文件范围 | 代码范围审查；无工具/RAG/持久队列实现 | 通过 |

## 7. 风险、回滚与异常处理

| 风险或失败场景 | 影响 | 预防/检测 | 回滚或恢复 |
| --- | --- | --- | --- |
| 进程内队列无法跨 worker | 同会话可能并行 | 强制单 worker并记录限制 | 未来迁移 Redis/数据库队列和分布式锁 |
| 重启导致重复/丢失执行 | 回答状态不一致 | DB 条件状态、启动扫描、终态保护 | 标记失败或重新排队，不覆盖终态 |
| 同步 SQL 阻塞事件循环 | 其他会话 SSE 延迟 | `asyncio.to_thread` 和并发测试 | 降低同步操作范围，后续再引入 AsyncSession |
| SSE/LLM 流异常 | 客户端收不到终态 | heartbeat、异常终态事件和超时 | 保留 DB 状态，客户端可重新订阅 |
| migration/依赖失败 | 发布失败 | 隔离库验证和锁检查 | 保留旧 revision，使用前向修复，不对共享库 downgrade |

## 8. 阶段交付物

- `agent_runs` 模型、migration、运行时、LLM adapter、orchestrator、SSE/cancel API；
- Fake LLM 与 runtime/LLM/SSE/回归测试；
- 配置、单 worker 和 Nginx SSE 设置；
- 更新总方案阶段状态、本文档状态及第二阶段执行记录。

## 9. 计划调整记录

实施调整记录：受控环境已完成 PostgreSQL migration、`agent_runs` 表、advisory lock/并发、真实 LLM 和 SSE 基本端到端验证；多 worker/多容器验证及生产迁移、生产部署按单 worker 和环境边界不执行。用户已确认继续采用总方案的 OpenAI-compatible LLM 方向。
