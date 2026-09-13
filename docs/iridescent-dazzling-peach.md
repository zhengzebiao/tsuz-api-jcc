# Context

JCC 当前 GitHub Actions 仍是旧版部署方案：CI 监听 `master`，Deploy 在 GitHub Runner 构建镜像，未显式复用 Compose project，且远程运行时环境没有完整注入 JCC 的跨服务配置。用户已确定 JCC 与主应用共用 Docker 网络 `tsuz-api-main-test`，并要求 Deploy 按 `tsuz-api-main` 的发布流程自动执行 Alembic、幂等 seed 和 `report-permissions`。同时补齐默认权限目录中的 `jcc:record:read`。

目标是复用主应用的发布安全机制和服务器本地构建流程，但保持 JCC 只验证 JWT、不持有 `JWT_PRIVATE_KEY`，并保留 JCC 自己的 PostgreSQL/Redis、数据配置和 smoke test。

# Scope and constraints

- 修改 GitHub Actions、Compose/环境示例、权限目录、测试和相关部署文档；不改 JCC 业务 API 或认证实现。
- CI 与发布基线统一使用仓库默认分支 `main`。
- Deploy 继续支持 immutable `test-vX.Y.Z` / `product-vX.Y.Z` 发布及 workflow_dispatch 回滚。
- 测试环境网络固定为主应用已创建的 `tsuz-api-main-test`；生产使用对应主应用生产网络（默认 `tsuz-api-main-prod`，以 GitHub Environment Variable 覆盖），JCC 不创建或删除共享网络。
- JCC 基础设施容器和数据卷保持独立（例如 `jcc-postgres-test`、`jcc-redis-test`）；不得把主应用私钥、邮件配置或 `sync-jcc-data` 混入 JCC Deploy。
- 正常 immutable release 自动执行 `alembic upgrade head`、`python -m app.seed`、`python -m scripts.report_permissions`；回滚只拉取历史镜像并启动，不重复执行这些初始化副作用命令。

# Implementation plan

1. **补齐权限目录与测试**
   - 在 `scripts/report_permissions.py` 的 `DEFAULT_PERMISSIONS` 增加 `jcc:record:read`，保持现有 `--permissions` JSON 覆盖参数和 `MainClient.report_permissions()` 上报接口不变。
   - 新增定向测试，通过 mock `MainClient` 验证默认执行上报三个 JCC 权限，并保留自定义 JSON 输入行为。

2. **迁移 CI 到主应用的基线**
   - 更新 `.github/workflows/ci.yml` 监听 `main`，使用 JCC 测试数据库和专属镜像名；保留 `JWT_PUBLIC_KEY` 而不加入私钥、PDM install、lint、pytest、Alembic current 和 Docker build。

3. **重构 Deploy workflow**
   - 以 `tsuz-api-main/.github/workflows/deploy.yml` 的并发控制、`resolve-deploy`、严格 tag/commit 校验、Compose project、SSH 校验、服务器 checkout/build/push、immutable 回滚、健康检查为基线。
   - 适配 JCC 镜像名、JCC 容器/端口变量；测试部署网络默认/示例为 `tsuz-api-main-test`，生产由 product environment 配置对应网络。
   - 生成远程 `.env` 时完整注入 JCC 运行所需配置：JCC 数据库、JCC Redis、`MAIN_REDIS_URL`、JWT 公钥、Service Token 公钥/issuer/audience、JCC/Main App ID 与 Secret、Main token/API 地址、JCC 数据同步参数、Redis key 前缀、CORS、日志和运行时参数；绝不生成 `JWT_PRIVATE_KEY`。
   - 发布服务器在启动 API 前使用同一个 Compose project 执行：
     1. `alembic upgrade head`；
     2. `python -m app.seed`；
     3. `python -m scripts.report_permissions`。
     三步任一步失败都阻止发布。凭证通过远程受保护的 `.env`/进程环境传递，避免在日志中打印 secret。
   - `report-permissions` 依赖主应用已启动、主应用 migration/grant 已就绪、共享网络可达以及 JCC/Main App 凭证完整；不把主应用 admin seed、permission sync、邮件/Tencent 配置复制过来。
   - 保留 JCC smoke test（`/health` 200、未认证 `/api/profile` 401、`X-Request-ID` 透传），不要复制主应用登录 smoke test。
   - 回滚路径只登录 registry、拉取历史 JCC 镜像、更新 Compose 并做健康/smoke 检查；明确跳过 bootstrap，避免旧镜像重复改数据库或上报权限。

4. **统一 Compose 与基础设施网络**
   - 将 `docker-compose.infra.yml` 的网络声明改为 `external: true`，名称取 `DOCKER_NETWORK_NAME`，示例测试值为已存在的 `tsuz-api-main-test`；保留 JCC 独立容器和 volume。
   - 确保 `docker-compose.deploy.yml` 与 Deploy workflow 使用同一 `COMPOSE_PROJECT_NAME`、`DOCKER_NETWORK_NAME`；所有远程 Compose 命令显式带 `-p`。
   - 新增 `.github/workflows/init.yml`，参考主应用 Init，但只校验共享网络已由主应用创建，再启动/验证 JCC PostgreSQL 和 Redis；不得创建同名网络或操作主应用资源。

5. **同步 Migrate、环境示例和文档**
   - 更新 `.github/workflows/migrate.yml` 使用 `COMPOSE_PROJECT_NAME`、共享网络和统一 Compose 命令，保留 revision 字符校验及 product 备份确认；它作为指定 revision/故障恢复入口，正常 release 则由 Deploy 自动 migration。
   - 更新 `.env.deploy.example`、`.env.test.example`、`.env.product.example` 的容器内服务名、共享网络名、完整 JCC/Main 连接配置及 GitHub Variables/Secrets 说明；测试示例明确 `tsuz-api-main-test`，主应用 API/Redis 使用共享网络中的实际服务名。
   - 更新 `README.md` 的发布顺序、自动 bootstrap、共享网络、Init、回滚和必需 secrets 说明，移除与自动 Deploy 冲突的“Product 不自动 seed/仅手动 migrate”描述；明确 `sync-jcc-data` 不属于发布流程。

# Verification

- 运行 YAML 解析/静态检查（优先 `actionlint`；若环境没有则使用 Python YAML parser，并记录限制）。
- 运行权限目录定向 pytest，再运行 `pdm run lint`、`pdm run test` 和 `pdm run alembic-current`。
- 使用 `docker compose --env-file ... -f docker-compose.infra.yml config` 与 `docker-compose.deploy.yml config` 验证变量替换、external network、JCC 服务名和 Compose project；确认 JCC Compose 不会创建/删除主应用网络或容器。
- 静态检查正常发布顺序为 migration → seed → permission report → API 启动，回滚不执行 bootstrap，且 workflow 与环境文件不包含 `JWT_PRIVATE_KEY`、主应用专属 seed 或 `sync-jcc-data`。
- 不在本地真实触发 GitHub Actions、生产 migration、外部主应用 permission report 或远程部署；这些属于有副作用操作，执行记录中明确区分已验证和待远端验证项。
