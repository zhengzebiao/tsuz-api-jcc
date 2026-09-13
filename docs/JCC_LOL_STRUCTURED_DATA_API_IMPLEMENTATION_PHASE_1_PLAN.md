# JCC 官方资料同步与结构化入库：第一阶段“统一同步与结构化入库”实现计划

> 状态：已完成
>
> 总实施方案：[JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PLAN.md](JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PLAN.md)
>
> 阶段执行记录：[JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PHASE_1_EXECUTION.md](JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PHASE_1_EXECUTION.md)
>
> 范围：统一 `sync-jcc-data` 同步入口、raw revision/hash 和结构化 snapshot 导入；不提前实现第二阶段 `/jcc/*` 用户 Token API。

## 1. 背景与阶段基准

### 1.1 前置阶段状态

本功能没有独立的前置阶段。当前仓库已有可复用的一次性 raw 同步实现：

- 实施前的 `scripts/sync_lol_data.py` 已实现官方版本发现、固定资源下载、JSON/版本校验、临时目录、原子发布、文件锁和有限重试；当前实现已重命名为 [scripts/sync_jcc_data.py](../scripts/sync_jcc_data.py)；
- 实施前的 `tests/test_sync_lol_data.py` 已覆盖基础同步成功、完整快照跳过、失败清理、锁竞争和网络重试；当前测试已重命名为 [tests/test_sync_jcc_data.py](../tests/test_sync_jcc_data.py)；
- `raw/18.18.2-S19/` 是已有完整快照，但旧 manifest 没有 revision 和 content hash 字段；本阶段按 revision 1 兼容读取，不回写历史文件。

### 1.2 当前仓库事实

- 现有 ORM 只有 `app_settings`、`sample_profiles`，Alembic head 为 `0001_initial_app_schema`；Alembic 通过 [alembic/env.py](../alembic/env.py) 显式导入模型。
- 原始基础资源使用 `version`、`season`、`setId`、`data` 包装；`job` 的 set 字段实际出现 `setID`，英雄 `class`/`species` 使用 `|` 分隔。
- 英雄数据含 `0`、`-1` 非关系哨兵，不能当作未知 race/job；真实关系 ID 必须能够在对应基础表中解析。
- 装备 `synthesis1`、`synthesis2` 为 `0` 时表示没有材料；`adventure` 的业务 ID 使用记录自身 `adventureId`，不能只信任 JSON object key。
- 当前工作区已有两份总方案文档新增，除此之外没有用户未说明的代码修改；实现不得重置、覆盖或删除这些变更。

### 1.3 本阶段目标

1. 将同步代码和 PDM 命令统一为 `scripts/sync_jcc_data.py` / `pdm run sync-jcc-data`，支持 `--force-refresh`、canonical SHA-256、历史 revision 匹配和不可覆盖发布；
2. 将完整 raw revision 解析为英雄、羁绊及 tiers、英雄羁绊关系、装备及 recipe、强化符文、特殊机制、开局奇遇结构化中间表示，并在一个数据库事务中导入；
3. 新增 `jcc_*` 表和 current 指针，数据库失败时回滚事务但保留 raw 和旧 current；补齐自动化测试、迁移、配置、README 和阶段记录。

## 2. 范围与约束

### 2.1 本阶段实现

- 固定官方资源集合的 raw reader、manifest 校验、旧 manifest 兼容和 canonical content hash；
- 普通同步复用完整 raw，`--force-refresh` 全量重检，同 Hash 复用历史 revision，不同 Hash 发布 `-rN`；
- `app/jcc_data/` 的 reader、hash、validation、adapter、ORM、repository 和同步编排；
- `jcc_snapshots`、`jcc_current_snapshots`、实体和关系表及 Alembic migration；
- 统一命令串起版本发现、raw 选择/发布、解析关系校验、事务导入和 current 切换；
- 测试只使用临时目录、fake fetcher、SQLite 或明确隔离的 PostgreSQL；同步和导入失败路径必须可重试且不污染旧数据。

### 2.2 本阶段明确不实现

- `/jcc/snapshot`、`/jcc/heroes` 或任何 `/jcc/*` HTTP 资料 API；
- 用户 `jcc:data:read` 权限生产配置、Service Token、应用间 Grant 或 raw HTTP 访问；
- 阵容结构化表/API、Embedding、全文/向量检索和 Agent 回答；`lineup_detail_total.json` 只作为 raw 快照文件保存和校验，不在本阶段入库；
- 生产 CDN 下载、生产数据库迁移/导入、生产权限变更和自动历史数据清理。

### 2.3 已确认约束

- 旧 `lol` 同步入口改为 `jcc` 命名，不新增独立 `ingest-*` 命令；
- raw 是不可变历史输入，禁止覆盖、删除已发布目录或复用 revision；
- canonical hash 只包括固定九个结构化资源文件，manifest 运行时间和 lineup 不参与 Hash；
- 解析和跨资源关系校验完成后才进入数据库事务；所有实体关系属于同一 snapshot；
- API 进程和用户认证代码不因本阶段改变；数据库回滚优先前向修复，migration downgrade 仅用于隔离测试。

### 2.4 临时数据与隔离测试规则

- 单元测试使用 `tmp_path`、fake fetcher 和 SQLite 内存库，不连接共享或生产数据库；
- 真实 PostgreSQL 迁移/约束测试只使用随机、一次性的隔离数据库；执行失败或清理失败必须如实记录；
- 不执行 `FLUSHDB`、共享数据库清库或生产 downgrade，不把真实 CDN/生产验证写成自动化通过；
- 测试和日志不保存或输出 Token、Secret、数据库密码、完整 raw payload 或完整外部响应。

### 2.5 前置依赖与环境条件

| 依赖 | 所需状态 | 当前状态 | 不满足时的处理 |
| --- | --- | --- | --- |
| PDM/Python 运行环境 | 可执行 pytest、ruff、Alembic | 已有项目环境 | 继续执行可运行的定向检查，未执行项如实记录 |
| 现有 raw 快照 | 可被 reader 校验 | 已有 `raw/18.18.2-S19/` | 兼容旧 manifest，不改写历史目录 |
| PostgreSQL | 迁移 round-trip 和真实 JSON/外键约束 | 是否可用需运行时确认 | 无隔离实例时只执行 SQLite/静态迁移检查并记录未执行 |

## 3. 详细设计与修改文件

### 3.1 Raw reader、Hash 和同步 CLI

修改/新增：

- [scripts/sync_jcc_data.py](../scripts/sync_jcc_data.py)：保留现有固定 CDN endpoint、重试、锁和 fetcher 注入，增加 force-refresh、revision、Hash、配置优先级和统一导入编排；
- `app/jcc_data/snapshot_reader.py`：固定白名单、manifest/资源完整性、目录 revision 和旧 manifest 兼容；
- `app/jcc_data/canonical_hash.py`：canonical JSON、单文件 Hash 和 snapshot content hash；
- `tests/test_sync_jcc_data.py`：从旧同步测试迁移并覆盖普通复用、force-refresh、Hash 命中/新增 revision、不可覆盖和失败重试。

设计：

1. 普通模式只请求 versiondataconfig；当前版本的最高完整 revision 可直接复用，未入库 raw 仍进入解析/导入；残缺的匹配目录 fail closed。
2. force-refresh 在锁内重新下载全部资源到临时目录；Hash 命中历史目录则删除临时目录并复用，Hash 新增则写完整 manifest 后原子 rename 到下一个 revision。
3. 旧无后缀目录逻辑 revision=1，content_hash 在内存补算；新目录写 `base_version`、`revision`、`raw_directory_name`、`content_hash`、`generated_at`。
4. CLI 只输出状态、版本、revision、Hash 前缀和目录，不输出 payload、凭证或完整错误响应；raw 发布失败不覆盖历史目录。

### 3.2 结构化解析和关系校验

新增：

- `app/jcc_data/validation.py`：字段别名、ID、整数/Decimal、分隔关系和安全错误；
- `app/jcc_data/adapters.py`：将 `RawSnapshot` 转换为 `StructuredSnapshot`，解析 heroes、traits/tiers、hero_traits、equipment/recipes、augments、adventures、galaxies；
- `app/jcc_data/__init__.py`：领域包导出稳定入口；
- `tests/test_jcc_data_import.py`：使用最小 fixture 覆盖全部实体和关系。

设计：

- 英雄 `class`、`species` 的正常 ID 分别映射 job、race；`0`/`-1` 哨兵跳过，未知非哨兵、空分段和重复分段拒绝；
- race/job 基础记录先形成 trait，`trait.type=0/1` 必须与 race/job 对应，按 `checkId` 聚合唯一 tier；
- 装备材料必须在同一资源集合中存在，`0` 不生成伪实体；adventure ID 取自身 `adventureId`；
- 所有外部 ID 以字符串保存，精确字段使用整数或 Decimal；中间表示完成后才开始数据库事务；不解析 lineup detail。

### 3.3 数据、迁移和状态

新增 ORM 和迁移：

- `app/jcc_data/models.py`：定义 `jcc_snapshots`、current、实体和关系模型，`JSON` 存 source attributes，唯一约束覆盖 snapshot 外部 ID、trait kind、tier、recipe 和关系；
- `app/models/__init__.py`、`alembic/env.py`：显式注册 JCC 模型；
- `alembic/versions/0002_jcc_structured_data.py`：只创建/删除本阶段表、外键、唯一约束和索引，不插入业务数据；
- `app/jcc_data/repository.py`：mode 事务锁、幂等导入、current 切换和只读查询；
- `app/jcc_data/sync_service.py`：raw → adapter → repository 编排。

`jcc_snapshots` 保存 mode、season、version、revision、set、raw directory、Hash、来源 manifest 和 imported_at；`jcc_current_snapshots` 保存每个 mode 的 snapshot 指针。快照、所有实体、关系和 current 更新在同一个事务提交；异常整体 rollback，旧 current 不变，raw 保留。

### 3.4 API、Schema 或公共契约

本阶段不增加 FastAPI 路由、Schema 或用户鉴权契约。现有 `/api/profile`、`/internal/v1/records`、Service Auth、Redis 黑名单/Session 代码不修改。只增加本地 `sync-jcc-data` CLI 契约。

### 3.5 配置、依赖和外部服务

- [app/core/config.py](../app/core/config.py) 增加 `JCC_DATA_*` 非敏感配置默认值；CLI 显式参数优先；
- 三个环境示例和 [README.md](../README.md) 改为说明 JCC 同步、raw 保留、数据库导入和 force-refresh；
- 不新增运行时依赖，`pdm.lock` 仅在依赖确有变化时更新；
- 普通 CI 使用 fake fetcher；真实 CDN 仅在明确授权的临时 raw 目录执行。

### 3.6 安全、权限与可观测性

- 固定 URL、固定资源文件名、路径组件和 JSON 格式校验，禁止任意 URL/路径；
- fail closed：下载、关系、Hash 或事务失败均不发布 current；
- 日志只记录 operation、状态、版本、revision、Hash 前缀、计数和安全错误类别；不输出 payload、Token、Secret、连接串或完整堆栈；
- 文件锁保护 raw 下载、revision 分配和发布；数据库事务锁/唯一约束保护 mode 并发导入。

## 4. 实施步骤

1. 建立本阶段计划和执行记录文件，更新总方案的阶段链接；
2. 新增 reader、canonical hash、validation、adapter 和最小单元测试；
3. 重命名同步脚本/测试，接入配置、force-refresh、revision/hash 和新命令；
4. 新增 ORM、Alembic migration 和 metadata 注册；
5. 实现 repository 事务/幂等/current 与 sync service 编排；
6. 补齐导入/回滚/失败重试测试，更新 README 和环境示例；
7. 只对本阶段新增/修改 Python 文件执行定向 ruff，执行定向测试、迁移状态、锁文件和 diff 检查；
8. 如有隔离 PostgreSQL，再执行 migration round-trip；最后按真实结果更新执行记录、阶段计划和总方案。

## 5. 测试与验证计划

### 5.1 定向测试

| 测试文件/范围 | 覆盖行为 | 预期结果 |
| --- | --- | --- |
| `tests/test_sync_jcc_data.py` | 下载/复用、force-refresh、canonical Hash、revision、旧 manifest、失败清理、锁和 CLI | raw 历史不可变，状态和退出码正确 |
| `tests/test_jcc_data_import.py` | adapters、关系校验、SQLite 导入、幂等、current、事务 rollback | 完整 snapshot 成功发布，异常不改变旧 current |
| 现有测试目录 | 用户 API、内部 records、Redis 状态和日志回归 | 既有行为不变 |

### 5.2 回归与质量检查

```bash
pdm run pytest tests/test_sync_jcc_data.py tests/test_jcc_data_import.py -q
pdm run ruff check app/jcc_data scripts/sync_jcc_data.py tests/test_sync_jcc_data.py tests/test_jcc_data_import.py
pdm run alembic-current
pdm lock --check
pdm run test
pdm run sync-jcc-data --help
git diff --check
```

上述 ruff 仅覆盖本阶段新增和修改的 Python 文件；不执行全仓 lint。

### 5.3 真实环境验证

真实 CDN、隔离 PostgreSQL upgrade/downgrade/upgrade、完整官方数据导入和生产部署均不进入普通 CI。没有明确授权和隔离资源时标记为未执行，不连接共享或生产数据库。

## 6. 验收标准与追踪

| 编号 | 验收标准 | 实现位置 | 验证方式 | 状态 |
| --- | --- | --- | --- | --- |
| AC-1-01 | 普通模式复用完整 raw，未入库 raw 仍可进入导入 | `scripts/sync_jcc_data.py`, `app/jcc_data/sync_service.py` | 定向同步/导入测试 | 已满足 |
| AC-1-02 | force-refresh Hash 命中不新建目录，Hash 新增生成完整 `-rN` | `snapshot_reader.py`, `canonical_hash.py`, script | revision/hash 测试 | 已满足 |
| AC-1-03 | 历史目录不可覆盖/删除，失败不污染旧 raw | script | 失败清理和不可覆盖测试 | 已满足 |
| AC-1-04 | 完整 raw 形成结构化 snapshot，事务失败 current 不变 | models/repository/sync_service | 导入 rollback/current 测试 | 已满足 |
| AC-1-05 | `pdm run sync-jcc-data` 串起发现、raw、解析、导入和 current | script | CLI/编排测试 | 已满足 |
| AC-1-06 | 文件、模块和表统一使用 `jcc` 命名 | 全部阶段文件 | 文件清单、migration metadata | 已满足 |
| AC-1-07 | 本阶段不增加 `/jcc/*` API 且既有 API 无回归 | `app/main.py` 未改动 | 全量现有测试 | 已满足 |
| AC-1-08 | 定向测试、迁移状态、锁文件和 diff 结果真实记录 | 阶段执行记录 | 执行命令 | 已满足 |

## 7. 风险、回滚与异常处理

| 风险或失败场景 | 影响 | 预防/检测 | 回滚或恢复 |
| --- | --- | --- | --- |
| CDN/JSON/关系校验失败 | 本轮不能更新 | 固定 schema、Hash 和 fail closed | 保留旧 raw/current，下次重试 |
| 同版本内容变化 | 若覆盖会丢历史 | Hash + 不可复用 revision | 发布新的 `-rN`，旧目录保留 |
| 数据库导入失败 | 新 snapshot 不完整 | 单事务、唯一约束、mode 锁 | rollback，raw 保留，重复运行统一命令 |
| 并发同步/导入 | 重复下载或 current 竞争 | 文件锁、事务锁、唯一约束 | 锁竞争跳过或事务失败重试 |
| 迁移不兼容 | 应用启动/导入失败 | 隔离 upgrade 检查，expand-first | 生产前向修复，不自动 downgrade |

应用回滚不删除 raw、历史结构化 snapshot 或新表；本阶段不实现 current 回滚命令。

## 8. 阶段交付物

代码与配置：

- `scripts/sync_jcc_data.py`、`app/jcc_data/`、JCC ORM、迁移和 `sync-jcc-data` 命令；
- 配置示例和 README 同步说明。

测试：

- `tests/test_sync_jcc_data.py`、`tests/test_jcc_data_import.py` 及现有回归测试。

文档：

- 更新 [总实施方案](JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PLAN.md) 的阶段状态与链接；
- 更新本计划的状态和最终设计调整；
- 更新 [第一阶段执行记录](JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PHASE_1_EXECUTION.md)。

## 9. 计划调整记录

| 调整项 | 原计划 | 调整后 | 原因 | 对总方案/后续阶段的影响 |
| --- | --- | --- | --- | --- |
| 结构化阵容数据 | 总方案 raw 包含 lineup，但阶段未明确表范围 | 只保存/校验 `lineup_detail_total.json`，本阶段不建 lineup 表 | 总方案第 7.2 节未定义 lineup 结构化表，避免扩大阶段范围 | 无；后续如需阵容 RAG 另行设计 |
| Lint 范围 | 总方案要求新增/修改文件检查 | 明确仅对本阶段新增/修改 Python 文件执行 Ruff | 用户明确要求不检测全仓旧问题 | 不影响功能验收 |
| Mode 配置 | 版本发现固定筛选“自然之力” | 通过 `JCC_DATA_MODE` + `JCC_DATA_MODE_NAME` 筛选配置中唯一 current 记录 | 官方 version config 同时包含多个模式的 current 记录，需先按业务模式过滤 | 无；增强配置一致性 |
| 并发锁 | current 行锁 + 唯一约束 | PostgreSQL mode 级 advisory transaction lock + current 行锁 + 唯一约束 | 首次导入尚无 current 行，单靠行锁不能串行化 | 无；补齐首次导入并发保护 |
| Trait tiers | 只保留 level | 同时保存 `tier_order` 与官方 level，并校验 maxLevel/numList/tier 一致 | 与总方案表契约对齐并提高 fail-closed 完整性 | 第二阶段可稳定排序 tiers |
