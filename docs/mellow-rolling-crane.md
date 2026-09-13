# Context

`tsuz-api-main` 已在本机通过 Docker 启动 PostgreSQL（`127.0.0.1:15432`）和 Redis（`127.0.0.1:16379`），API 则在宿主机启动。`tsuz-api-jcc` 当前的环境示例仍使用容器 DNS 名称，基础设施 Compose 也使用默认端口和泛化名称；同时代码只有一个 Redis 客户端，导致 main 写入的 access-token 黑名单与 session 撤销状态、以及 JCC 自己未来的缓存/业务状态混在同一连接职责中。

本次仅改造本地开发路径：JCC 使用独立 Docker PostgreSQL 和独立 Docker Redis，API 在本地启动；认证状态继续从 main Redis 读取。已确认采用两个 Redis 容器/实例，JCC PostgreSQL 与 Redis 分别暴露 `15433`、`16380`，main Redis 保持 `16379`，且 blacklist 与 session 都以 main Redis 为唯一认证状态源。部署模板与 GitHub Actions 本次不调整。

## Implementation

1. **补齐宿主机本地环境配置**
   - 更新被忽略的本地 [`.env`](../.env)，使 JCC API 可直接在宿主机运行：
     - `DATABASE_URL` 指向 `127.0.0.1:15433`；
     - `REDIS_URL` 指向 JCC Redis `127.0.0.1:16380/0`；
     - 新增 `MAIN_REDIS_URL=redis://127.0.0.1:16379/0`；
     - 使用唯一的 JCC 容器名、数据库名/用户、Compose project/network 名与 `POSTGRES_PORT=15433`、`REDIS_PORT=16380`；
     - JWT public key、issuer/audience、`TOKEN_BLACKLIST_PREFIX` 和 `SESSION_PREFIX` 与 main 当前 test 配置保持一致，但不复制 main 的私钥或部署密钥。
   - 更新已提交的 [`.env.test.example`](../.env.test.example)，以无真实密钥的占位值记录相同的本地拓扑，供后续复制/对照。

2. **让 JCC 基础设施 Compose 只启动自己的 PostgreSQL 与 Redis**
   - 调整 [`docker-compose.infra.yml`](../docker-compose.infra.yml)：保留现有 PostgreSQL/Redis 持久卷模式，改用 JCC 专属默认容器名和端口，并增加 PostgreSQL `pg_isready` 与 Redis `PING` healthcheck。
   - 保持该 Compose 不启动 API，也不再创建 main Redis；main Redis 由 `tsuz-api-main` 的 infra Compose 负责。JCC Compose 只负责 `jcc-postgres-test` 与 `jcc-redis-test`，避免生命周期和数据卷耦合。
   - 使用 JCC 自己的 Compose project/network，避免与 main 的容器、网络、卷重名；由于 API 在宿主机运行，不依赖容器 DNS 或共享 Docker 网络。

3. **拆分应用 Redis 客户端职责**
   - 在 [`app/core/config.py`](../app/core/config.py) 增加 `main_redis_url` 设置，保留 `redis_url` 作为 JCC 自有 Redis 连接。
   - 在 [`app/core/redis.py`](../app/core/redis.py) 保留 `get_redis()`（JCC 自有 Redis），新增独立缓存的 `get_main_redis()`（main 认证状态 Redis）。
   - 修改 [`app/services/blacklist_service.py`](../app/services/blacklist_service.py) 与 [`app/services/session_service.py`](../app/services/session_service.py)，让 access-token blacklist 与 session revoke 的读写都走 `get_main_redis()`；这是对现有 main key 格式和服务接口的复用，不改变鉴权调用链。JCC 自有 `get_redis()` 留给后续 JCC 缓存/业务状态，避免认证状态写入错误实例。

4. **更新测试与开发文档**
   - 调整 [`tests/test_redis_state_services.py`](../tests/test_redis_state_services.py)，分别替换/断言 main Redis 客户端，验证 blacklist 与 session 均使用认证状态源，并增加两个 Redis URL 生成独立客户端的覆盖，防止以后退回单连接。
   - 更新 [`README.md`](../README.md) 的本地启动步骤、环境变量说明与 Redis 架构说明：先启动 main infra（若尚未运行），再用 JCC `.env` 启动自身 infra，运行迁移/seed，最后 `pdm run dev`；明确 `REDIS_URL` 是 JCC 自有状态，`MAIN_REDIS_URL` 是 main 的 blacklist/session 状态。

## Verification

1. 运行 `docker compose --env-file .env -f docker-compose.infra.yml config`，确认仅生成 JCC PostgreSQL/Redis，宿主端口为 `15433`/`16380`，名称和卷不与 main 冲突。
2. 运行 `pdm run lint` 与 `pdm run test`，确认配置、双 Redis 客户端、blacklist/session 路由及现有 API 测试通过。
3. 启动 JCC infra 后检查两个容器 health；分别对 `127.0.0.1:16379`（main）和 `127.0.0.1:16380`（JCC）执行 `PING`，并验证 JCC PostgreSQL `15433` 可连接。
4. 运行 `pdm run migrate`、`pdm run seed` 和 `pdm run dev`，访问 `/health`。
5. 端到端验证 Redis 隔离：在 main Redis 写入符合 `TOKEN_BLACKLIST_PREFIX` 或 `SESSION_PREFIX` 的测试标记后，JCC 受保护接口返回 401；清除标记后恢复成功，同时 JCC Redis 中不存在该认证标记。
