# AI Agent / RAG 聊天功能：第二阶段“队列、LLM 和 SSE”执行记录

> 状态：部分完成
>
> 执行日期：2026-10-04
>
> 总实施方案：[jcc-ai-agent-rag-implementation-plan.md](jcc-ai-agent-rag-implementation-plan.md)
>
> 阶段实现计划：[JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_2_PLAN.md](JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_2_PLAN.md)

## 1. 执行范围与结论

本次根据总方案完成第二阶段“队列、LLM 和 SSE”的代码实现和自动化验证。

阶段结论：单进程 Agent runtime、运行记录、OpenAI-compatible 文本流抽象、latest-wins 取消、SSE/cancel API、lifespan 恢复入口和单 worker/SSE 代理配置已落地；Fake LLM、runtime、adapter、阶段一回归和全量测试通过。由于当前未授权连接长期/生产 PostgreSQL，未执行 PostgreSQL 0003/0004 migration、并发锁验证、真实 LLM 调用或生产部署，因此阶段标记为“部分完成”，不能据此进入已完成状态。

本阶段实际完成：

1. 新增 `agent_runs` ORM、0004 Alembic migration、状态条件更新和 output_content 部分回答保存；
2. 新增 `ConversationRuntimeManager`，使用单进程 `asyncio.Queue(maxsize=1)`、conversation runtime 锁、execution task、cancel event、latest-wins 和启动恢复；
3. 新增 provider-neutral LLM 接口和 OpenAI-compatible SSE stream adapter，统一配置错误、认证、限流、超时和 provider 错误；
4. 新增文本 orchestrator，使用最近上下文和 gamble/operation prompt，保留用户原始消息并单独写入 assistant 消息；
5. 扩展 202 消息响应返回 `run_id`，新增 SSE events 和显式 cancel API；
6. 新增 LLM/runtime 测试，并完成全量回归、编译、Ruff、diff 和临时 SQLite migration 往返验证；
7. 修改单 worker 默认值、LLM 配置样例和 Nginx 流式代理参数。

本阶段明确未实现或未执行：

- 未实现阶段三工具调用、工具记录、结构化查询工具、阵容推导和来源记录；
- 未实现阶段四 RAG、Embedding、全文/向量检索和来源表；
- 未实现多 worker、多容器、Redis/数据库持久队列和分布式锁；
- 未调用真实 OpenAI-compatible endpoint，未写入真实 API key，未执行生产迁移或部署；
- 未执行隔离 PostgreSQL 0003/0004 migration、PostgreSQL advisory lock/并发验证；
- SSE 端到端 TestClient 流式断开、heartbeat 和跨用户测试尚未补齐。

## 2. 实际代码与配置变更

### 2.1 Run 持久化和状态

- [app/agent/models.py](../app/agent/models.py)：新增 `AgentRun`，保存 message/conversation 关联、provider/model、状态、取消标志、错误、output_content、时间和耗时；
- [alembic/versions/0004_agent_runs.py](../alembic/versions/0004_agent_runs.py)：新增 `agent_runs` 表、外键、状态约束、唯一 message 约束和查询索引；
- [app/conversations/repository.py](../app/conversations/repository.py)：新增 run/message 查询、创建、条件状态更新、恢复扫描、最近消息查询和 assistant 消息创建；
- [app/conversations/service.py](../app/conversations/service.py)：新增 run 幂等创建、owner-scoped message/run 查询和 cancel 状态处理；
- [app/conversations/schemas.py](../app/conversations/schemas.py)：`MessageResponse.run_id` 从固定 null 扩展为可选字符串。

关键状态链路：

```text
user message queued + agent run queued
  → run/message running
  → run output_content 增量保存
  → message streaming（用户正文不覆盖）
  → run completed/failed/cancelled
  → assistant message completed（成功时）
```

取消或 supersede 使用条件更新，已完成状态不会被覆盖；旧排队消息记录 `superseded`，并发布 cancelled 事件。

### 2.2 LLM、Orchestrator 和 runtime

- [app/agent/llm/base.py](../app/agent/llm/base.py)：定义 `LLMClient`、`TextDelta` 和安全内部异常；
- [app/agent/llm/openai_compatible_client.py](../app/agent/llm/openai_compatible_client.py)：通过 `httpx.AsyncClient` 解析 `/chat/completions` SSE，只返回文本 delta，不记录 Authorization 或上游正文；
- [app/agent/prompts.py](../app/agent/prompts.py)：固定安全基础提示和两种 strategy mode 语义；
- [app/agent/orchestrator.py](../app/agent/orchestrator.py)：读取最近消息、执行文本流、保存增量、处理 timeout/provider error/cancel，并创建 assistant 回复；同步 SQLAlchemy 操作均通过 `asyncio.to_thread` 使用独立 session；
- [app/agent/runtime.py](../app/agent/runtime.py)：管理每会话 queue/lock/current execution/subscriber，入队替换旧 queued、取消匹配 execution、检查 DB queued→running 条件，启动扫描 queued/running/cancelling，停止时回收任务；
- [app/agent/events.py](../app/agent/events.py)：统一事件 JSON/SSE 序列化和 heartbeat；
- [app/agent/factory.py](../app/agent/factory.py)：按配置创建 adapter/runtime。

### 2.3 API、lifespan 和 SSE

- [app/api/agent.py](../app/api/agent.py)：消息提交创建 run 并返回非空 run_id；新增 `/events` SSE 和 `/cancel` API；保留 JWT scope、owner 过滤、归档检查和幂等语义；
- [app/main.py](../app/main.py)：增加 FastAPI lifespan，配置完整 LLM 时启动/恢复 runtime，退出时停止；未配置 LLM 的本地测试不连接新表；
- SSE 响应设置 `text/event-stream`、`Cache-Control: no-cache`、`X-Accel-Buffering: no`，断开只移除订阅；已终态请求立即回放终态并结束。

### 2.4 配置、依赖和部署

- [app/core/config.py](../app/core/config.py)：增加 provider/model/base URL/API key、LLM timeout/max tokens、队列、执行/关闭 timeout、heartbeat、上下文数量；
- [.env.test.example](../.env.test.example)、[.env.product.example](../.env.product.example)：增加非敏感配置样例和 Secret 注入占位；product/test 均为 `WEB_CONCURRENCY=1`；
- [Dockerfile](../Dockerfile)：Gunicorn 默认 worker 改为 1；
- [nginx/default.conf](../nginx/default.conf)：关闭 proxy buffering/cache，设置 300 秒读写超时；
- 未新增 Python 依赖或修改锁文件，复用现有 `httpx`。

## 3. 关键设计结果

1. 单进程 runtime 只承诺单会话串行；多 worker/多容器不在本阶段支持，必须保持 `WEB_CONCURRENCY=1`。
2. AgentRun 与用户消息一一对应，幂等请求复用已有 run；用户消息内容永远保留原始提问，模型部分回答保存于 `agent_runs.output_content`，成功回答另建 assistant 消息。
3. execution 以数据库条件状态为最后闸门，只有 queued→running 成功才调用 LLM；SSE 断开不取消执行，显式 cancel 才触发取消。
4. 新消息采用 latest-wins：取消当前匹配 execution、将旧 queued run/message 标记 cancelled，并清空内存队列后执行最新消息；不同会话拥有独立 runtime，可并行。
5. 运行时只有在 LLM 配置完整时由 lifespan 启动；生产/测试环境必须先执行 0004 migration，否则配置完整的 runtime 启动会因缺表失败。这一兼容限制必须在发布前处理，不能靠跳过迁移上线。
6. 本阶段只提供文本流，工具调用和 RAG 明确留给后续阶段。

## 4. 与阶段计划的差异

| 差异 | 计划内容 | 实际实施 | 原因 | 影响与处理 |
| --- | --- | --- | --- | --- |
| LLM SDK 依赖 | 计划允许新增 OpenAI-compatible 依赖 | 使用现有 `httpx` 实现轻量 SSE adapter，未新增 SDK | 当前依赖已有 httpx，减少锁文件和 provider SDK 耦合 | 保持通用接口；后续若协议复杂可替换 adapter |
| 用户消息输出 | 初始草案可能将增量写回 message | 实际写入 `AgentRun.output_content`，用户消息正文保持不变 | 避免破坏历史审计和上下文语义 | 增加 0004 字段；assistant 成功消息单独保存 |
| 测试 async plugin | 计划示例使用异步测试 | 仓库未安装 pytest-asyncio，测试使用 `asyncio.run` 包装 | 不新增仅为测试的依赖 | 行为覆盖不变，遵循现有工具链 |
| lifespan 无配置行为 | 计划强调启动恢复 | 未配置 LLM 时跳过 runtime 启动，避免旧 0002 本地库启动即失败 | 当前长期开发库尚未受控升级 0004 | 发布前必须迁移 0004；配置完整时仍执行恢复 |
| Nginx location | 初始计划增加 Agent 专用 location | 将 SSE buffering/cache/timeout 放到既有 `/` location | 保持现有部署配置测试的 upstream alias 断言 | 所有代理请求均关闭 buffering，行为更宽但兼容现有配置 |
| 阶段状态 | 计划目标为实现阶段二 | 记录为部分完成 | PostgreSQL、真实 LLM、SSE E2E 和生产部署未执行 | 发布前补环境验证后再更新状态 |

## 5. 测试与验证结果

### 5.1 验证汇总

| 检查 | 命令或方法 | 结果 | 证据/说明 |
| --- | --- | --- | --- |
| LLM/runtime 定向测试 | `./.venv/bin/pytest tests/test_agent_llm.py tests/test_agent_runtime.py -q` | 通过 | 6 passed |
| 阶段一 Agent API 回归 | `./.venv/bin/pytest tests/test_agent_conversations.py -q` | 通过 | 10 passed |
| 全量测试 | `./.venv/bin/pytest -q` | 通过 | 138 passed，2 条第三方 deprecation warnings |
| 定向 Ruff | `./.venv/bin/ruff check app/agent app/api/agent.py app/conversations app/core/config.py app/main.py app/models/__init__.py alembic/env.py alembic/versions/0004_agent_runs.py tests/test_agent_llm.py tests/test_agent_runtime.py` | 通过 | All checks passed |
| Python 编译 | `./.venv/bin/python -m compileall -q app alembic/versions/0004_agent_runs.py tests/test_agent_llm.py tests/test_agent_runtime.py` | 通过 | 无输出 |
| Diff 检查 | `git diff --check` | 通过 | 无空白错误 |
| Migration round-trip | 临时 SQLite `upgrade head → downgrade 0003_agent_conversations → upgrade head` | 通过 | 0001 至 0004 创建、0004 删除和重建成功，临时文件已清理 |
| Alembic heads | `./.venv/bin/alembic heads` | 通过/信息 | head 为 `0004_agent_runs` |
| Alembic check | `./.venv/bin/alembic check` | 未通过/环境限制 | 当前长期数据库仍停在 0002，Alembic 报 `Target database is not up to date`；未擅自升级共享库 |
| PDM lock | `pdm lock --check` | 未执行 | 未新增或升级依赖；本次环境未把该命令结果作为已验证证据 |

### 5.2 失败与未执行项

- 首次全量测试因全局 lifespan 连接仍为 0002 的长期数据库、读取不存在 `agent_runs` 表而失败；已改为未配置 LLM 时不启动 runtime，最终全量测试 138 passed。配置完整的发布环境仍必须先执行 0004 migration。
- `alembic check` 在长期开发数据库未升级到 0004 时失败，原因是目标数据库落后，不是迁移脚本 drift；未对长期数据库执行 upgrade/downgrade。
- 没有执行真实 OpenAI-compatible endpoint、LLM token 统计、生产迁移、生产部署或多 worker 验证。
- 没有执行隔离 PostgreSQL migration、advisory lock、并发 sequence/latest-wins 的 PostgreSQL 专用验证；SQLite round-trip 不能替代这些证据。
- SSE 端到端 TestClient 流式读取、heartbeat、断开不取消和跨用户 API 测试尚未补齐，因此对应验收项不能标记为完整通过。

### 5.3 真实环境或人工验证

| 验证项 | 环境 | 副作用/授权 | 结果 |
| --- | --- | --- | --- |
| LLM stream | Fake LLM/httpx MockTransport | 无外部副作用 | 通过 |
| Agent runtime | SQLite 内存库 + asyncio.run | 临时数据，测试结束销毁 | 通过 |
| Migration round-trip | 临时 SQLite 文件 | 创建/删除临时文件，不触及长期库 | 通过 |
| PostgreSQL 0003/0004 migration 与并发 | 长期开发/生产 PostgreSQL | 未授权执行 | 未执行 |
| OpenAI-compatible 真实调用 | 外部 endpoint | 未配置/未授权 | 未执行 |
| 生产部署、Nginx 实际流式传输 | product 环境 | 未授权执行 | 未执行 |

## 6. 阶段验收结果

| 编号 | 验收标准 | 结果 | 验证证据 |
| --- | --- | --- | --- |
| AC-2-01 | 同一会话同一时刻最多执行一条，不同会话可并行 | 部分通过 | runtime 有界队列、execution 状态闸门和单会话测试通过；不同会话并行专项测试待补 |
| AC-2-02 | 新消息取消当前和旧 queued，仅最新消息执行 | 部分通过 | runtime latest-wins 条件更新、superseded 事件和取消逻辑已实现；完整竞态专项测试待补 |
| AC-2-03 | 202 返回非空 run_id，幂等重放不重复 run | 部分通过 | API 返回和 `AgentRun` 唯一 message 约束已实现；阶段一回归通过，run 幂等专测待补 |
| AC-2-04 | SSE 收到增量文本、heartbeat 和终态 | 部分通过 | SSE 事件序列化、headers、终态回放和 heartbeat 代码已实现；端到端流式测试待补 |
| AC-2-05 | LLM 失败、超时、取消和部分输出正确标记 | 部分通过 | provider/认证/配置/adapter Fake 测试通过；完整 orchestrator timeout/日志脱敏测试待补 |
| AC-2-06 | 显式 cancel 幂等，SSE 断开不取消 Agent | 部分通过 | runtime cancel 定向测试通过；SSE 断开和 API 幂等测试待补 |
| AC-2-07 | 重启扫描并处理未取消 queued 消息 | 待环境验证 | `lifespan → runtime.start → list_recoverable_runs` 已实现；未在隔离 PostgreSQL/真实 lifespan 环境执行 |
| AC-2-08 | 单 worker、SSE 代理和迁移符合约束 | 部分通过 | 138 全量测试、配置检查、Ruff、compile、diff、SQLite migration round-trip 通过；PostgreSQL migration 待验证 |
| AC-2-09 | 未提前实现工具、RAG、多 worker/持久队列 | 通过 | 阶段代码范围审查；无 tool registry、RAG 表、Embedding、Redis/持久队列 |

## 7. 安全、兼容性与可观测性核对

### 安全

- API、SSE、cancel 复用 `require_scope("jcc:agent:chat")`；消息/run 查询继续校验 JWT `sub` 对应 user-owned conversation；
- OpenAI-compatible API key 只从设置读取并用于 Authorization header，不写入 run、metadata、SSE 或日志；
- LLM 错误对外只发固定 error code，不转发上游响应正文；
- 客户端仍不能提交 assistant/tool/system role、run_id、状态、sequence 或 user_id；
- prompt 明确把检索/用户文本当资料而非指令，并禁止 SQL/代码执行；阶段二尚无工具执行面；
- output_content 和消息正文仍受 8,000 字符上限约束；
- 未增加限流，属于阶段五范围，当前部署需由外部网关或后续阶段补充。

### 兼容性

- 阶段一消息 API、JWT scope、用户隔离、归档和幂等回归测试不变；
- `MessageResponse.run_id` 由固定 null 变为可选非空，属于阶段二预期兼容扩展；
- 0004 为新增表/索引，不修改既有 JCC 结构化资料表；但配置完整的 runtime 依赖 0004，发布必须先迁移；
- SQLite migration 往返通过，PostgreSQL DDL、外键、条件更新和 advisory lock 仍未验证；
- 单 worker 是强约束，Docker 默认和 product/test 示例已同步为 1；
- Nginx 既有 `/` upstream 关闭 buffering/cache 并延长读写超时，保留现有 network alias。

### 可观测性

- 复用 Request ID middleware；run 保存 provider/model/error/duration/status，便于后续指标接入；
- SSE 事件包含 message_id/run_id，错误事件仅包含固定 error_code；
- 当前未新增 metrics、token usage 采集、告警或结构化 Agent 日志，属于后续质量阶段；
- 测试警告仅为现有 FastAPI/Starlette 与 httpx deprecation warnings，未因本次代码新增失败。

## 8. 遗留问题与后续阶段入口

### 8.1 当前阶段遗留问题

| 问题 | 影响 | 负责人/条件 | 处理阶段 |
| --- | --- | --- | --- |
| 长期开发数据库仍为 0002 | 配置完整 runtime 启动会因 `agent_runs` 缺表失败 | 发布前受控执行 `alembic upgrade head`，不得共享库 downgrade | 阶段二发布前 |
| PostgreSQL 0003/0004、并发和 advisory lock 未验证 | 不能证明真实数据库状态转换、外键和并发 latest-wins 语义 | 一次性隔离 PostgreSQL 16/pgvector 环境 | 阶段二补充验证 |
| SSE E2E、heartbeat、断开和跨用户测试缺失 | SSE 外部契约尚无完整自动化证据 | 增加 TestClient/ASGI 流测试 | 阶段二补充验证 |
| 不同会话并行和完整替换竞态专项测试缺失 | 并行/取消证明不完整 | Fake LLM 多会话并发测试 | 阶段二补充验证 |
| 真实 LLM endpoint/部署未验证 | provider 协议、Secret、Nginx 实际传输未知 | 受控测试环境和真实凭证 | 发布前 |
| 无限流、token 成本统计和 metrics | 生产滥用/成本可见性不足 | 后续质量和运行完善阶段 | 阶段五 |

### 8.2 下一阶段可复用能力

- 阶段三可复用 `AgentRun`、事件 sink、orchestrator 的流式生命周期和 runtime cancel 语义；
- 阶段三工具调用必须在独立白名单/输入 schema 中实现，不得让 LLM 直接执行 SQL；
- 阶段三需将 tool calls 作为独立持久化记录，不把工具中间结果塞入 `AgentRun.output_content`；
- 阶段四可复用 SSE `source` 事件扩展点，但当前阶段没有 source 表或 RAG 查询；
- 后续多 worker/多容器不能直接复用当前 asyncio queue/lock，必须迁移 Redis/数据库持久队列与分布式锁。

## 9. 文档同步记录

- [总方案](jcc-ai-agent-rag-implementation-plan.md)：需同步第二阶段状态为“部分完成”，增加本计划/执行记录链接，并记录 OpenAI-compatible 选择、单 worker 限制和未验证项；
- [第二阶段计划](JCC_AI_AGENT_RAG_IMPLEMENTATION_PHASE_2_PLAN.md)：已更新为“部分完成”，增加执行记录链接、实际验收状态和计划/实现差异；
- 本执行记录：记录实际修改、验证结果、未执行项、遗留问题和下一阶段入口。

## 10. 阶段结论

第二阶段部分完成：

- run 持久化、单进程 latest-wins runtime、OpenAI-compatible 文本流、SSE/cancel API 和单 worker部署约束已实现；
- Fake LLM/runtime 定向测试 6 passed，阶段一回归 10 passed，全量测试 138 passed；Ruff、compile、diff 和临时 SQLite migration round-trip 通过；
- PostgreSQL migration/并发、真实 LLM、SSE E2E/断开、生产部署和多 worker 未执行，不能标记为全部验收通过；
- 在补齐受控 PostgreSQL 和 SSE E2E 验证、并按发布流程执行 0004 migration 后，才能将阶段状态更新为“已完成”并进入阶段三。
