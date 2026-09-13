# 应用间权限管理：第一阶段“最小闭环”实施计划

> 状态：部分完成（JCC 代码、默认自动化验证和配置文档已完成；隔离真实 smoke 待环境验证）
>
> 总实施方案：[main 应用间权限管理总方案](../../tsuz-api-main/plan/APP_TO_APP_PERMISSION_MANAGEMENT_IMPLEMENTATION_PLAN.md)
>
> 执行记录：[APP_TO_APP_PERMISSION_MANAGEMENT_IMPLEMENTATION_PHASE_1_EXECUTION.md](APP_TO_APP_PERMISSION_MANAGEMENT_IMPLEMENTATION_PHASE_1_EXECUTION.md)

## Context

[APP_TO_APP_PERMISSION_MANAGEMENT.md](/Users/zhengzebiao/code/tsuz-api-jcc/docs/APP_TO_APP_PERMISSION_MANAGEMENT.md) 已确定：`tsuz-api-main` 是统一 Service Token 签发中心，应用间授权采用“调用方应用 → 目标应用 → Scope”。本阶段不应另造一套应用注册机制，而应复用 main 已有的 `POST /admin/apps` App 创建接口。

前一版计划提出用脚本直接写入固定的 `app_main`、`app_jcc` 和 Secret，目的只是让隔离测试快速准备数据；这不应成为正式实现，因为它绕过了现有 App 管理 API，也无法体现真实的 Secret 一次性返回和管理员授权流程。本计划改为：

1. 管理员通过现有 `/admin/apps` 接口分别创建主应用服务身份和 JCC 子应用服务身份；接口继续自动生成真实 `app_id` 和 `app_secret`，数据库只保存 `app_secret_hash`。
2. 将接口返回的真实 `app_id` 和一次性 Secret 通过 Secret Store/部署环境传给对应服务。文档中的 `app_main`、`app_jcc` 是逻辑角色名，不强制改变现有随机 App ID 生成规则。
3. 通过新增的受保护 Scope/Grant 管理 API 配置长期授权，而不是由 Seed、迁移或脚本直接写凭证和授权关系。
4. 阶段一同时打通两个方向的只读通信：
   - `app_main → app_jcc → jcc:record:read`；
   - `app_jcc → app_main → main:application:read`。

已核对的仓库事实：

- main 的 [App 模型](/Users/zhengzebiao/code/tsuz-api-main/app/models/app.py) 已有 `app_id`、`app_secret_hash`、`is_enabled` 等字段；[app/core/security.py](/Users/zhengzebiao/code/tsuz-api-main/app/core/security.py) 已有 App Secret Hash 和常量时间校验。
- main 的 [App 管理 API](/Users/zhengzebiao/code/tsuz-api-main/app/api/admin_apps.py) 已实现创建、查询、启停和 Secret 重新生成，创建/重新生成响应只返回一次明文 Secret，并设置 `Cache-Control: no-store`。
- main 当前的 [TokenService](/Users/zhengzebiao/code/tsuz-api-main/app/services/token_service.py) 与 [AuthorizationService](/Users/zhengzebiao/code/tsuz-api-main/app/services/authorization_service.py) 面向用户 Token，包含用户 Session 和用户权限逻辑，不能直接复用为 Service Token 验证。
- main 当前 Alembic head 为 `0007_app_service_authorization`，通过 [alembic/env.py](/Users/zhengzebiao/code/tsuz-api-main/alembic/env.py) 显式导入模型；`0007` 新增 Resource Scope/Service Grant 授权表。
- JCC 当前只有用户 JWT 依赖 [app/deps/auth.py](/Users/zhengzebiao/code/tsuz-api-jcc/app/deps/auth.py)；[SampleProfile](/Users/zhengzebiao/code/tsuz-api-jcc/app/models/sample_profile.py) 和现有 Seed 可作为第一个只读资源接口的数据来源。
- 实施前两个仓库工作区均干净：main 为 `fix/uvicorn-access-logging`，JCC 为 `chore/jcc-local-infra`。

本阶段继续遵守已经确认的 Secret 约束：每个 App 只有一个当前 Secret，直接保存 Hash；不新增 `app_credentials` 表，不实现无停机轮换、Credential/Token Version、JWKS 轮换或即时 Token 撤销。

## 目标与非目标

### 本阶段目标

1. 通过现有 `POST /admin/apps` 注册两个逻辑应用身份，并覆盖创建响应、凭证 Hash 和 Secret 安全保存的真实 API 流程。
2. 新增 `resource_scopes` 和 `app_service_grants` 数据结构及迁移。
3. 新增受用户管理权限保护的最小 Scope/Grant 管理 API，用于配置并撤销阶段一所需的两个方向授权。
4. 新增 main `POST /internal/oauth/token` Client Credentials 接口，使用 HTTP Basic 的 `app_id + app_secret` 认证调用方，校验目标 App、Scope 和 Grant 后签发 5 分钟 Service Token。
5. 在 main 和 JCC 各自新增独立的 Service Token 验证依赖，与用户 Token 依赖完全分离。
6. 新增 JCC `GET /internal/v1/records` 和 main `GET /internal/v1/applications/{app_id}` 两个只读内部接口。
7. 提供两端的最小 Service Client：main 可用 `app_main` 凭证调用 JCC，JCC 可用 `app_jcc` 凭证调用 main；通过隔离 HTTP smoke 验证双向链路。
8. 补齐定向测试、迁移测试、配置说明、README 和 main 仓库要求的总方案/阶段计划/执行记录。

### 明确不实现

- 应用间写接口、`Idempotency-Key`、乐观锁、数据归属 `own/any`、批量操作和业务数据写入。
- 多 Secret 并行、无停机轮换、Credential/Token Version、JWKS 端点、在线 Introspection、即时撤销和 Refresh Token。
- 用迁移、Seed 或生产脚本生成/覆盖 App ID、App Secret；应用注册必须走现有 `/admin/apps`。
- Scope/Grant 的复杂审批流、限流、mTLS 和应用 Actor 审计；本阶段管理操作使用现有用户 Actor 审计。
- 用户 Token 与 Service Token 互换，或把用户 `scope`/`roles` 当作应用间授权。
- 把任意客户端提交的 caller、`X-App-ID` 或请求 Scope 原样当作可信身份/权限。

## 推荐架构与最终契约

### 应用注册与授权配置

```text
管理员用户 Token
  ↓ 现有 main POST /admin/apps（调用两次）
创建主应用服务身份、JCC 子应用服务身份
  ↓ 一次性保存真实 app_id + app_secret
新增 main Scope/Grant 管理接口
  ↓ 管理员配置长期授权
app_main_id → app_jcc_id → jcc:record:read
app_jcc_id  → main_app_id → main:application:read
```

现有 App 创建 API 自动生成真实 `app_id`。部署配置使用 `MAIN_APP_ID`、`JCC_APP_ID` 等环境变量保存返回的实际值；这些变量名是配置角色，不表示数据库中的 App ID 必须固定为 `app_main`/`app_jcc`。其中每个服务同时配置自己的 App ID 和对方的 App ID：自己的 ID 用于 `sub`/申请凭证，对方 ID 用于 Token 请求的 `audience`，以及资源端严格校验入站 Token 的 `aud`。Secret 只通过创建接口的一次性响应进入 Secret Store，不进入 Git、迁移、Seed、日志或普通业务请求。

### 双向调用链

```text
main 使用 MAIN_APP_ID + MAIN_APP_SECRET
  ↓ Basic Auth
main POST /internal/oauth/token
  audience=JCC_APP_ID
  scope=jcc:record:read
  ↓
Service Token: sub=MAIN_APP_ID, aud=JCC_APP_ID, token_use=service
  ↓ Bearer
JCC GET /internal/v1/records
  ↓
返回 active SampleProfile 的安全字段

JCC 使用 JCC_APP_ID + JCC_APP_SECRET
  ↓ Basic Auth
main POST /internal/oauth/token
  audience=MAIN_APP_ID
  scope=main:application:read
  ↓
Service Token: sub=JCC_APP_ID, aud=MAIN_APP_ID, token_use=service
  ↓ Bearer
main GET /internal/v1/applications/{app_id}
  ↓
返回 main 允许公开的 App 元数据
```

两个方向都由 main 签发 Token；JCC 不签发 Token，只作为资源服务和调用 main 的客户端。

### Service Token Claim 和生命周期

- `iss`：独立的 Service Token issuer 配置，例如 `tsuz-api-main`；不复用用户 Token 的业务语义。
- `sub`：通过 Basic Auth 验证出的真实 caller App ID。
- `aud`：本次请求声明且通过 Grant 校验的目标 App ID；资源服务必须严格匹配本服务配置的 App ID。
- `token_use`：固定为 `service`。
- `scope`：由 main 从有效 Grant 得到的、规范化后的空格分隔 Scope。
- `iat`、`nbf`、`exp`、`jti`：签发、生效、过期和唯一标识。
- 有效期固定为 300 秒，不签发 Refresh Token；调用客户端在过期前重新申请。
- 第一阶段复用 main 现有 RSA 签名密钥对，但 Service Token 使用独立 issuer、claims 和验证依赖。JCC 只配置公钥，绝不配置 main 私钥。
- 资源服务验证 Service Token 时不调用用户黑名单/Session 服务；第一阶段撤销 Grant 后，已经签发的 Token 最多继续有效至 `exp`。

## 接口契约

### 1. 现有 App 注册接口

继续使用 main 现有接口，不改变既有路由：

```http
POST /admin/apps
Authorization: Bearer <admin-user-token>
Content-Type: application/json
```

请求字段沿用 [AdminAppCreate](/Users/zhengzebiao/code/tsuz-api-main/app/schemas/admin_app.py)。管理员分别创建主应用和 JCC 应用，响应中的 `app.app_id` 和 `app_secret` 由受控操作人员保存。普通查询、编辑、启停响应继续不得包含 Secret 或 Hash。

阶段一不新增“指定 app_id 创建”能力，避免改变现有 App ID 生成契约；如果未来需要固定 ID，应另行设计迁移和兼容策略。

### 2. Resource Scope 管理 API（main）

新增路由前缀 `/admin/resource-scopes`，使用现有用户权限依赖。最小接口：

```text
GET  /admin/resource-scopes
POST /admin/resource-scopes
POST /admin/resource-scopes/{scope_id}/disable
POST /admin/resource-scopes/{scope_id}/enable
```

创建请求：

```json
{
  "target_app_id": "<实际 JCC app_id>",
  "scope_code": "jcc:record:read",
  "description": "Read JCC records"
}
```

规则：

1. `target_app_id` 必须指向现有 App；Scope code 使用严格、稳定的目标业务命名格式，去除首尾空格，不接受空值和重复 Scope。
2. `(target_app_id, scope_code)` 唯一；重复创建返回固定冲突错误，不生成第二条记录。
3. 禁用 Scope 不删除历史 Grant；Token Endpoint 只使用 `is_enabled=true` 的 Scope。
4. 仅管理用户可创建或变更 Scope，调用方 App 不能自行声明能力。
5. 管理操作与用户 Actor 审计在同一数据库事务提交。

### 3. App Service Grant 管理 API（main）

新增路由前缀 `/admin/service-grants`，使用现有用户权限依赖。最小接口：

```text
GET  /admin/service-grants
POST /admin/service-grants
POST /admin/service-grants/{grant_id}/revoke
```

创建请求：

```json
{
  "caller_app_id": "<实际 caller app_id>",
  "scope_id": 1,
  "expires_at": null
}
```

规则：

1. caller App 和 Scope 必须存在；target 由 Scope 的 `target_app_id` 唯一推导，不在 Grant 中重复保存。
2. `(caller_app_id, scope_id)` 唯一；重复提交相同有效 Grant 返回幂等结果，已撤销 Grant 不被静默恢复。
3. 撤销是明确的目标状态操作，保存撤销时间、原因和用户 Actor；撤销只阻止后续 Token 签发。
4. Token Endpoint 查询时必须同时满足 caller/target、Scope 启用、Grant 状态为 enabled、`valid_from <= now` 和 `expires_at` 未到期。
5. 任何请求不能通过请求体注入 caller 身份；Token Endpoint 的 caller 只来自已验证 Basic Auth。

### 4. main Client Credentials Token Endpoint

```http
POST /internal/oauth/token
Authorization: Basic base64(<app_id>:<app_secret>)
Content-Type: application/x-www-form-urlencoded
Cache-Control: no-store

grant_type=client_credentials&audience=<target_app_id>&scope=<scope1>%20<scope2>
```

请求处理：

1. 只从 HTTP Basic 读取 App ID 和 Secret；缺少、格式错误、App 不存在、Secret 错误或 caller 被禁用统一返回 `401 {"error":"invalid_client"}`，不泄露枚举信息。
2. 只允许 `grant_type=client_credentials`；`audience` 必须是非空目标 App ID；请求表单拒绝未声明字段。
3. Scope 规范化为集合，拒绝空值、重复值、格式非法值；请求的完整集合必须是有效 Grant 集合的子集，不能静默删除未授权 Scope。
4. 目标 App 必须存在且启用；Scope 的目标必须与 audience 完全一致。
5. 成功响应：

```json
{
  "access_token": "<service-token>",
  "token_type": "Bearer",
  "expires_in": 300,
  "scope": "jcc:record:read"
}
```

6. 成功和错误响应设置 `Cache-Control: no-store`；不记录 Authorization Header、Secret、完整 Token 或表单原文。

### 5. JCC 内部只读 API

```http
GET /internal/v1/records
Authorization: Bearer <service-token>
X-Request-ID: <optional>
```

接口固定要求 `jcc:record:read`。只查询 `sample_profiles.is_active=true`，按 `id` 稳定排序，响应只包含：

```json
[
  {"id": 1, "slug": "starter-profile", "display_name": "Starter Profile", "is_active": true}
]
```

无效 Service Token 返回 `401`，Token 有效但缺 Scope 返回 `403`；不改变用户侧 `/api/profile` 的行为。

### 6. main 内部只读 API

```http
GET /internal/v1/applications/{app_id}
Authorization: Bearer <service-token>
X-Request-ID: <optional>
```

接口固定要求 `main:application:read`，严格验证 `aud` 等于配置的 main App ID，再查询指定 App。响应只返回安全元数据：`id`、`app_id`、`name`、`icon_url`、`access_url`、`service_account_name`、`is_enabled`、时间和 `version`；不得返回 `app_secret` 或 `app_secret_hash`。不存在的 App 返回固定 `APP_NOT_FOUND`。

## 数据模型与迁移

### ResourceScope

新增 main [resource_scope.py](/Users/zhengzebiao/code/tsuz-api-main/app/models/resource_scope.py)，对应 `resource_scopes`：

- `id`：整数主键；
- `target_app_id`：`String(64)`，外键指向 `apps.app_id`；
- `scope_code`：`String(128)`；
- `description`：`String(255)`；
- `is_enabled`：Boolean，默认 `true`；
- `created_at`、`updated_at`：沿用项目无时区 `DateTime` 约定。

约束和索引：

- `UNIQUE(target_app_id, scope_code)`；
- `target_app_id`、`is_enabled` 查询索引；
- `target_app_id` 外键使用明确的删除策略，不能因删除 App 产生孤儿 Scope。

### AppServiceGrant

新增 main [app_service_grant.py](/Users/zhengzebiao/code/tsuz-api-main/app/models/app_service_grant.py)，对应 `app_service_grants`：

- `id`：整数主键；
- `caller_app_id`：`String(64)`，外键指向 `apps.app_id`；
- `scope_id`：外键指向 `resource_scopes.id`；
- `status`：`enabled` 或 `revoked`，默认 `enabled`；
- `valid_from`、`expires_at`；
- `created_by`、`created_at`；
- `revoked_by`、`revoked_at`、`revoke_reason`，可空；
- `UNIQUE(caller_app_id, scope_id)`，以及 caller、scope、status 的查询索引。

Grant 不重复保存 target；通过 Scope 归属保证 caller/target/scope 三元组不会产生冗余不一致。迁移不得插入 App、Secret、Scope 或 Grant 业务数据。

### Alembic

- 新增 main `alembic/versions/0007_app_service_authorization.py`，`down_revision=0006_email_registration`。
- 在 main `alembic/env.py` 显式导入两个模型。
- `upgrade` 只创建新表、外键、唯一约束、Check/查询索引；`downgrade` 只删除本迁移对象。
- 新 head 为 `0007_app_service_authorization` 后，更新 main 现有迁移/集成验证中硬编码的 `0006_email_registration` head 断言；历史中间节点断言保持不变。
- 真实迁移 round-trip 只能使用随机、一次性临时 PostgreSQL 数据库；不在共享开发或生产库执行 downgrade、清库或删除长期数据。

## Service Token 验证设计

### main 和 JCC 各自新增独立依赖

两端都新增 `app/deps/service_auth.py`，提供：

- `ServicePrincipal(caller_app_id, audience, jti, scopes)`；
- 独立的 Bearer 安全方案；
- `get_service_principal()`；
- `require_service_scope(scope)`。

验证顺序：

1. 必须存在 Bearer Token；
2. 使用固定允许算法和配置公钥验证签名；
3. 检查 `iss`、严格字符串 `aud`、`token_use=service`；
4. 检查 `sub`、`scope`、`jti` 类型和非空约束；
5. 让 PyJWT 校验 `iat`/`nbf`/`exp`，并应用有限时钟偏差；
6. 缺少接口所需 Scope 返回 `403`；
7. 通过验证后从 `sub` 取得 caller App ID，不读取普通请求头中的应用身份。

Service Auth 不调用 `get_current_user()`、用户黑名单或用户 Session 服务，避免用户 Token 与 Service Token 混淆。用户 Token 即使由同一 RSA Key 签名，也因缺少/不匹配 `token_use`、Audience 或 Scope 而不能访问内部接口。

## Service Client 设计

为使两个方向不仅有资源端点，也有可复用的调用实现：

- main 新增内部 JCC Client：使用 `MAIN_APP_ID`、`MAIN_APP_SECRET`，向 main Token Endpoint 申请 audience 为 `JCC_APP_ID` 的 Token，再请求 JCC `/internal/v1/records`。
- JCC 新增内部 Main Client：使用 `JCC_APP_ID`、`JCC_APP_SECRET`，向 `MAIN_TOKEN_URL` 申请 audience 为 `MAIN_APP_ID` 的 Token，再请求 main `/internal/v1/applications/{app_id}`。
- Client 使用短期内存 Token 缓存，并在过期前提前刷新；不持久化 Token，不把 Secret 放入普通业务 URL、日志或异常文本。
- HTTP 超时使用显式配置；第一阶段不实现复杂重试和写请求幂等。Token 申请失败、401、403 和目标业务错误保留安全、固定的客户端异常类型。
- 两端增加运行时 `httpx` 依赖；不能只把 httpx 留在测试依赖中。

## 配置与凭证

### main

修改 [app/core/config.py](/Users/zhengzebiao/code/tsuz-api-main/app/core/config.py) 增加非用户 Token 配置：

```dotenv
SERVICE_TOKEN_ISSUER=tsuz-api-main
SERVICE_TOKEN_EXPIRE_SECONDS=300
SERVICE_TOKEN_CLOCK_SKEW_SECONDS=5
SERVICE_TOKEN_PUBLIC_KEY="<main public key injected through secret management>"
MAIN_APP_ID=<value returned by POST /admin/apps>
MAIN_APP_SECRET=<value returned once by POST /admin/apps>
JCC_APP_ID=<value returned by POST /admin/apps>
JCC_API_BASE_URL=http://127.0.0.1:8001
INTERNAL_HTTP_TIMEOUT_SECONDS=10
```

main 签发端第一阶段复用已有 `JWT_PRIVATE_KEY`；`SERVICE_TOKEN_PUBLIC_KEY` 与 JCC 配置的公钥来自同一测试/部署密钥对。示例文件只能包含占位符和注入说明，不包含真实值。

### JCC

修改 [app/core/config.py](/Users/zhengzebiao/code/tsuz-api-jcc/app/core/config.py) 增加：

```dotenv
SERVICE_TOKEN_ISSUER=tsuz-api-main
SERVICE_TOKEN_AUDIENCE=<JCC_APP_ID returned by POST /admin/apps>
SERVICE_TOKEN_PUBLIC_KEY="<copy matching main public key through secret management>"
JCC_APP_ID=<value returned by POST /admin/apps>
JCC_APP_SECRET=<value returned once by POST /admin/apps>
MAIN_APP_ID=<value returned by POST /admin/apps>
MAIN_TOKEN_URL=http://127.0.0.1:8000/internal/oauth/token
MAIN_API_BASE_URL=http://127.0.0.1:8000
INTERNAL_HTTP_TIMEOUT_SECONDS=10
```

JCC 仍然只接收用户 JWT 公钥和 Service Token 公钥，不增加任何私钥配置。实际本地 `.env` 使用安全文件权限，示例和 Git 历史不写 Secret。

## 代码变更清单

### main

配置、依赖和路由：

- [app/core/config.py](/Users/zhengzebiao/code/tsuz-api-main/app/core/config.py)：Service Token 和双向 Client 配置。
- [app/main.py](/Users/zhengzebiao/code/tsuz-api-main/app/main.py)：注册内部 OAuth、Scope/Grant 管理和 main 内部资源 Router。
- [pyproject.toml](/Users/zhengzebiao/code/tsuz-api-main/pyproject.toml)、[pdm.lock](/Users/zhengzebiao/code/tsuz-api-main/pdm.lock)：加入运行时 `httpx` 和 Token 表单解析所需依赖。
- [.env.local.example](/Users/zhengzebiao/code/tsuz-api-main/.env.local.example)、[.env.deploy.example](/Users/zhengzebiao/code/tsuz-api-main/.env.deploy.example)、[README.md](/Users/zhengzebiao/code/tsuz-api-main/README.md)：说明 API 注册、一次性 Secret 保存、Service Token 和调用配置。

模型、迁移和授权：

- 新增 [app/models/resource_scope.py](/Users/zhengzebiao/code/tsuz-api-main/app/models/resource_scope.py)。
- 新增 [app/models/app_service_grant.py](/Users/zhengzebiao/code/tsuz-api-main/app/models/app_service_grant.py)。
- 修改 [alembic/env.py](/Users/zhengzebiao/code/tsuz-api-main/alembic/env.py)，新增 `0007_app_service_authorization.py`。
- 新增 Resource Scope/Grant Schema、Service 和管理 API 文件，沿用现有严格 Schema、用户权限依赖、事务 commit/rollback、固定错误码和 AuditEvent 模式。

Token 与内部资源：

- 新增 [app/schemas/service_token.py](/Users/zhengzebiao/code/tsuz-api-main/app/schemas/service_token.py)。
- 新增 [app/services/service_token_service.py](/Users/zhengzebiao/code/tsuz-api-main/app/services/service_token_service.py)。
- 新增 [app/api/internal_oauth.py](/Users/zhengzebiao/code/tsuz-api-main/app/api/internal_oauth.py)。
- 新增 main [app/deps/service_auth.py](/Users/zhengzebiao/code/tsuz-api-main/app/deps/service_auth.py)。
- 新增 main [app/api/internal.py](/Users/zhengzebiao/code/tsuz-api-main/app/api/internal.py)，实现 `/internal/v1/applications/{app_id}`。
- 新增 main [app/clients/jcc_client.py](/Users/zhengzebiao/code/tsuz-api-main/app/clients/jcc_client.py)。

### JCC

- [app/core/config.py](/Users/zhengzebiao/code/tsuz-api-jcc/app/core/config.py)：Service Token 验证、JCC/ main App 身份和 HTTP Client 配置。
- 新增 [app/deps/service_auth.py](/Users/zhengzebiao/code/tsuz-api-jcc/app/deps/service_auth.py)：独立 `ServicePrincipal`、Bearer 验证和 Scope 依赖。
- 新增 [app/schemas/internal.py](/Users/zhengzebiao/code/tsuz-api-jcc/app/schemas/internal.py)。
- 新增 [app/api/internal.py](/Users/zhengzebiao/code/tsuz-api-jcc/app/api/internal.py)：实现 `/internal/v1/records`。
- 新增 [app/clients/main_client.py](/Users/zhengzebiao/code/tsuz-api-jcc/app/clients/main_client.py)。
- [app/main.py](/Users/zhengzebiao/code/tsuz-api-jcc/app/main.py)：注册内部 Router，不改变 `/api/profile`。
- [.env.test.example](/Users/zhengzebiao/code/tsuz-api-jcc/.env.test.example)、[.env.product.example](/Users/zhengzebiao/code/tsuz-api-jcc/.env.product.example)、[.env.deploy.example](/Users/zhengzebiao/code/tsuz-api-jcc/.env.deploy.example)、[README.md](/Users/zhengzebiao/code/tsuz-api-jcc/README.md)：补充配置和双向调用说明。
- JCC 不新增应用间授权数据库；Scope/Grant 的唯一事实源在 main。

### 测试与验证

- main 新增 ResourceScope/Grant 模型、迁移、Schema、Service、管理 API、Service Token 和 main 内部 API 测试。
- main 扩展现有 App 管理 API 测试，确认两个应用必须通过现有创建接口注册，响应 Secret 仍为一次性且普通响应安全。
- JCC 新增 Service Auth、内部 records API、Main Client 和边界测试。
- 两端更新迁移 head 断言、OpenAPI 测试和用户鉴权回归测试。
- 新增 opt-in 双服务 HTTP smoke：在隔离临时数据库中先通过 HTTP `/admin/apps` 创建测试 App，再通过 HTTP Scope/Grant 管理 API 配置授权，随后分别执行两个方向的 Token 申请和只读调用。该测试工具只负责测试编排，不直接生成或写入 App ID/Secret，也不替代生产注册流程。

## 实施顺序

1. 在 main 新增 ResourceScope/Grant 模型、迁移、Alembic 注册和数据层测试；更新迁移检查中的 head 断言。
2. 在 main 实现 Scope/Grant Schema、Service、管理 API、用户权限声明和用户 Actor 审计；验证重复、禁用、撤销和事务回滚。
3. 在 main 实现独立 Service Token Schema、ServiceTokenService、HTTP Basic/Form Token Endpoint 和 Service Auth；验证 caller、target、Scope 子集、Claim、TTL、错误码和脱敏。
4. 在 main 实现内部 App 只读接口和 JCC Client。
5. 在 JCC 实现独立 Service Auth、records 只读接口和 Main Client；确保用户 `/api/profile` 不变。
6. 更新两端配置模板、README、运行时依赖和锁文件；不把真实 app_id、Secret、Token 或连接凭证提交到仓库。
7. 先运行两端定向测试、lint、全量测试和 main `alembic check`；再在获得授权且资源隔离时运行 PostgreSQL migration round-trip 和双服务 HTTP smoke。
8. 实施完成后按照 main [CLAUDE.md](/Users/zhengzebiao/code/tsuz-api-main/CLAUDE.md) 同步总实施方案、第一阶段计划和第一阶段执行记录；所有结果按真实情况标记。

## 测试与验证计划

### main 定向测试

至少覆盖：

- 现有 `/admin/apps` 创建两个 App，数据库只保存 Hash，创建响应设置 `no-store`，列表/详情不暴露 Secret；
- Resource Scope 目标 App 校验、唯一约束、启停和固定错误；
- Grant caller/Scope 校验、唯一约束、幂等创建、撤销、有效期和目标归属；
- Basic App 凭证正确签发 Token；不存在 App、错误 Secret、禁用 caller 统一 `invalid_client`；
- 不存在/禁用 target、未知 Scope、错误 Scope 目标、未授权/部分未授权 Scope、已撤销或过期 Grant 全部拒绝；
- Token `sub/aud/token_use/scope/iat/nbf/exp/jti` 和 300 秒生命周期正确；响应始终 `no-store`；
- main Service Auth 正确接受面向 main 的 Token，错误签名、issuer、audience、token type、时间、Claim 类型和缺 Scope 分别得到 401/403；
- main 内部 App 响应字段安全，普通用户 Token 不能访问；
- Scope/Grant 管理事务失败回滚，审计不含 Secret/Hash/完整 Token；
- 原有用户登录、权限同步、App 管理和 Redis 用户状态测试无回归。

### JCC 定向测试

至少覆盖：

- 正确 Service Token 通过 `/internal/v1/records`；
- 错误签名、issuer、audience、token_use、过期、未生效、缺少/错误类型 Claim、公钥缺失均为 401；
- 缺 `jcc:record:read` 或只含其他 Scope 为 403；
- 合法用户 JWT 即使签名相同，也不能访问内部 records；Service Auth 不读取用户 blacklist/session；
- 只返回 active SampleProfile 的安全字段；内部 OpenAPI Bearer scheme 和 Request ID 正常；
- Main Client 只向 Token Endpoint 发送 Basic 凭证，缓存 Token 不超过有效期，错误响应和日志不包含 Secret/Token；
- 原有 `/api/profile`、用户 JWT、main Redis 黑名单/Session 读取测试无回归。

### 迁移、质量与命令

```bash
# main
cd /Users/zhengzebiao/code/tsuz-api-main
pdm run pytest tests/test_app_to_app_models.py tests/test_service_token.py tests/test_service_authorization_api.py -q
pdm run lint
pdm run test
pdm run alembic check
git diff --check

# JCC
cd /Users/zhengzebiao/code/tsuz-api-jcc
pdm run pytest tests/test_internal_api.py tests/test_main_client.py tests/test_logging.py -q
pdm run ruff check tests/test_internal_api.py tests/test_main_client.py tests/test_logging.py
pdm run test
git diff --check
```

命令中的测试文件可在实现时按仓库实际命名拆分，但覆盖范围不能减少。PostgreSQL round-trip 使用随机临时数据库，验证：

```text
0006_email_registration
  → 0007_app_service_authorization
  → 检查新表、外键、唯一约束和索引
  → downgrade 0006
  → 确认新表删除且历史 apps/users 数据保留
  → upgrade head
  → alembic check
```

### 双向 HTTP smoke

使用独立临时 main/JCC PostgreSQL、独立 Redis Key 前缀、随机监听端口和运行时测试 RSA Key：

1. main 迁移并创建测试管理员/同步管理权限；JCC 迁移并 Seed `sample_profiles`。
2. 通过 main `/admin/apps` HTTP 接口创建两个 App，读取响应中的一次性 Secret，仅保存在测试进程内存；不调用 Service 直接插入 App。
3. 通过 main Scope/Grant 管理 HTTP 接口创建两个 Scope 和两个 Grant。
4. 启动 main 与 JCC；使用 main App Client 申请 `aud=JCC_APP_ID` Token 并读取 `/internal/v1/records`。
5. 使用 JCC Main Client 申请 `aud=MAIN_APP_ID` Token 并读取 main `/internal/v1/applications/{app_id}`。
6. 验证错误 audience、缺 Scope、错误 Secret、禁用 caller/target 和撤销 Grant 的边界。
7. 停止进程、删除临时数据库和临时 Redis namespace；失败或清理失败必须真实记录，不能宣称 smoke 通过。

生产迁移、部署、真实长期 Secret 注入和外部服务调用不在本计划审批前执行。

## 安全、异常和兼容性

- 默认拒绝；caller 只能来自 HTTP Basic 验证，资源服务 caller 只能来自签名 Token 的 `sub`。
- Token Endpoint 和资源端点都固定算法，不根据 JWT 的 `alg` 动态选择验证方式。
- `aud` 必须是单个、精确匹配的目标 App ID；仅有 Scope 而 Audience 错误仍然拒绝。
- App、Scope、Grant 管理异常返回固定错误码，不返回 SQL、约束名、堆栈或凭证内容。
- 长期 App Secret 只出现在现有 App 创建/重新生成的一次性响应和受控 Secret Store；业务请求只携带 5 分钟 Service Token。
- 日志、Request ID、HTTP Client 异常和审计只允许记录非敏感标识；不得记录 Authorization Header、Basic 内容、完整 JWT、Secret 或 Hash。
- Grant/Scope 管理操作沿用现有用户 Actor 审计；应用级 Actor 字段和详细应用调用审计留到后续阶段。
- 新迁移只新增表，不破坏现有 App、用户、角色、权限、Session 和审计数据；生产回滚优先使用前向修复，不依赖 downgrade。
- 用户 JWT 和 Service Token 即使暂时复用 RSA 密钥，也必须由不同的 issuer/token_use/audience/验证依赖隔离；未来可在不改变 API 语义的情况下切换专用密钥。

## 阶段验收标准

- [x] 两个服务身份继续由现有 `POST /admin/apps` 创建；实现与测试保持一次性 Secret/Hash 约束。
- [x] Resource Scope/Grant 管理 API 可配置并撤销两个方向授权；唯一约束、Scope 目标归属和用户 Actor 审计有测试证据。
- [x] Service Auth 严格验证签名、issuer、audience、token_use、时间、Claim 类型和 Scope；用户 Token 不能访问 JCC 内部 API。
- [x] JCC records 只返回 active 安全字段；Main Client 使用 Basic/token cache/固定错误契约。
- [x] Token、Secret、Hash 不进入日志、普通响应或 URL；Basic 日志脱敏和内部字段白名单有测试证据。
- [x] 原有用户鉴权、`/api/profile`、Redis 黑名单/Session 读取无回归，JCC 全量测试通过。
- [ ] main/JCC 的真实双向 HTTP smoke、隔离 migration round-trip 和 main `alembic check` 的新 head 环境结果待专用环境验证。
- [x] main 总方案、两侧第一阶段计划、执行记录和 JCC 设计文档已互链，且未写入真实敏感值。
