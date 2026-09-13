# 应用间权限管理：第一阶段“最小只读闭环”执行记录

> 状态：部分完成
>
> 执行日期：2026-09-11
>
> 总实施方案：[main 总实施方案](../../tsuz-api-main/plan/APP_TO_APP_PERMISSION_MANAGEMENT_IMPLEMENTATION_PLAN.md)
>
> 阶段实现计划：[APP_TO_APP_PERMISSION_MANAGEMENT_IMPLEMENTATION_PHASE_1_PLAN.md](APP_TO_APP_PERMISSION_MANAGEMENT_IMPLEMENTATION_PHASE_1_PLAN.md)

## 1. 执行范围与结论

JCC 侧已完成独立 Service Auth、内部 records 只读资源、调用 main 的 Service Client、运行时 `httpx`、配置示例、Basic 日志脱敏及测试。main 侧对应的签发、Scope/Grant 和内部 App API 已在相邻仓库实现。

阶段结论：JCC 全量测试、阶段新增文件定向 lint、锁文件和 diff 检查通过。JCC 全仓既有 lint 问题不处理，本阶段及后续阶段均忽略。隔离跨服务 HTTP smoke 与 PostgreSQL migration round-trip 未执行，因此阶段为“部分完成”。

## 2. 实际代码与配置变更

### 2.1 独立 Service Auth

- [service_auth.py](../app/deps/service_auth.py)：新增 `ServicePrincipal`、`ServiceBearer`、固定 RS256、issuer、严格单值 audience、`token_use=service`、时间/必需 claim 类型和 Scope 校验；
- Service Token 不读取用户 blacklist/session，不复用 [auth.py](../app/deps/auth.py)；
- 缺失/无效 Token 固定 401，Token 有效但缺接口 Scope 固定 403。

### 2.2 内部 records API

- [internal.py](../app/api/internal.py)：新增 `GET /internal/v1/records`，固定要求 `jcc:record:read`；
- [internal.py](../app/schemas/internal.py)：响应只允许 `id/slug/display_name/is_active`；
- 查询仅返回 active `SampleProfile`，按 ID 稳定排序；
- [main.py](../app/main.py)：注册内部 Router，原 `/api/profile` 不变。

### 2.3 Main Client

- [main_client.py](../app/clients/main_client.py)：使用 `JCC_APP_ID/JCC_APP_SECRET` 请求 main `POST /internal/oauth/token`，audience 为 `MAIN_APP_ID`，scope 为 `main:application:read`；
- Basic 只用于 token endpoint，资源请求使用 Bearer；
- 短期 Token 只缓存在进程内，并提前 30 秒刷新；
- HTTP timeout 显式配置，认证/请求失败转换为固定且不含 Secret/Token 的异常。

### 2.4 配置、依赖和日志

- [config.py](../app/core/config.py)：新增 Service Token issuer/audience/public key、JCC 凭证、main App ID、token/API URL 和 timeout；
- [.env.test.example](../.env.test.example)、[.env.product.example](../.env.product.example)、[.env.deploy.example](../.env.deploy.example)：只写占位符和注入说明，不含真实 Secret/Token/私钥；
- [pyproject.toml](../pyproject.toml)、[pdm.lock](../pdm.lock)：`httpx` 提升为运行时依赖；
- [logging.py](../app/core/logging.py)：新增 Basic Authorization 脱敏；
- [README.md](../README.md)：记录双向 Grant、服务端/客户端职责和环境变量。

## 3. 关键行为与安全边界

```text
main 签发 Service Token
  → JCC 验证 iss/aud/token_use/iat/nbf/exp/sub/jti/scope
  → require jcc:record:read
  → 查询 active SampleProfile
  → 返回字段白名单
```

JCC 不配置 `JWT_PRIVATE_KEY` 或任何 Service Token 私钥。用户 JWT 即使使用相同测试 RSA key，也因缺少 `token_use=service` 等 claim 而不能访问内部 API。Caller 只来自签名 Token 的 `sub`，不信任 `X-App-ID`。

## 4. 测试与验证结果

| 检查 | 命令 | 结果 | 说明 |
| --- | --- | --- | --- |
| 中断恢复命令 | `pdm run ruff check tests/test_internal_api.py tests/test_main_client.py && pdm run pytest -q` | 通过 | 初次恢复：46 passed, 2 third-party warnings |
| 补充 Basic 脱敏后全量测试 | `pdm run pytest -q` | 通过 | 47 passed, 2 third-party warnings |
| 阶段新增/修改测试 lint | `pdm run ruff check tests/test_internal_api.py tests/test_main_client.py tests/test_logging.py` | 通过 | All checks passed |
| JCC 全仓 lint | 不执行 | 忽略 | 既有问题不处理，后续阶段也不再检查 |
| 锁文件 | `pdm lock --check` | 通过 | 依赖与 lock 一致 |
| Diff | `git diff --check` | 通过 | 无空白错误 |
| 双向 HTTP smoke | 隔离 main/JCC PostgreSQL、Redis、随机端口 | 未执行 | 动态资源清理脚本写入被安全守卫拒绝，且未授权连接共享资源 |

## 5. 阶段验收映射

| 验收标准 | 结果 | 证据 |
| --- | --- | --- |
| 正确 Service Token 可读取 records | 通过（测试客户端） | [test_internal_api.py](../tests/test_internal_api.py) |
| 错误签名/issuer/audience/token_use/时间/claim 返回 401 | 通过 | [test_internal_api.py](../tests/test_internal_api.py) |
| 缺 `jcc:record:read` 返回 403 | 通过 | [test_internal_api.py](../tests/test_internal_api.py) |
| 用户 Token 不能访问内部 API | 通过 | [test_internal_api.py](../tests/test_internal_api.py) |
| 仅返回 active 安全字段并传播 Request ID | 通过 | [test_internal_api.py](../tests/test_internal_api.py) |
| Main Client Basic/token cache/固定错误安全 | 通过 | [test_main_client.py](../tests/test_main_client.py) |
| 原用户 `/api/profile`、Redis blacklist/session 无回归 | 通过 | JCC 全量 47 passed |
| 真实双向跨进程 HTTP 链路 | 待环境验证 | 本次未执行 |
| JCC 全仓 lint | 忽略 | 既有问题不处理，后续阶段也不再检查 |

## 6. 与计划差异及遗留问题

| 项目 | 实际情况 | 处理 |
| --- | --- | --- |
| 隔离 smoke | 未执行 | 需要明确授权动态创建/删除随机临时数据库和 Redis namespace，禁止退回共享/生产资源 |
| JCC 数据迁移 | 无新增迁移 | records 复用现有 `sample_profiles`，符合阶段计划 |

## 7. 文档同步

- [设计文档](APP_TO_APP_PERMISSION_MANAGEMENT.md)：增加第一阶段状态、计划和执行记录链接；
- [阶段计划](APP_TO_APP_PERMISSION_MANAGEMENT_IMPLEMENTATION_PHASE_1_PLAN.md)：标记部分完成并记录真实验证结果/遗留项；
- [main 总方案](../../tsuz-api-main/plan/APP_TO_APP_PERMISSION_MANAGEMENT_IMPLEMENTATION_PLAN.md)、[main 阶段计划](../../tsuz-api-main/plan/APP_TO_APP_PERMISSION_MANAGEMENT_IMPLEMENTATION_PHASE_1_PLAN.md)、[main 执行记录](../../tsuz-api-main/plan/APP_TO_APP_PERMISSION_MANAGEMENT_IMPLEMENTATION_PHASE_1_EXECUTION.md)：记录跨仓实现和相同的部分完成结论；

## 8. 阶段结论

JCC 第一阶段代码和默认自动化验证已完成，用户鉴权路径未回归。JCC 全仓既有 lint 问题不处理，后续阶段也忽略。由于隔离 migration/双向 HTTP smoke 尚未执行，阶段保持“部分完成”。
