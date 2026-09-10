# 应用间权限管理设计：调用方 → 目标方 → Scope

## 1. 文档目的

本文说明在 `tsuz-api-main` 作为统一认证与授权中心、`tsuz-api-jcc` 及未来多个子应用相互调用的场景下，如何围绕 `app_id`、`app_secret` 和 Service Token 管理应用间权限。

本文只讨论应用身份和应用间数据读写，不涉及用户身份、用户角色或代表用户调用的授权模型。

核心结论：

> `app_id + app_secret` 用于证明“调用者是哪一个应用”；`调用方 → 目标方 → Scope` 用于决定“该应用能对哪个应用执行什么操作”；Service Token 是这项授权在短时间内的可验证载体。

---

## 2. 核心概念

一次应用间授权至少包含三个维度：

```text
调用方应用（caller） + 目标应用（target） + 操作能力（scope）
```

可以将每条授权表达为：

```text
Grant(caller_app, target_app, scope)
```

例如：

```text
app_main → app_jcc → jcc:record:read
app_main → app_jcc → jcc:record:write
app_jcc  → app_main → main:config:read
app_jcc  → app_main → main:event:write
```

这四条是四项互相独立的授权，不能相互推导。

### 2.1 调用方

调用方是主动发起 HTTP 请求的应用。例如：

- main 调用 JCC 时，调用方是 `app_main`；
- JCC 调用 main 时，调用方是 `app_jcc`；
- CRM 调用 JCC 时，调用方是 `app_crm`。

调用方身份必须来自对 `app_id + app_secret` 的验证结果，或者来自已经验证通过的 Service Token 的 `sub`，不能相信普通请求头中由客户端自行填写的应用 ID。

### 2.2 目标方

目标方是拥有 API 和业务数据、负责执行最终权限检查的应用。例如：

- main 调用 JCC 数据接口时，目标方是 `app_jcc`；
- JCC 调用 main 的撤销接口时，目标方是 `app_main`。

Service Token 使用 `aud` 声明目标应用，使签发给 JCC 的 Token 不能被拿去调用 main、CRM 或其他应用。

### 2.3 Scope

Scope 表示目标应用公开给其他应用的一项业务能力。例如：

```text
jcc:record:read
jcc:record:create
jcc:record:update
jcc:record:delete
jcc:statistics:read
jcc:export:create

main:config:read
main:event:write
main:session:revoke
```

Scope 的语义由目标应用定义：

- `jcc:*` 由 JCC 定义并在 JCC API 中执行；
- `main:*` 由 main 定义并在 main API 中执行；
- `crm:*` 由 CRM 定义并在 CRM API 中执行。

---

## 3. 为什么不能只给应用配置全局 `read` 或 `write`

假设系统存在：

```text
app_main
app_jcc
app_crm
```

如果只给 JCC 配置：

```text
permissions = ["read", "write"]
```

会产生以下歧义：

- JCC 可以读取 main 还是 CRM？
- JCC 可以写入哪些资源？
- 新增一个应用后，JCC 是否自动获得它的访问权限？
- `write` 是否包含创建、修改、删除和批量导入？

使用方向明确的授权后：

```text
app_jcc → app_main → main:config:read
```

只表示 JCC 可以调用 main 中要求 `main:config:read` 的接口，不表示 JCC 可以：

- 写入 main 配置；
- 读取 CRM 数据；
- 调用 JCC 自己的管理接口；
- 访问以后新增的其他应用。

因此应用间权限应遵循：

```text
默认拒绝，显式授权，方向隔离，最小权限
```

---

## 4. 认证和授权必须分开

### 4.1 认证：证明调用方是谁

JCC 使用自己的凭证向 main 的 Token Endpoint 申请 Service Token：

```text
app_id     = app_jcc
app_secret = secret_jcc
```

main 验证成功后，只能确认：

```text
当前请求方确实是 app_jcc
```

这一步不能自动赋予 JCC 任何业务权限。

### 4.2 授权：决定调用方能对谁做什么

main 还需要查询授权关系：

```text
app_jcc 是否能调用 app_main？
app_jcc 对 app_main 被授予了哪些 Scope？
本次申请的 audience 和 Scope 是否都在授权范围内？
```

因此三类数据的职责是：

```text
app_id + app_secret  → 验证调用方身份
Grant                 → 定义长期授权关系
Service Token         → 携带本次批准的短期权限
```

---

## 5. 权限矩阵示例

假设有 main、JCC 和 CRM 三个应用，可以得到如下权限矩阵：

| 调用方 ↓ / 目标方 → | main | JCC | CRM |
| --- | --- | --- | --- |
| main | — | `jcc:record:read`、`jcc:record:write` | `crm:customer:read` |
| JCC | `main:config:read`、`main:event:write` | — | 无权限 |
| CRM | `main:config:read` | `jcc:statistics:read` | — |

需要注意：

1. `main → JCC` 和 `JCC → main` 是两个方向，必须分别授权；
2. 空白关系表示默认拒绝；
3. 新增应用不会自动继承已有权限；
4. 一个方向上的多个 Scope 可以分别启用、撤销和设置有效期；
5. 权限不具有传递性。

例如已有：

```text
app_main → app_jcc → jcc:data:read
app_jcc  → app_crm → crm:data:read
```

不能据此推导：

```text
app_main → app_crm → crm:data:read
```

main 如果需要调用 CRM，必须有单独授权。

---

## 6. 推荐的数据模型

### 6.1 应用注册表

```text
applications
├── id
├── app_id
├── app_secret_hash
├── name
├── is_enabled
├── service_account_name
├── created_at
└── updated_at
```

示例：

| id | app_id | name | is_enabled |
| ---: | --- | --- | --- |
| 1 | `app_main` | 主应用 | true |
| 2 | `app_jcc` | JCC 子应用 | true |
| 3 | `app_crm` | CRM 子应用 | true |

`app_id` 是稳定、不可由调用方随意更改的应用身份。

当前 main 的 App 模型已经具备 `app_id`、`app_secret_hash`、`service_account_name` 和 `is_enabled` 等基础字段，可参考 [main App 模型](../../tsuz-api-main/app/models/app.py)。

### 6.2 App Secret 存储规则

每个应用只保留一个有效的 App Secret，其 Hash 直接保存在 `applications.app_secret_hash`（对应当前 main 的 `apps.app_secret_hash`）中。

约束和规则：

- 数据库只保存 Secret Hash，不保存明文 Secret；
- 每个应用使用独立 Secret；
- App Secret 只负责认证应用身份，不直接定义权限；
- 应用间权限单独保存在 Grant 中；
- App 被禁用后，其 `app_id + app_secret` 不能再换取新 Token。

### 6.3 目标应用支持的 Scope

```text
resource_scopes
├── id
├── target_app_id
├── scope_code
├── description
├── is_enabled
├── created_at
└── updated_at
```

推荐唯一约束：

```text
UNIQUE(target_app_id, scope_code)
```

示例：

| 目标应用 | Scope | 说明 |
| --- | --- | --- |
| `app_jcc` | `jcc:record:read` | 读取 JCC 记录 |
| `app_jcc` | `jcc:record:write` | 创建或修改 JCC 记录 |
| `app_jcc` | `jcc:record:delete` | 删除 JCC 记录 |
| `app_jcc` | `jcc:statistics:read` | 读取 JCC 统计数据 |
| `app_main` | `main:config:read` | 读取 main 下发配置 |
| `app_main` | `main:event:write` | 向 main 上报应用事件 |

### 6.4 应用间授权表

概念模型：

```text
app_service_grants
├── id
├── caller_app_id
├── target_app_id
├── scope_id
├── status
├── valid_from
├── expires_at
├── created_by
├── created_at
├── revoked_by
├── revoked_at
└── revoke_reason
```

推荐唯一约束：

```text
UNIQUE(caller_app_id, target_app_id, scope_id)
```

还应保证 `scope_id` 对应的 Scope 确实属于 `target_app_id`。可以通过复合外键约束，或者在数据模型中只保存 `caller_app_id + scope_id`，再通过 `scope_id` 推导目标应用，以避免冗余不一致。

示例数据：

| 调用方 | 目标方 | Scope | 状态 |
| --- | --- | --- | --- |
| `app_main` | `app_jcc` | `jcc:record:read` | enabled |
| `app_main` | `app_jcc` | `jcc:record:write` | enabled |
| `app_jcc` | `app_main` | `main:config:read` | enabled |
| `app_jcc` | `app_main` | `main:event:write` | enabled |
| `app_jcc` | `app_main` | `main:data:delete` | revoked |

---

## 7. Service Token 申请流程

假设 JCC 需要读取 main 配置并上报事件。

### 7.1 JCC 申请 Token

```http
POST /internal/oauth/token
Authorization: Basic base64(app_jcc:secret_jcc)
Content-Type: application/x-www-form-urlencoded

 grant_type=client_credentials&
 audience=app_main&
 scope=main:config:read main:event:write
```

说明：上述表单换行仅用于展示，实际请求按标准表单编码发送。

### 7.2 main 验证调用方凭证

main 执行：

1. 从 Basic Auth 提取 `app_id` 和 `app_secret`；
2. 根据 `app_id` 查询应用；
3. 使用安全的常量时间比较验证 `app_secret` 与 `app_secret_hash`；
4. 检查 App 是否启用；
5. 将已验证的应用作为 `caller=app_jcc`。

调用方不能在请求体中另行指定：

```json
{
  "caller_app": "app_admin"
}
```

调用方身份只能来自凭证验证结果。

### 7.3 main 验证目标应用

请求声明：

```text
audience = app_main
```

main 检查：

- `app_main` 是否存在；
- `app_main` 是否启用；
- `app_main` 是否允许作为 Service Token 的资源目标；
- JCC 是否存在任何指向 `app_main` 的有效 Grant。

### 7.4 main 查询允许的 Scope

查询条件：

```text
caller = app_jcc
target = app_main
status = enabled
valid_from <= 当前时间
expires_at 为空或大于当前时间
```

假设查询结果为：

```text
allowed_scopes = {
  "main:config:read",
  "main:event:write"
}
```

客户端申请：

```text
requested_scopes = {
  "main:config:read",
  "main:event:write"
}
```

必须满足：

```text
requested_scopes ⊆ allowed_scopes
```

如果 JCC 申请：

```text
main:config:read main:data:delete
```

而 Grant 中没有 `main:data:delete`，应拒绝整个 Token 请求：

```http
HTTP/1.1 400 Bad Request
Content-Type: application/json

{
  "error": "invalid_scope",
  "error_description": "Requested scope is not granted"
}
```

不建议静默删除未授权 Scope 后继续签发 Token，否则客户端可能误以为获得了完整权限。

### 7.5 main 签发 Service Token

验证全部通过后，main 签发短期 Token：

```json
{
  "iss": "tsuz-api-main",
  "sub": "app_jcc",
  "aud": "app_main",
  "token_use": "service",
  "scope": "main:config:read main:event:write",
  "iat": 1788900000,
  "nbf": 1788900000,
  "exp": 1788900300,
  "jti": "service-token-uuid"
}
```

Claim 含义：

| Claim | 含义 |
| --- | --- |
| `iss` | Token 的统一签发方 |
| `sub` | 调用方应用，即 `app_jcc` |
| `aud` | 目标应用，即 `app_main` |
| `token_use` | 明确 Token 类型为应用间服务 Token |
| `scope` | 本次获批的应用间权限 |
| `iat` / `nbf` / `exp` | 签发、生效和失效时间 |
| `jti` | Token 唯一标识，用于审计或撤销 |

Service Token 建议有效期为 3～5 分钟，不签发 Refresh Token。调用应用可将 Token 缓存在内存中，并在过期前约 30 秒重新申请。

---

## 8. 目标应用收到 Token 后的验证流程

JCC 使用 Token 调用 main：

```http
GET /internal/v1/config
Authorization: Bearer <service-token>
X-Request-ID: req-123
```

main 作为目标应用必须执行以下检查。

### 8.1 验证 Token 的真实性

检查：

- JWT 签名正确；
- 算法属于服务端固定允许列表；
- `iss` 是受信任的统一签发方；
- `exp` 未过期；
- `nbf` 已生效；
- 必要 Claim 完整且类型正确。

不能根据 Token 自带的 `alg` 随意选择验证算法。

### 8.2 验证 Audience

main 必须检查：

```text
aud == app_main
```

假设存在另一个合法 Token：

```json
{
  "sub": "app_jcc",
  "aud": "app_crm",
  "scope": "crm:data:read"
}
```

即使它的签名完全合法，main 也必须拒绝，因为该 Token 不是签发给 main 的。

### 8.3 验证 Token 类型

检查：

```text
token_use == service
```

这样可以避免未来引入用户 Token 后发生 Token 类型混淆。

### 8.4 验证接口所需 Scope

假设接口声明：

```text
GET /internal/v1/config
required_scope = main:config:read
```

Token 包含 `main:config:read`，允许访问。

若请求：

```text
DELETE /internal/v1/config/{id}
required_scope = main:config:delete
```

而 Token 不包含该 Scope，返回：

```http
HTTP/1.1 403 Forbidden
Content-Type: application/json

{
  "code": "insufficient_scope"
}
```

### 8.5 执行目标应用自己的业务规则

Scope 只解决“调用方是否可以调用这类能力”，不能替代业务校验。

例如 Token 包含：

```text
jcc:record:write
```

并不意味着调用方可以：

- 修改所有字段；
- 绕过状态流转；
- 修改系统字段；
- 覆盖其他应用的数据；
- 跳过数据校验；
- 执行任意数据库操作。

JCC 仍需执行自己的字段白名单、状态机、版本冲突、数据归属和审计规则。

---

## 9. 双向调用示例

### 9.1 main 读取 JCC 数据

长期 Grant：

```text
app_main → app_jcc → jcc:record:read
```

main 申请：

```text
audience = app_jcc
scope = jcc:record:read
```

Service Token：

```json
{
  "sub": "app_main",
  "aud": "app_jcc",
  "token_use": "service",
  "scope": "jcc:record:read"
}
```

调用：

```http
GET /internal/v1/records/123
Authorization: Bearer <service-token>
```

JCC 验证 Token 后，检查接口所需 `jcc:record:read`，再查询自己的数据库并返回字段白名单内的数据。

### 9.2 main 尝试删除 JCC 数据

main 使用只有 `jcc:record:read` 的 Token 调用：

```http
DELETE /internal/v1/records/123
Authorization: Bearer <service-token>
```

接口要求：

```text
jcc:record:delete
```

JCC 返回 `403 Forbidden`。即使调用者是 main，也不能绕过 JCC 的接口权限。

### 9.3 JCC 向 main 上报事件

长期 Grant：

```text
app_jcc → app_main → main:event:write
```

Service Token：

```json
{
  "sub": "app_jcc",
  "aud": "app_main",
  "token_use": "service",
  "scope": "main:event:write"
}
```

调用：

```http
POST /internal/v1/events
Authorization: Bearer <service-token>
Idempotency-Key: 5592c2c0-1baf-43fb-8f34-f36d06087798
Content-Type: application/json

{
  "event_type": "jcc.record.updated",
  "resource_id": "123"
}
```

main 验证 Audience、Token 类型和 Scope 后接收事件。该授权不代表 JCC 可以读取 main 的其他数据。

---

## 10. Grant 与 Service Token 的区别

### 10.1 Grant 是长期授权配置

数据库中保存：

```text
app_main → app_jcc → jcc:record:read
```

它表示管理员允许 main 申请这一权限。Grant 可以持续存在，直到被禁用、撤销或达到过期时间。

### 10.2 Service Token 是短期授权结果

Token 中保存：

```json
{
  "sub": "app_main",
  "aud": "app_jcc",
  "scope": "jcc:record:read",
  "exp": "约 5 分钟后"
}
```

它表示 main 在短时间内可以使用这项权限调用 JCC。

两者关系：

```text
Grant
  │ Token Endpoint 校验
  ▼
Service Token
  │ 目标应用验证
  ▼
具体内部 API
```

因此：

- App Secret 与 Grant 分别承担身份认证和权限授权职责；
- 增加或撤销 Grant 不需要修改 App Secret；
- Grant 被撤销后，不再签发包含该 Scope 的新 Token；
- 已签发 Token 最多继续有效到 `exp`；
- 若要求立即撤销，可增加 Token Introspection 或应用状态缓存。

第一阶段采用 3～5 分钟短期 Token，通常可以在复杂度与撤销时效之间取得较好平衡。

---

## 11. Scope 命名原则

推荐格式：

```text
目标应用:资源:动作
```

例如：

```text
jcc:record:read
jcc:record:create
jcc:record:update
jcc:record:delete
jcc:statistics:read
jcc:export:create

main:config:read
main:event:write
main:application-status:update
main:session:revoke
```

规则：

1. 以目标应用命名空间开头，降低跨应用名称冲突；
2. 使用业务资源，不使用数据库表名；
3. 动作尽量明确；
4. 删除、批量操作、导出等高风险能力单独授权；
5. 避免 `admin`、`all`、`read_write_everything` 等过宽权限；
6. 第一阶段可以合并 `create/update` 为 `write`，但删除权限仍建议单独拆分。

---

## 12. 数据归属与 `own` / `any` 权限

即使当前不涉及用户，也可能存在“某条数据属于哪个来源应用”的问题。

例如 JCC 保存：

```text
record_1.source_app_id = app_main
record_2.source_app_id = app_crm
```

可以进一步定义：

```text
jcc:record:read:own
jcc:record:read:any
jcc:record:write:own
jcc:record:write:any
```

若 CRM 只有：

```text
app_crm → app_jcc → jcc:record:read:own
```

JCC 应从已验证 Token 的 `sub` 得到：

```text
caller_app_id = app_crm
```

并自动施加数据范围：

```sql
WHERE source_app_id = 'app_crm'
```

不能相信调用方自行传入的：

```http
X-App-ID: app_crm
```

因为普通 Header 可以伪造。调用方身份必须来自经过签名验证的 Token。

如果只有 main 可以跨来源读取，可以配置：

```text
app_main → app_jcc → jcc:record:read:any
app_crm  → app_jcc → jcc:record:read:own
```

---

## 13. 权限管理职责划分

### 13.1 目标应用定义能力

JCC 定义并维护：

```text
jcc:record:read
jcc:record:write
jcc:statistics:read
```

JCC 决定每个内部接口要求哪些 Scope，并负责最终业务校验。

### 13.2 main 认证中心管理授权关系

main 保存并管理：

```text
app_main → app_jcc → jcc:record:read
app_jcc  → app_main → main:event:write
```

main 在签发 Token 时验证凭证、Audience 和 Grant，不能把客户端申请的 Scope 原样写入 Token。

### 13.3 被调用应用执行最终校验

目标应用收到 Token 后检查：

```text
签名
iss
aud
token_use
exp / nbf
scope
本地业务规则
```

职责可以概括为：

```text
目标应用定义 Scope
main 分配 Scope
目标应用强制执行 Scope
```

---

## 14. Grant 撤销和 App 禁用

### 14.1 撤销单项 Grant

撤销：

```text
app_main → app_jcc → jcc:record:write
```

结果：

- main 无法再申请带 `jcc:record:write` 的新 Token；
- `jcc:record:read` 不受影响；
- 已签发 Token 最多继续生效至过期。

### 14.2 禁用整个调用应用

禁用 `app_jcc` 后：

- JCC 不能再用 `app_id + app_secret` 申请新 Service Token；
- JCC 已有的全部出站 Grant 暂时不可使用；
- 已签发 Token 是否立即失效取决于目标应用是否做在线状态检查；
- 使用短期 Token 时，最晚在几分钟后自然失效。

### 14.3 禁用目标应用

禁用目标应用与禁用调用应用语义不同。应阻止继续签发以该应用为 Audience 的 Token，但是否停止目标服务本身由部署和运行状态管理负责。

---

## 15. 错误响应建议

### 15.1 App 凭证错误

```http
HTTP/1.1 401 Unauthorized

{
  "error": "invalid_client"
}
```

不要分别返回“App 不存在”和“Secret 错误”，避免泄露已注册的 `app_id`。

### 15.2 申请未授权 Scope

```http
HTTP/1.1 400 Bad Request

{
  "error": "invalid_scope"
}
```

### 15.3 Audience、签名或 Token 类型错误

目标 API 返回：

```http
HTTP/1.1 401 Unauthorized
```

### 15.4 Token 有效但缺少接口 Scope

```http
HTTP/1.1 403 Forbidden

{
  "code": "insufficient_scope"
}
```

### 15.5 目标数据不符合业务规则

根据业务语义返回 `403`、`404`、`409` 或 `422`。不要用 Scope 校验替代资源归属、版本冲突和状态机校验。

---

## 16. 内部数据写入的附加要求

应用间写接口除了 Scope，还建议具备：

- `Idempotency-Key`，避免超时重试造成重复写入；
- 乐观锁版本号或 `If-Match`，防止覆盖并发修改；
- 明确的字段白名单；
- 调用应用级限流；
- 标准化错误码；
- `X-Request-ID` 和分布式追踪；
- 应用级审计日志；
- HTTPS 或可信网络内的 mTLS；
- Token、Secret 和敏感请求体日志脱敏。

幂等键的唯一范围建议为：

```text
(caller_app_id, endpoint_or_operation, idempotency_key)
```

---

## 17. 审计要求

每次重要的应用间读写至少记录：

```text
caller_app_id
resource_app_id
token_jti
scope
HTTP method
route template
target resource type / id
result
request_id
idempotency_key（写操作）
source_ip
created_at
```

禁止记录：

- 明文 `app_secret`；
- `app_secret_hash`；
- 完整 Service Token；
- 业务敏感字段的完整内容。

当前 main 的审计模型主要以用户为 Actor。应用间通信落地时，建议支持：

```text
actor_type = user | app | system
actor_user_id = nullable
actor_app_id = nullable
```

---

## 18. 不推荐的设计

### 18.1 所有应用共用一个 Secret

无法区分真实调用方，也无法对单个应用独立禁用、限流和审计。

### 18.2 在每个业务请求中发送长期 App Secret

`app_id + app_secret` 应只用于 Token Endpoint。业务接口使用短期 Service Token，减少长期凭证暴露面。

### 18.3 只传 `X-App-ID`

普通 Header 不能证明调用方身份，任何客户端都可以伪造。

### 18.4 只校验 Scope，不校验 Audience

签发给 CRM 的合法 Token 不能拿来调用 JCC。目标应用必须同时检查 `aud + scope`。

### 18.5 Token Endpoint 原样接受客户端提交的 Scope

申请 Scope 必须是有效 Grant 的子集，不能由调用方自行扩权。

### 18.6 自动配置双向权限

`main → JCC` 获得读取权限，不代表 `JCC → main` 自动获得任何权限。

### 18.7 权限自动传递

A 可以调用 B、B 可以调用 C，不代表 A 可以调用 C。

### 18.8 main 直接连接 JCC 数据库

应用间数据读写应经过目标应用的内部 API，使目标应用保留权限、校验、脱敏、审计和数据模型所有权。

---

## 19. 当前项目的最小落地方案

### 19.1 main 侧

1. 复用现有 App 的 `app_id`、`app_secret_hash` 和 `is_enabled`；
2. 将 main 自身也注册为一个应用身份，例如 `app_main`；
3. 新增 `resource_scopes`；
4. 新增 `app_service_grants`；
5. 新增 Client Credentials Token Endpoint；
6. 新增专门的 `ServiceTokenService`，与用户 Token 逻辑分离；
7. Service Token 使用独立 `token_use=service` 和目标 Audience；
8. Token 有效期控制在 3～5 分钟；
9. 管理端支持 Grant 创建、禁用、撤销和审计；
10. 每个 App 只维护一个 `app_secret_hash`，不新增独立凭证表。

### 19.2 JCC 侧

1. 新增 `/internal/v1/*` 应用间 API；
2. 新增独立的 `ServicePrincipal` 和 Service Token 验证依赖；
3. 现有用户鉴权依赖与服务鉴权依赖保持分离；
4. 严格验证 `iss`、`aud=app_jcc`、`token_use=service`、时间和 Scope；
5. 每个接口显式声明所需 Scope；
6. 从 Token 的 `sub` 获取真实调用应用；
7. 根据调用应用执行数据归属和字段范围限制；
8. 写操作增加幂等、并发控制和审计；
9. 不保存其他应用的明文 App Secret；
10. 只信任 main 签发 Service Token 的配置公钥。

JCC 当前的认证依赖主要处理用户 JWT，可参考 [JCC 当前鉴权依赖](../app/deps/auth.py)。Service Token 不应直接塞入该用户认证函数，而应新增独立模块，避免用户 Token 与 Service Token 混用。

---

## 20. 推荐的签发与校验伪代码

### 20.1 Token Endpoint

```python
caller = authenticate_app(app_id, app_secret)
if not caller.is_enabled:
    raise InvalidClientError()

target = get_enabled_application(requested_audience)
allowed_scopes = get_granted_scopes(
    caller_app_id=caller.id,
    target_app_id=target.id,
)

if not requested_scopes.issubset(allowed_scopes):
    raise InvalidScopeError()

service_token = issue_service_token(
    subject=caller.app_id,
    audience=target.app_id,
    scopes=requested_scopes,
    expires_in_seconds=300,
)
```

### 20.2 目标 API

```python
principal = verify_service_token(
    token,
    expected_issuer="tsuz-api-main",
    expected_audience="app_jcc",
    expected_token_use="service",
)

require_scope(principal, "jcc:record:read")

records = repository.list_for_caller(
    caller_app_id=principal.app_id,
)
```

---

## 21. 测试建议

### 21.1 Token Endpoint

覆盖：

- 正确的 `app_id + app_secret`；
- 不存在的 App；
- 错误 Secret；
- 禁用 App；
- 过期或撤销凭证；
- 不存在的 Audience；
- 禁用的目标应用；
- Scope 完整获批；
- 部分 Scope 未授权时拒绝整个请求；
- Token 的 `sub/aud/token_use/scope/exp/jti` 正确；
- 响应包含 `Cache-Control: no-store`；
- 日志和响应不泄露 Secret。

### 21.2 目标应用验证

覆盖：

- 有效签名和正确 Audience；
- 错误签名；
- 错误 Issuer；
- 错误 Audience；
- 错误 Token 类型；
- 过期和未生效 Token；
- 缺少所需 Scope；
- 一个目标应用的 Token 不能重放到另一个应用；
- `read` Token 不能调用 `write/delete` 接口；
- `own` Scope 只能访问调用方所属数据；
- 禁用 Grant 后不能申请新 Token。

### 21.3 契约与集成测试

至少建立 main 与 JCC 的契约测试：

- Service Token Claim 格式；
- Audience 名称；
- Scope 名称和语义；
- Service Token 公钥配置；
- 错误响应；
- Request ID 和幂等键；
- Token 过期和时钟偏差容忍。

---

## 22. 推荐实施顺序

### 阶段一：最小闭环

1. 注册 `app_main` 和 `app_jcc`；
2. 定义第一批目标应用 Scope；
3. 建立 `app_service_grants`；
4. 实现 Client Credentials Token Endpoint；
5. 签发 5 分钟 Service Token；
6. JCC 实现独立 Service Token 验证；
7. 选一个只读内部接口完成端到端验证。

### 阶段二：数据写入

1. 增加写 Scope；
2. 引入 `Idempotency-Key`；
3. 增加乐观锁或 `If-Match`；
4. 补齐应用 Actor 审计；
5. 增加按 App 的限流和监控。

### 阶段三：撤销与权限治理强化

1. 根据即时撤销需求增加 Redis 应用状态检查或 Token Introspection；
2. 对高权限 Scope 增加审批和告警；
3. 完善 Grant 有效期、撤销原因和变更审计；
4. 增加异常调用、越权申请和高频失败告警。

---

## 23. 最终模型

main 调用 JCC：

```text
app_main + secret_main
        │
        │ 认证调用方并检查 Grant
        ▼
Service Token
  sub   = app_main
  aud   = app_jcc
  scope = jcc:record:read
        │
        ▼
JCC /internal/v1/*
```

JCC 调用 main：

```text
app_jcc + secret_jcc
        │
        │ 认证调用方并检查 Grant
        ▼
Service Token
  sub   = app_jcc
  aud   = app_main
  scope = main:event:write
        │
        ▼
main /internal/v1/*
```

归纳如下：

```text
app_id + app_secret
  决定：你是哪一个应用

caller → target → scope
  决定：该应用能对哪个应用做什么

Service Token
  表示：这项权限在短时间内已由统一认证中心批准

目标应用本地业务规则
  决定：这次具体数据操作最终是否允许
```
