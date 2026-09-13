# JCC 金铲铲官方资料定时同步实施方案


> 状态：废弃⚠️，不进入实现
>
> 本方案基于当前 `tsuz-api-jcc` 的 FastAPI、PDM、Docker Compose 和官方 JSON 原始快照实现，结合 [RAG 数据设计](jcc-ai-agent-rag-data-design.md)、现有同步脚本与当前部署文件制定。
>
> 本方案只规划第一版“`.env` 开关 + 独立定时 Worker + 版本更新后原子保存原始快照”，不实现 API 开关、数据库状态管理、结构化入库或向量化。

## 1. 已确认业务配置与关键决策

| 项目 | 决策或配置 | 状态/来源 |
| --- | --- | --- |
| 同步触发方式 | 由独立 Worker 长期运行，启动后立即检测，之后每 3 小时检测一次 | 已确认；用户当前需求 |
| 调度间隔 | `LOL_DATA_SYNC_INTERVAL_SECONDS=10800`，默认值为 10800 秒 | 已确认；用户当前需求 |
| 启停控制 | 只通过 `.env`/部署环境变量控制，不提供 HTTP API 或管理页面 | 已确认；用户当前需求 |
| Worker 与后端关系 | Worker 与后端作为同一部署编排中的两个进程/服务启动；不在 FastAPI startup、Gunicorn worker 或请求进程内创建循环 | 已确认的实现原则 |
| 默认开关 | `LOL_DATA_SYNC_ENABLED=false`，部署或本地明确启用时设置为 `true` | 合理默认；安全启动策略 |
| 版本范围 | 继续只同步官方配置中唯一的最新“自然之力”版本 | 已确认；现有脚本和官方数据契约 |
| 数据形态 | 保存官方原始 JSON 和 manifest，按 `<version>-<season>` 保留历史目录 | 已确认；现有快照和 [RAG 数据设计](jcc-ai-agent-rag-data-design.md) |
| 更新判定 | 每轮读取官方 `versiondataconfig.js`；目标版本目录完整则跳过，发现新版本则下载并发布 | 已确认；现有 `sync_latest()` 行为 |
| 外部调度器 | 生产优先使用 Docker Compose/service manager 启动常驻 Worker；不同时配置 cron 和常驻 Worker | 推荐默认；需要按部署环境落地 |
| 真实 CDN 验证 | 不纳入普通 CI，使用临时目录进行受控人工验证 | 已确认的安全边界 |

## 2. 背景与现状

### 2.1 背景

当前仓库需要持续保存金铲铲官方资料，供后续结构化处理、全文检索和 Embedding 使用。官方资料以 CDN JSON 形式发布，版本变化时需要下载一组相互关联的文件；如果只覆盖当前文件，无法追溯历史版本，也可能在部分下载成功时产生混合版本数据。

目标是把现有的一次性同步命令扩展为一个可被部署系统托管的常驻 Worker：后端服务启动时同时启动 Worker，Worker 由 `.env` 控制是否执行，每 3 小时检查一次版本，发现版本变化后完整下载并原子发布。开关关闭时不删除已有快照、不请求 CDN。现有 [RAG 数据设计](jcc-ai-agent-rag-data-design.md) 对同步频率给出 30～60 分钟的通用建议；本计划按用户当前确认采用 3 小时作为第一版运行配置，不改变其原始快照、后续结构化处理和向量化分层原则。

### 2.2 当前架构

已核对的相关仓库事实：

- [scripts/sync_lol_data.py](../scripts/sync_lol_data.py) 已有一次性 `sync_latest()`，负责版本发现、官方资源下载、元数据校验、临时目录写入、原子发布和 `fcntl.flock` 单实例锁；当前 CLI 支持 `--raw-dir`、`--lock-file`、`--timeout` 和 `--retries`。
- [pyproject.toml](../pyproject.toml) 已注册 `sync-lol-data = "python scripts/sync_lol_data.py"`；项目已有 `pydantic-settings`，可以复用其 `.env` 读取和类型校验能力，不新增第三方依赖。
- [tests/test_sync_lol_data.py](../tests/test_sync_lol_data.py) 已覆盖一次性同步成功、完整快照幂等跳过、配置歧义、元数据不匹配、阵容结构错误、失败清理、网络重试和锁冲突。
- [raw/](../raw/) 按 `<version>-<season>` 保存快照，快照包含 `versiondataconfig.json`、8 个基础资料 JSON、`lineup_detail_total.json` 和 `manifest.json`；锁文件与临时目录已在 [.gitignore](../.gitignore) 中忽略。
- [.env.test.example](../.env.test.example)、[.env.product.example](../.env.product.example) 和 [.env.deploy.example](../.env.deploy.example) 已有应用运行配置，但尚未包含资料同步配置。
- [docker-compose.deploy.yml](../docker-compose.deploy.yml) 当前编排 `api` 和 `nginx`，尚未编排资料同步 Worker；[Dockerfile](../Dockerfile) 已将 `scripts/` 复制到镜像，Worker 可以复用同一镜像。
- 当前本地推荐流程是 API 在宿主机运行、`docker-compose.infra.yml` 只运行 PostgreSQL/Redis；该基础设施 Compose 不应因为本功能变成应用进程编排文件。
- 当前工作区在本方案编写前已经存在同步脚本、测试、README、PDM 和忽略规则改动，并可见新的 `raw/18.18.2-S19/` 快照目录；实施时必须保留这些已有改动，不能通过清理工作区或重置快照来实现本方案。

### 2.3 现状差距

当前 `sync_latest()` 只能由外部反复启动，不能从 `.env` 读取启用状态和间隔，也没有常驻循环、优雅停止以及启动时与应用编排并行启动的定义。现有单次逻辑已经提供了数据一致性基础，本方案只在其外层增加配置和调度，不重写下载、校验及发布流程。

## 3. 目标与非目标

### 3.1 目标

本期完成：

1. 为同步 Worker 增加 `.env`/环境变量配置，默认每 10800 秒检查一次，启动后先执行一次检查；
2. 增加 `--watch` 常驻模式，复用现有 `sync_latest()`，关闭开关时不访问 CDN，失败后保留旧快照并等待下一轮；
3. 在 Docker Compose 部署中增加独立 `data-sync` 服务，与后端使用相同镜像、配置和持久化原始数据卷；
4. 补充测试、环境示例、运行文档、启停和回滚说明；
5. 保持原始 JSON、manifest、锁、临时目录、历史版本和现有用户 API 行为不变。

### 3.2 非目标

本期明确不实现：

- 任何 `/admin/data-sync`、状态查询、手动触发或 API 开关接口；
- 将同步开关、最近版本、错误信息写入 `AppSetting`、PostgreSQL、Redis 或新增同步状态表；
- 把定时循环放入 FastAPI lifespan/startup、Gunicorn worker、请求处理线程或应用后台任务；
- 结构化数据库入库、全文索引、Embedding、向量库和数据 API；
- 同一版本内基于内容哈希的增量下载或向量化；第一版只按官方当前版本及快照完整性判断；
- 图片下载、图片授权处理、官方资料字段改造和业务数据清洗；
- 多实例分布式调度、租约服务、队列、在线状态面板和跨主机锁；
- 通过 cron 与常驻 Worker 同时调度同一份数据目录；
- 生产环境真实 CDN 下载、部署发布或删除长期数据。

## 4. 需求与核心流程

### 4.1 参与者和使用场景

| 参与者 | 前置条件 | 操作 | 预期结果 |
| --- | --- | --- | --- |
| 部署运维 | `.env` 注入同步配置，持久化 raw 数据卷已准备 | 启动应用 Compose | `api` 与独立 `data-sync` 同时启动；Worker 按配置运行 |
| 本地开发者 | 已安装 PDM，配置 `.env` | 执行 `pdm run sync-lol-data-watch` | Worker 宿主机运行，不依赖 API 请求 |
| 部署运维 | 需要暂停同步 | 将 `LOL_DATA_SYNC_ENABLED` 改为 `false` 并重启/重新创建 Worker | Worker 保持空闲，不删除历史快照、不访问 CDN |
| Worker | 开关为 `true` | 启动或完成上一轮等待后读取版本配置 | 无更新则跳过；有新版本则下载、校验并原子发布 |
| Worker | CDN、JSON 或文件系统失败 | 当前轮失败 | 记录不含敏感信息的错误，旧快照继续保留，下一轮可重试 |

### 4.2 正常流程

```text
服务管理器启动 data-sync
        ↓
读取 .env / 环境变量并校验配置
        ↓
LOL_DATA_SYNC_ENABLED=false？
  ├─ 是：记录 disabled，保持空闲等待；修改 .env 后需重启 Worker
  └─ 否
        ↓
立即调用现有 sync_latest()
        ↓
读取 versiondataconfig.js，确定唯一当前版本
        ↓
当前 <version>-<season> 快照完整？
  ├─ 是：记录 skipped
  └─ 否：下载全部资源 → 校验 → 临时目录写入 → 原子发布
        ↓
等待 10800 秒（可由环境变量覆盖）
        ↓
重复检查，直到收到 SIGTERM/SIGINT
```

等待应从本轮处理完成后开始计算，使用可被停止信号唤醒的等待机制，避免同步进行时间过长时多个周期重叠。Worker 只在进程启动时读取一次环境配置；修改 `.env` 后必须重启或重新创建 Worker，不提供运行时 API 热切换。

### 4.3 异常与边界流程

- 配置文件缺失、布尔值/数字格式错误或间隔不合法：启动阶段 fail closed，输出安全错误并以非零状态退出，不请求 CDN。
- `LOL_DATA_SYNC_ENABLED=false`：Worker 不执行版本请求，保持进程可托管的空闲状态；不删除已有快照。为了避免 Compose `restart: unless-stopped` 产生重启空转，不在关闭状态主动退出。
- 当前版本目录已存在且完整：只读取版本配置和本地完整性信息，不重复下载资源。
- 同名版本目录存在但不完整：拒绝覆盖，记录失败；人工处理目录后再重试。
- 下载、解析、版本/赛季/模式校验或 manifest 写入失败：不发布目标目录，清理临时目录，旧版本保持可用。
- 另一进程持有锁：视为本轮 skipped，不执行第二次下载；部署中不应再配置另一个定时器。
- Worker 正在下载时收到停止信号：尽快结束当前阻塞请求并释放锁；若无法在请求超时内停止，服务管理器负责发送终止信号，残留临时目录下次启动时清理或忽略，不得替换已有目标目录。
- 同一版本官方内容在版本号不变时发生变化：第一版不做内容哈希重同步，保留现有快照行为，后续单独设计内容修订策略。

## 5. 当前架构适配与总体设计

### 5.1 设计原则

- 复用 [sync_latest()](../scripts/sync_lol_data.py) 作为唯一一次性同步入口，Watch 层只负责配置、循环、停止和错误隔离。
- 复用项目已有 `pydantic-settings` 读取 `.env`，不执行 `source .env`，避免多行公钥和特殊字符被 shell 错误解析；命令行显式参数优先于环境变量，环境变量优先于默认值。
- Worker 与 API 进程分离。Gunicorn 可以继续按 `WEB_CONCURRENCY` 扩容而不增加同步任务数量。
- 版本目录发布必须保持“全部下载和校验成功后再切换”；失败不能让 API 或后续处理看到混合版本。
- 原始快照是不可变历史输入；关闭同步只暂停未来更新，不删除历史数据。
- 第一版不引入数据库/Redis 状态，避免同步进程和应用状态耦合，也避免为简单轮询增加迁移和清理责任。

### 5.2 目标架构

```text
.env / Compose environment
          │
          ├──────────────→ FastAPI api（现有生命周期不变）
          │
          └──────────────→ data-sync Worker
                              │
                              ├─ 配置校验与 10800 秒调度
                              ├─ fcntl 单实例锁
                              ├─ 官方 CDN JSON
                              └─ 持久化 raw/<version>-<season>/ 快照
```

`api` 和 `data-sync` 使用同一镜像但使用不同进程入口：API 继续执行 Dockerfile 的 Gunicorn CMD，Worker 使用 `python scripts/sync_lol_data.py --watch`。两者不通过 HTTP 互相控制；数据目录通过 Compose 持久化卷保留。未来结构化入库可以作为新的独立 Consumer，不应把入库逻辑塞入本阶段 Worker。

### 5.3 兼容策略

- 现有 `pdm run sync-lol-data` 保持一次性执行语义，适合人工单轮同步和受控任务；新增 `pdm run sync-lol-data-watch` 不改变旧命令。
- 现有 `raw/<version>-<season>/` 目录和 manifest 字段不变；历史快照不迁移、不覆盖。
- 现有 FastAPI 路由、数据库、Redis、用户认证和 Service Token 不改变。
- Docker 应用回滚不删除 raw 持久化卷；回滚后的旧 Worker 仍应能读取和跳过已存在的完整目录。若后续数据格式发生不兼容，应先停止 Worker 并执行专项兼容评估，而不是在回滚时删除数据卷。
- `.env` 新变量均有默认值；未配置时同步默认关闭，避免旧部署在未审查前自动访问外部 CDN。

## 6. 接口与外部契约设计

本期不改变 HTTP/API 公共契约。新增的是本地命令和部署进程契约。

### 6.1 Worker 命令

```bash
pdm run sync-lol-data-watch
```

等价进程入口：

```bash
python scripts/sync_lol_data.py --watch
```

保留现有一次性命令：

```bash
pdm run sync-lol-data [--raw-dir PATH] [--lock-file PATH] [--timeout SECONDS] [--retries COUNT]
```

Watch 模式继续允许显式 CLI 参数覆盖配置文件中的目录、锁、超时和重试值；`--watch` 本身只改变生命周期，不改变一次同步的下载和发布规则。

### 6.2 进程结果和日志契约

- Watch 配置合法且进程持续运行时保持前台运行，由 Docker/systemd/launchd 负责重启和停止。
- 启动配置错误或不可恢复的文件系统错误返回非零状态；正常停止返回 0。
- 单轮 `updated`、`skipped`、`disabled` 和 `failed` 均输出结构化或稳定文本日志，至少包含状态、版本（如果已发现）和目标目录（如果适用）；不输出响应正文、完整请求头、Token、Secret 或环境文件。
- 锁竞争不是数据损坏，按 skipped 处理，不应触发失败告警；网络/校验失败应保留错误摘要并等待下一轮。

### 6.3 官方 CDN 契约

继续使用现有 [脚本中的官方 URL 和资源字段](../scripts/sync_lol_data.py)：

- `versiondataconfig.js` 用于版本发现；
- `chess`、`race`、`job`、`trait`、`equip`、`hex`、`adventure`、`galaxy` 共 8 个资源；
- `lineup_detail_total.json` 用于阵容资料；
- 请求使用固定 User-Agent、显式超时和有限重试；不需要认证凭证。

不把外部 CDN 当作事务资源：只有本地所有 payload 校验完成后才发布新目录，网络失败时继续保留最后一个成功快照。

## 7. 数据模型、迁移与状态设计

### 7.1 原始数据模型

不新增数据库表。原始文件布局保持：

```text
raw/
  <version>-<season>/
    versiondataconfig.json
    chess.json
    race.json
    job.json
    trait.json
    equip.json
    hex.json
    adventure.json
    galaxy.json
    lineup_detail_total.json
    manifest.json
```

`manifest.json` 继续记录模式、模式名称、赛季、版本、更新时间、来源 URL、文件名和记录数。目标目录名来自已校验的版本和赛季字段，并拒绝路径分隔符、控制字符等不安全组件。

### 7.2 迁移策略

不涉及 PostgreSQL/Alembic 迁移、历史数据回填或数据库 downgrade。实施前后 `pdm run alembic-current` 的结果应保持不变。

### 7.3 文件锁、临时状态和持久化卷

- 默认锁文件：`<raw-dir>/.sync.lock`；继续使用 `fcntl.flock`，文件本身加入 `.gitignore`。
- 临时目录：`<raw-dir>/.sync-<uuid>/`；必须在失败和正常发布后清理，清理失败要在日志中说明。
- 发布操作：临时目录与目标目录位于同一文件系统，使用原子目录改名；目标已存在时不覆盖。
- Docker `data-sync` 使用稳定命名的 raw 持久化卷，例如 `LOL_DATA_SYNC_VOLUME_NAME` 指定的卷；部署更新和应用回滚不得使用 `down -v` 删除它。
- 同一 raw 目录只允许一个常驻 Worker。若外部确需一次性命令，必须停止或协调常驻 Worker，不能依赖重复调度来实现高可用。

### 7.4 事务与并发

文件系统锁覆盖版本检查、下载、临时写入和发布全流程，避免两个进程同时下载同一版本。网络请求不放在数据库事务内，也不写共享数据库状态。新版本发布是一次目录级切换，读取方只能看到旧完整目录或新完整目录，不应看到下载中的临时文件。

## 8. 模块与服务拆分

### `scripts/sync_lol_data.py`

- 新增 `SyncSettings` 或等价配置加载函数，读取 `LOL_DATA_SYNC_*` 变量并校验布尔值、间隔、路径、超时和重试次数；复用已有标准库同步核心。
- 新增 `--watch` 参数和可测试的 `run_watch()`/等待抽象；使用 `threading.Event` 或等价停止信号机制响应 SIGTERM/SIGINT。
- 每轮只调用现有 `sync_latest()`；捕获可恢复同步异常后记录并进入下一轮，捕获配置/不可恢复错误后安全停止。
- 不负责 FastAPI 生命周期、API 权限、数据库状态、向量化和业务数据处理。

### `data-sync` Compose 服务

- 在 [docker-compose.deploy.yml](../docker-compose.deploy.yml) 增加与 `api` 使用相同镜像和 `.env` 的独立服务。
- 入口固定为 `python scripts/sync_lol_data.py --watch`，设置 `restart: unless-stopped`、优雅停止时间和 raw 持久化卷。
- 不依赖 API 健康状态才能开始版本检查；同步只依赖官方 CDN 和本地文件系统。若部署平台要求统一启动顺序，使用服务管理器编排，不从 API 进程派生子进程。

### 配置和运维文档

- 在环境示例、PDM 脚本、README 和部署说明中描述变量、启停、日志、持久化卷、首次下载和回滚行为。
- 不提交具体 launchd/systemd 主机路径、真实凭证或生产日志；部署平台可在仓库外维护 service unit。

## 9. 配置、依赖与外部服务

### 9.1 配置

建议新增以下配置，示例文件只写非敏感值：

```dotenv
# false 为安全默认值；明确部署时改为 true
LOL_DATA_SYNC_ENABLED=false
# 3 小时
LOL_DATA_SYNC_INTERVAL_SECONDS=10800
# 宿主机默认可使用 raw；容器建议使用 /app/raw
LOL_DATA_SYNC_RAW_DIR=raw
# 为空时默认使用 <raw-dir>/.sync.lock
# LOL_DATA_SYNC_LOCK_FILE=
LOL_DATA_SYNC_TIMEOUT_SECONDS=30
LOL_DATA_SYNC_RETRIES=2
```

规则：

- `.env`/环境变量在 Worker 启动时读取一次；命令行参数优先级高于环境变量，环境变量高于默认值。
- `LOL_DATA_SYNC_ENABLED=false` 时 Worker 不请求 CDN，只保持空闲；要切换开关需重启或重新创建 Worker，例如 `docker compose up -d --force-recreate data-sync`。
- `LOL_DATA_SYNC_INTERVAL_SECONDS` 必须为正数，建议拒绝低于 60 秒的配置，避免误配置造成 CDN 高频请求；默认值固定为 10800。
- 相对 `LOL_DATA_SYNC_RAW_DIR` 按进程工作目录解析；Docker 中明确设置为 `/app/raw`，宿主机 service unit 必须设置正确 `WorkingDirectory`。
- `LOL_DATA_SYNC_LOCK_FILE` 为空时自动落在 raw 目录内；自定义路径必须位于可写且受保护的本地目录。

### 9.2 依赖

- 复用现有 `pydantic-settings`，不新增第三方运行时依赖；不需要修改 `pdm.lock` 的依赖内容。
- 若实现保持配置解析为标准库，也不得引入 dotenv shell 执行方式；最终选择必须保留多行环境值和类型校验安全性。
- Dockerfile 已复制脚本目录，预计只需增加 Compose 入口，不改变 API 镜像启动命令。

### 9.3 外部服务契约

- 官方 CDN 为无认证 HTTPS 只读来源；使用当前固定 endpoint，不允许从 `.env` 任意替换为未经审查的下载地址。
- 单请求 timeout 默认 30 秒，临时网络/5xx/限流错误最多重试 2 次，采用已有退避逻辑；永久 HTTP 错误和 JSON/元信息错误立即结束当前轮。
- 普通 CI 使用 fake fetcher，不访问真实 CDN；真实下载只在明确授权、临时 raw 目录和可审计环境中执行。

## 10. 代码变更清单

### 配置和依赖

- [ ] [scripts/sync_lol_data.py](../scripts/sync_lol_data.py)：新增 `.env` 配置模型/加载、`--watch`、停止信号、定时等待和每轮错误隔离；保留现有单次入口。
- [ ] [pyproject.toml](../pyproject.toml)：新增 `sync-lol-data-watch = "python scripts/sync_lol_data.py --watch"`。
- [ ] [.env.test.example](../.env.test.example)、[.env.product.example](../.env.product.example)、[.env.deploy.example](../.env.deploy.example)：新增同步变量和启停说明，不写 Secret。
- [ ] [pdm.lock](../pdm.lock)：无依赖变化；实施后执行一致性检查。

### API、Schema 或公共契约

- 本期不修改 FastAPI 路由、Schema、OpenAPI、用户认证、Service Auth 或客户端接口。
- 新增的命令行 `--watch` 和 `LOL_DATA_SYNC_*` 是运维契约，不对外提供 HTTP API。

### 服务和领域逻辑

- [ ] [docker-compose.deploy.yml](../docker-compose.deploy.yml)：增加独立 `data-sync` 服务、稳定 raw 持久化卷、`restart` 和优雅停止配置；不改变 `api`/`nginx` 入口。
- [ ] [Dockerfile](../Dockerfile)：原则上不修改；只有镜像运行路径验证发现必要问题时才做最小兼容调整，并在执行记录说明。
- [ ] [docker-compose.infra.yml](../docker-compose.infra.yml)：不增加 Worker，保持只负责 JCC PostgreSQL/Redis 的既有边界。

### 模型和迁移

- 本期不新增模型、表、Alembic migration、Redis key、队列或应用状态记录。

### 测试和文档

- [ ] [tests/test_sync_lol_data.py](../tests/test_sync_lol_data.py)：增加配置读取、禁用不下载、启动立即执行、间隔等待、失败后继续、停止信号和 watch CLI 测试。
- [ ] [README.md](../README.md)：增加 Worker 启动、`.env` 开关、三小时周期、Docker Compose、宿主机 service manager、日志和回滚说明。
- [ ] 本方案对应的阶段实现计划和阶段执行记录：在进入实现阶段后创建，记录实际修改和真实验证结果。

## 11. 异常处理与可观测性

### 11.1 异常响应或错误契约

| 场景 | 外部结果 | 内部处理 |
| --- | --- | --- |
| 开关关闭 | Worker 不下载，进程保持空闲 | 记录 `disabled`，不创建新快照 |
| 配置格式错误 | Worker 启动失败，非零退出 | 输出变量名和约束，不输出 `.env` 内容 |
| 官方版本配置无唯一当前版本 | 当前轮失败，旧快照继续可用 | 记录安全错误，等待下一轮 |
| 单个资源 HTTP/网络失败 | 当前轮失败 | 有限重试后记录资源名称，清理临时状态 |
| JSON/版本/赛季/模式校验失败 | 当前轮失败 | 不发布目标目录，不覆盖旧目录 |
| 完整快照已存在 | 当前轮成功跳过 | 记录 `skipped`，不重复下载 |
| 锁被占用 | 当前轮成功跳过 | 记录锁竞争，不触发数据失败告警 |
| 持久化卷不可写 | Worker 轮询失败或启动失败 | fail closed，保留已有目录并告警 |
| SIGTERM/SIGINT | 进程按服务管理器约定退出 | 唤醒等待、释放锁、清理可清理临时目录 |

### 11.2 日志、指标与追踪

第一版不新增监控系统或指标依赖，但日志至少区分：`started`、`disabled`、`updated`、`skipped`、`failed`、`stopped`。建议记录：Worker 实例、版本、season、结果、耗时、错误类型和目标目录；禁止记录完整 payload、Authorization、Secret、私钥、环境文件和完整异常响应正文。

部署平台应基于进程退出、连续 `failed` 日志、raw 卷磁盘空间和最后成功版本建立告警。由于第一版不写状态数据库，跨重启的“最后一次成功时间”只能从日志和 manifest 推断，不在本期伪造状态 API。

## 12. 安全与权限要求

1. 同步开关只由部署文件/环境管理者修改；不接受普通 HTTP 请求或用户输入改变本地进程行为。
2. `.env` 仅保存非敏感同步参数；脚本不读取或打印无关的 JWT、数据库密码、App Secret 和 Service Token。
3. 下载 URL 继续由代码中的固定官方 endpoint 和已校验相对路径构造，不允许把环境变量直接变成任意 SSRF 地址。
4. 目标目录名、资源文件名和配置数值必须校验；不允许通过版本/赛季字段跳出 raw 根目录。
5. 原始文件写入使用临时目录和目录级原子发布；下载失败 fail closed，不以半成品替换旧数据。
6. 使用文件锁避免本机并发；部署上只启动一个 `data-sync` 服务，不通过增加 API worker 数量提高同步并发。
7. raw 持久化卷只授予 Worker 必要的读写权限；服务管理器日志和 Docker 日志不得包含 payload 或环境文件内容。
8. 关闭同步只暂停未来下载，不执行删除；删除 raw 卷必须作为单独、明确授权的运维操作，不能绑定到普通部署命令。
9. 第三方 CDN 故障采用 fail closed；不能为了继续运行而使用未校验 JSON、跳过版本元数据检查或回退到共享生产数据目录。

## 13. 测试与验收

### 13.1 单元测试

- 配置未设置时默认 `enabled=false`、间隔 10800、超时 30、重试 2；显式环境变量能正确覆盖；非法值被拒绝；
- `enabled=false` 的 watch 不调用 fake fetcher；
- `enabled=true` 启动后立即调用一次同步，再按注入的等待器等待，不真实睡眠 3 小时；
- 当前轮抛出网络/校验异常后 Worker 记录失败并进入下一轮，不退出或发布半成品；不可恢复配置错误在启动时退出；
- SIGTERM/SIGINT 能唤醒等待并释放资源；
- 已有一次性同步的成功、跳过、失败清理、版本校验、锁竞争测试全部保持通过；
- CLI 参数覆盖 `.env` 的目录、锁、timeout 和 retries，旧的一次性命令保持兼容。

### 13.2 集成与编排测试

使用临时目录和 fake fetcher，不连接共享数据库、Redis 或真实 CDN：

```bash
pdm run pytest tests/test_sync_lol_data.py
pdm run ruff check scripts/sync_lol_data.py tests/test_sync_lol_data.py
pdm run sync-lol-data --help
```

验证 Compose 文件：

```bash
docker compose --env-file .env.deploy.example -f docker-compose.deploy.yml config
```

检查 `data-sync` 使用正确镜像、`--watch` 入口、环境变量、raw 持久化卷和 restart 策略；确认 `docker-compose.infra.yml` 仍只包含基础设施服务。

### 13.3 回归与质量检查

```bash
pdm run test
pdm lock --check
git diff --check
```

`pdm run lint` 应至少对本期新增/修改文件执行定向检查。当前仓库已有若干非本期 lint 问题，实施记录必须分别列出基线问题与本期结果，不能把全仓失败伪装成通过，也不能为了本方案顺带修改无关旧文件。

### 13.4 真实环境验证

以下项目不进入普通 CI，需明确授权后执行：

- 用临时 raw 目录运行一次真实 CDN 单轮同步，确认当前官方版本、文件数量、manifest 和原始字节保存；
- 在隔离部署环境启动 API 与 `data-sync`，确认 Worker 只启动一个实例、三小时周期可通过缩短测试间隔验证；
- 修改部署 `.env` 为 `false` 并重新创建 Worker，确认不产生外部请求且历史快照保留；恢复为 `true` 后确认下一次启动立即检查；
- 执行应用镜像回滚演练，确认 raw 持久化卷未被删除，且 API/Worker 进程可恢复。

生产 CDN 下载、生产部署、生产卷清理和任何真实历史数据删除不属于本方案默认验收动作。

## 14. 部署、迁移与回滚检查清单

### 配置与 Secret

- [ ] 各环境已明确 `LOL_DATA_SYNC_ENABLED`；未审查环境保持 `false`；
- [ ] `LOL_DATA_SYNC_INTERVAL_SECONDS=10800` 或经批准的覆盖值已注入；
- [ ] `LOL_DATA_SYNC_RAW_DIR` 和锁路径位于可写、持久化且非共享临时目录；
- [ ] 没有把 JWT 私钥、App Secret、Token 或数据库密码写入同步配置和日志；
- [ ] `.env` 变更后已重新创建/重启 `data-sync`，没有误以为常驻进程会自动读取新值。

### 数据与基础设施

- [ ] `data-sync` 使用稳定 raw 持久化卷；
- [ ] 未使用 `docker compose down -v` 删除 raw 卷；
- [ ] 部署主机有足够磁盘空间，历史版本保留策略已确认；
- [ ] 同一 raw 目录没有额外 cron、launchd、systemd 或手工常驻 Worker；
- [ ] API、PostgreSQL、Redis 的既有生命周期不因本功能改变。

### 应用与兼容性

- [ ] 定向测试、全量测试、锁文件检查和 Compose config 检查通过；
- [ ] `api` 和 `data-sync` 使用同一兼容镜像；
- [ ] Worker 启停日志和失败告警已接入部署平台；
- [ ] 首次启用的下载量、CDN 限制和 raw 卷容量已评估；
- [ ] 后续结构化处理未被误配置为本 Worker 的隐式副作用。

### 回滚策略

1. 应用镜像回滚时保留 raw 持久化卷，不回滚或删除历史 JSON；
2. 如果旧 Worker 与当前 manifest 不兼容，先停止 `data-sync`，保留数据卷，使用兼容镜像或前向修复；
3. 回滚后检查 API、Worker 进程、raw 目录完整性和日志；
4. 禁用同步只修改 `LOL_DATA_SYNC_ENABLED=false` 并重新创建 Worker，不删除已有快照；
5. 只有单独批准的数据保留/清理操作才能删除历史 raw 卷。

本期无数据库迁移，因此没有 Alembic downgrade 或数据库回滚步骤。

## 15. 分阶段实施顺序

本模块分为两个阶段。每个阶段完成并验证后再进入下一阶段；阶段实现计划和执行记录在开始编码前按本总方案创建。

### 第一阶段：可配置定时同步 Worker

> 状态：未开始
>
> 阶段计划：待创建
>
> 执行记录：待创建

前置依赖：

- 现有一次性 [sync_latest()](../scripts/sync_lol_data.py) 和定向测试保持可用；
- 明确采用 `.env` 开关，不引入 API 控制或数据库状态；
- 确认默认间隔 10800 秒以及关闭时的空闲语义。

开发内容：

1. 实现 `LOL_DATA_SYNC_*` 配置加载和校验；
2. 实现 `--watch`、启动立即检查、等待、失败后继续和信号停止；
3. 增加 PDM watch 命令和定向单元测试；
4. 更新环境示例，确保无 Secret、无真实部署值；
5. 更新 README 的宿主机运行、开关和周期说明。

本阶段不实现：

- Docker Compose `data-sync` 编排和生产发布；
- API/数据库/Redis 状态控制；
- 结构化入库、向量化和真实生产 CDN 验证。

阶段验收：

- `enabled=false` 不发生 CDN 请求；`enabled=true` 启动后立即执行并按间隔继续；
- 单轮更新、跳过、失败和锁竞争行为与原有 `sync_latest()` 一致；
- 停止信号可释放 Worker；
- 定向测试和新增文件 lint 通过，旧的一次性命令无回归。

### 第二阶段：服务编排接入与部署验收

> 状态：未开始
>
> 阶段计划：待创建
>
> 执行记录：待创建

前置依赖：

- 第一阶段 Worker 已通过自动化测试；
- 部署环境提供稳定 raw 持久化卷和 service manager；
- 已明确 `LOL_DATA_SYNC_ENABLED` 在 test/product 的实际值和变更流程。

开发内容：

1. 在 `docker-compose.deploy.yml` 增加独立 `data-sync` 服务和稳定 raw 卷；
2. 验证与 API 同镜像但独立进程，不受 Gunicorn worker 数量影响；
3. 补充 Compose config、启停、卷保留、日志告警和回滚验证；
4. 记录真实环境验证的授权边界和未执行项。

本阶段不实现：

- 在 `docker-compose.infra.yml` 中混入应用 Worker；
- API 启动代码派生 Worker 子进程；
- cron 与常驻 Worker 双重调度；
- 生产数据删除或无审批的真实 CDN 长期运行。

阶段验收：

- 应用编排启动 `api` 和唯一 `data-sync`；
- `data-sync` 使用持久化 raw 卷，应用重启/镜像更新不会删除历史快照；
- 关闭开关并重启 Worker 后无外部请求，打开后下一次启动立即检测；
- 部署、回滚、停止和重启路径均不产生半成品或并发覆盖。

## 16. 风险、待确认项与决策记录

### 16.1 风险

| 风险 | 影响 | 缓解措施 | 状态 |
| --- | --- | --- | --- |
| 常驻 Worker 与 cron/手工命令重复运行 | 增加 CDN 请求和锁竞争 | 部署文档规定单一调度方式，保留锁作为最后防线 | 已缓解于设计 |
| raw 目录未持久化 | 容器重建后历史资料丢失 | 使用稳定命名的 Docker volume 或受控 bind mount，禁止普通部署删除 | 待实现 |
| 关闭 `.env` 后未重启进程 | 任务仍按旧配置运行 | 文档和运维检查明确 restart/recreate，日志打印启动配置状态但不打印敏感值 | 待实现 |
| CDN 长时间不可用 | 新版本延迟，旧数据继续服务 | 有限重试、失败保留旧快照、下一轮自动重试和告警 | 已缓解于设计 |
| 版本目录人工残缺 | 新同步拒绝覆盖，更新停滞 | fail closed，人工清理/修复后重试，不静默删除 | 已缓解于现有实现 |
| 历史版本持续增加导致磁盘增长 | 持久卷容量耗尽 | 监控磁盘并另行制定保留策略；第一版不自动删除历史数据 | 开放 |
| API 与 Worker 使用不同镜像版本 | 后续消费者读取不兼容数据 | 同一 Compose image tag，回滚保留卷并检查兼容性 | 待实现 |

### 16.2 待确认项

| 问题 | 为什么需要确认 | 建议默认值 | 负责人/状态 |
| --- | --- | --- | --- |
| product 是否默认启用 | 会决定首次部署是否访问官方 CDN | `false`，完成 test 验证后由部署审批改为 `true` | 发布前确认 |
| product raw 使用 named volume 还是主机 bind mount | 影响备份、迁移和人工查看方式 | 稳定 named volume；平台若有统一备份要求再改为受控 bind mount | 部署前确认 |
| 历史快照保留时长/容量上限 | 自动删除会影响追溯，永久保留会消耗磁盘 | 第一版不自动删除，先监控并单独制定保留策略 | 后续运维决策 |
| 本地是否纳入 launchd/systemd unit | 不同开发机的路径和权限不同 | 仓库只提供命令和说明，unit 文件由本机维护 | 本地使用者确认 |

### 16.3 方案决策记录

| 决策 | 原因 | 替代方案 | 确认来源 |
| --- | --- | --- | --- |
| 使用独立 Worker，不放 FastAPI startup | Gunicorn 多 worker/扩容会复制任务；独立服务边界清晰 | 在 API 内创建后台线程；未采用 | 用户当前需求与现有部署架构 |
| `.env` 开关只在进程启动时读取 | 不引入 API、数据库状态和热配置依赖，第一版简单可控 | API 动态开关、每轮重读环境；不纳入本版 | 用户明确不做 API 控制 |
| 启动立即检查，之后按完成时间等待 10800 秒 | 新版本不必等首个周期，且避免轮次重叠 | 固定整点 cron；不作为常驻 Worker 默认方式 | 用户要求三小时检测 |
| 复用 `sync_latest()`，不重写下载逻辑 | 现有原子发布、校验和锁已具备测试证据 | 新建第二套同步实现；不采用 | 当前代码事实 |
| 不增加数据库/Redis 状态 | 原始快照同步不需要业务持久状态；降低迁移和一致性风险 | AppSetting/status 表；留待后续运维可观测性阶段 | 用户明确第一版范围 |
| raw 历史目录不自动删除 | 数据追溯优先，删除是不可逆运维动作 | 按版本自动清理；留待容量策略确认 | RAG 数据设计和安全原则 |

## 17. 完成标准

第一版完成后的端到端链路应为：

```text
部署系统启动 api + data-sync
        ↓
data-sync 读取 LOL_DATA_SYNC_* 配置
        ↓
enabled=true 时立即读取官方版本配置
        ↓
版本未变化：保留现有完整快照并等待 10800 秒
版本变化：下载全部 JSON → 校验 → 临时目录 → 原子发布
        ↓
失败：旧快照继续可用，下一轮重试
        ↓
服务停止：Worker 响应信号并释放锁，不删除历史数据
```

方案整体进入可发布状态至少要求：

- 第一阶段 Worker 配置、watch、停止和异常测试通过；
- 现有一次性同步命令、原始文件格式和 manifest 兼容；
- 第二阶段 Compose 服务唯一、raw 卷持久化、API worker 数量不会复制同步任务；
- `.env` 开关、三小时周期、关闭/开启和重启语义在 README 与环境示例中一致；
- `pdm run test`、本期定向 lint、`pdm lock --check`、Compose config 和 `git diff --check` 的真实结果已记录；
- 未执行的真实 CDN、生产部署和卷清理验证明确标注为待环境验证；
- 总方案、阶段计划和阶段执行记录在实际开发后互相链接，且不把未来结构化入库/向量化写成第一版已完成。
