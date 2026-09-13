# JCC 官方资料同步与结构化入库：第一阶段“统一同步与结构化入库”执行记录

> 状态：已完成
>
> 执行日期：2026-09-13
>
> 总实施方案：[JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PLAN.md](JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PLAN.md)
>
> 阶段实现计划：[JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PHASE_1_PLAN.md](JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PHASE_1_PLAN.md)

## 1. 执行范围与结论

本次根据总方案完成第一阶段“统一同步与结构化入库”。

阶段结论：统一命令、不可变 raw revision、canonical Hash、结构化解析、Alembic 模型、单事务导入和 current 切换均已落地；自动化测试、隔离 PostgreSQL migration round-trip、真实仓库 raw 快照导入和质量检查通过，可以进入第二阶段只读 API 开发。

本阶段实际完成：

1. 将 `sync_lol_data.py`/`sync-lol-data` 重命名为 `sync_jcc_data.py`/`sync-jcc-data`，新增 `--force-refresh`、历史 Hash 匹配和 `-rN` 发布；
2. 新增 snapshot reader、canonical Hash、adapter/validation、11 张 `jcc_*` 表、事务 repository 和 raw 导入 service；
3. 使用现有 `raw/18.18.2-S19` 验证旧 manifest 兼容、完整解析和 PostgreSQL 入库，补齐同步/导入测试、环境示例、README 与三类阶段文档。

本阶段明确未实现或未执行：

- 未实现 `/jcc/*` HTTP API、用户 `jcc:data:read`、Service Token 资料接口、阵容结构化表或向量检索；
- `lineup_detail_total.json` 继续作为 raw 完整性文件保存和校验，不参与本阶段 snapshot Hash，也不结构化入库；
- 未执行真实 CDN `--force-refresh`、生产迁移、生产数据库导入、生产权限变更或部署操作。

## 2. 实际代码与配置变更

### 2.1 Raw reader、Hash 和同步命令

- [sync_jcc_data.py](../scripts/sync_jcc_data.py)：统一版本发现、普通 raw 复用、force-refresh 全量下载、历史 revision 扫描、不可覆盖发布和结构化导入；
- [snapshot_reader.py](../app/jcc_data/snapshot_reader.py)：固定资源白名单、manifest/来源/记录数/版本校验、目录 revision 解析、旧 manifest 内存兼容及符号链接拒绝；
- [canonical_hash.py](../app/jcc_data/canonical_hash.py)：按固定文件名字典序执行 canonical JSON 与 snapshot SHA-256；
- [test_sync_jcc_data.py](../tests/test_sync_jcc_data.py)：覆盖网络重试、普通复用、force-refresh Hash 命中、内容变化 `-r2`、旧 manifest 不回写、数字 revision 排序、失败清理、锁和 CLI 导入失败重试。

关键实现：

```text
发现配置中唯一 mode/name current
  → 普通模式选择最高完整 revision，或 force-refresh 全量下载
  → canonical Hash 匹配所有历史 revision
  → 命中则复用；未命中则原子发布下一个 -rN
  → 完整解析和关系校验
  → PostgreSQL 单事务导入全部实体/关系并切换 current
```

旧 manifest 缺失 `base_version`、`revision`、`raw_directory_name`、`content_hash` 时只在内存补全；已有 raw 文件没有被修改。

### 2.2 结构化解析与关系

- [validation.py](../app/jcc_data/validation.py)：实现安全字符串、ID、Integer/Decimal、`|` 分隔关系和哨兵值处理；
- [adapters.py](../app/jcc_data/adapters.py)：形成 database-neutral `StructuredSnapshot`，覆盖英雄、羁绊/tier、英雄羁绊、装备/recipe、强化、特殊机制和奇遇；
- [sync_service.py](../app/jcc_data/sync_service.py)：统一 `read_snapshot → build_structured_snapshot → import_snapshot`；
- [test_jcc_data_import.py](../tests/test_jcc_data_import.py)：覆盖所有实体、关系哨兵、未知关系拒绝、幂等、current 切换/回滚和数据库失败 rollback。

说明：

- 英雄正常 class/species 必须在同 snapshot 的 job/race 中存在；`0`/`-1` 仅作为无关系哨兵跳过；
- trait `type=0/1` 必须对应 race/job，`maxLevel`、`numList`、连续 tier level 和激活人数必须一致；
- 装备材料 `0` 不产生伪实体，两个材料均通过同 snapshot 复合外键约束；
- 特殊机制使用记录内 `adventureId` 作为外部 ID；所有外部 ID 以字符串持久化。

### 2.3 数据、迁移和状态

- [models.py](../app/jcc_data/models.py)：新增 `jcc_snapshots`、`jcc_current_snapshots`、`jcc_heroes`、`jcc_traits`、`jcc_trait_tiers`、`jcc_hero_traits`、`jcc_equipment`、`jcc_equipment_recipes`、`jcc_augments`、`jcc_adventures`、`jcc_galaxies`；
- [repository.py](../app/jcc_data/repository.py)：通过 PostgreSQL mode 级 advisory transaction lock、current 行锁、唯一约束和一个事务实现幂等导入/current 切换；
- [0002_jcc_structured_data.py](../alembic/versions/0002_jcc_structured_data.py)：`0001_initial_app_schema → 0002_jcc_structured_data` expand migration；
- [alembic/env.py](../alembic/env.py)、[app/models/__init__.py](../app/models/__init__.py)：显式注册 JCC metadata。

迁移验证：

- 迁移版本：`0002_jcc_structured_data`；
- 执行环境：本机 Docker PostgreSQL 中随机一次性数据库，使用 `finally`/trap 删除；
- upgrade：通过，创建 11 张 `jcc_*` 表；
- 数据导入：`raw/18.18.2-S19` 成功导入 345 heroes、36 traits、90 tiers、704 hero-trait relations、157 equipment、55 recipes、257 augments、350 adventures、20 galaxies；英雄技能值以 JSON 保存 `skillBriefValue`/`skillValueDesc`；重复导入返回 `skipped`，数据库只保留 1 个 snapshot；
- downgrade：通过，全部 `jcc_*` 表删除，`app_settings`、`sample_profiles` 和 `alembic_version` 保留；
- 再次 upgrade head：通过；`alembic check` 返回 `No new upgrade operations detected`；临时数据库清理完成。

### 2.4 API、Schema 或公共契约

本阶段未改变 HTTP 公共契约，没有注册 `/jcc/*` 路由。现有 `/api/profile`、`/internal/v1/records`、用户 JWT、Service Auth 和 Redis 状态行为通过全量回归测试。

新增的本地命令契约：

```bash
pdm run sync-jcc-data
pdm run sync-jcc-data --force-refresh
pdm run sync-jcc-data --raw-dir PATH --timeout 30 --retries 2
```

### 2.5 配置、依赖和外部服务

- [config.py](../app/core/config.py) 和三个 `.env.*.example` 增加 `JCC_DATA_RAW_DIR`、`JCC_DATA_MODE`、`JCC_DATA_MODE_NAME`、`JCC_DATA_SYNC_TIMEOUT_SECONDS`、`JCC_DATA_SYNC_RETRIES`；
- [pyproject.toml](../pyproject.toml) 注册 `python -m scripts.sync_jcc_data`，解决直接执行脚本时项目根目录不在 import path 的问题；
- 没有新增依赖，`pdm.lock` 未修改且一致性检查通过；
- 未调用真实 CDN；同步测试全部使用 fake fetcher，真实仓库 raw 仅用于本地只读解析及一次性测试库导入。

## 3. 关键设计结果

1. Snapshot Hash 严格覆盖 `adventure/chess/equip/galaxy/hex/job/race/trait/versiondataconfig` 九个 JSON，文件名按字典序；lineup 与 manifest 运行字段不进入 Hash。
2. Raw 目录是不可变输入：无后缀为 revision 1，新内容为 `-r2` 及以后；普通同步不下载，force-refresh Hash 命中任意历史 revision 时不新建目录。
3. 完整中间表示在事务前生成；数据库写入和 current 指针在同一事务，失败保留旧 current 和 raw。
4. PostgreSQL advisory transaction lock 覆盖首次导入无 current 行的并发窗口；同 snapshot 复合外键避免跨 revision 关系。
5. 第二阶段可直接复用 `get_current_snapshot()` 和 snapshot 限定的实体表，不需要读取 raw。

## 4. 与阶段计划的差异

| 差异 | 计划内容 | 实际实施 | 原因 | 影响与处理 |
| --- | --- | --- | --- | --- |
| `--dry-run` | 可选能力 | 未实现 | 总方案标记为“建议保留”，不是阶段强制项；避免引入下载后不发布时的第二套临时状态语义 | 不影响第一阶段验收 |
| Mode 选择 | 固定自然之力 | 使用 mode + mode_name 配置筛选配置中的唯一 current | 官方 version config 同时存在多个模式 current | 增强一致性，无接口影响 |
| 并发锁 | current 行锁/唯一约束 | 增加 PostgreSQL advisory transaction lock | 首次导入没有 current 行可锁 | 增强并发安全 |
| Trait tier 字段 | level/档位 | 同时保存 `tier_order` 与 level | 与总方案数据契约一致并支持稳定排序 | 第二阶段直接复用 |

其余实现与阶段计划一致，未扩大到第二阶段 API。

## 5. 测试与验证结果

### 5.1 验证汇总

| 检查 | 命令或方法 | 结果 | 证据/说明 |
| --- | --- | --- | --- |
| 定向测试 | `pdm run pytest tests/test_sync_jcc_data.py tests/test_jcc_data_import.py -q` | 通过 | 27 passed，2 条第三方 deprecation warnings |
| 定向 Ruff | `pdm run ruff check alembic/env.py alembic/versions/0002_jcc_structured_data.py app/core/config.py app/jcc_data app/models/__init__.py scripts/sync_jcc_data.py tests/test_sync_jcc_data.py tests/test_jcc_data_import.py` | 通过 | All checks passed；按用户要求未执行全仓 lint |
| Python 编译 | `python -m compileall -q app scripts alembic/versions tests` | 通过 | 无输出 |
| 全量测试 | `pdm run test -q` | 通过 | 最终 74 passed，2 条第三方 deprecation warnings |
| 命令帮助 | `pdm run sync-jcc-data --help` | 通过 | 展示 raw-dir、lock-file、timeout、retries、force-refresh |
| 统一 CLI | 复制现有 raw 到临时目录 + 随机 PostgreSQL 后执行 `pdm run sync-jcc-data` | 通过 | `sync updated: mode=18 version=18.18.2 revision=1 hash=f86464eecafe...`，临时目录/数据库均已清理 |
| 锁文件 | `pdm lock --check` | 通过 | 无依赖变化 |
| Migration round-trip | 随机临时 PostgreSQL `upgrade → import → downgrade → upgrade` | 通过 | 11 表、真实 raw 数据计数、base 表保留和 head 均验证 |
| Alembic metadata | 临时 PostgreSQL head 上 `pdm run alembic check` | 通过 | `No new upgrade operations detected` |
| Diff | `git diff --check` | 通过 | 无空白错误 |

### 5.2 失败与未执行项

- 本机长期开发数据库当前仍在 `0001_initial_app_schema`，因此直接对该库执行 `pdm run alembic check` 得到 `Target database is not up to date`；未擅自迁移长期库。随后在随机一次性 PostgreSQL 中 upgrade head 并执行同一检查通过。
- 未执行真实 CDN force-refresh 和生产操作；这些项目不属于普通 CI 或本阶段无副作用验收。

### 5.3 真实环境或人工验证

| 验证项 | 环境 | 副作用/授权 | 结果 |
| --- | --- | --- | --- |
| PostgreSQL migration round-trip | 本机 Docker、随机一次性数据库 | 创建/删除临时数据库，未触及长期库 | 通过，清理完成 |
| 现有官方 raw 完整导入 | 随机数据库 | 只读仓库 raw，写入后随临时库删除 | 通过 |
| 统一 CLI 端到端 | 复制 raw 的临时目录 + 随机数据库 | 写入临时 raw 锁文件和临时数据库，结束后删除 | 通过 |
| 真实 CDN force-refresh | 外部官方 CDN | 未授权外部真实调用 | 未执行 |
| 生产迁移/导入/部署 | 生产环境 | 未授权 | 未执行 |

## 6. 阶段验收结果

| 编号 | 验收标准 | 结果 | 验证证据 |
| --- | --- | --- | --- |
| AC-1-01 | 普通模式复用完整 raw，未入库 raw 仍可进入导入 | 通过 | [test_sync_jcc_data.py](../tests/test_sync_jcc_data.py) CLI 编排/普通复用测试 |
| AC-1-02 | force-refresh Hash 命中不新建目录，Hash 新增生成完整 `-rN` | 通过 | force-refresh、lineup Hash 边界、r2 测试 |
| AC-1-03 | 历史目录不可覆盖/删除，失败不污染旧 raw | 通过 | incomplete、下载失败、DB 失败 raw 保留测试 |
| AC-1-04 | 完整 raw 形成结构化 snapshot，事务失败 current 不变 | 通过 | [test_jcc_data_import.py](../tests/test_jcc_data_import.py) 与临时 PostgreSQL 导入 |
| AC-1-05 | `sync-jcc-data` 串起发现、raw、解析、导入和 current | 通过 | CLI 导入编排测试、命令 help、真实 raw import service |
| AC-1-06 | 文件、模块和表统一使用 `jcc` 命名 | 通过 | 脚本/测试重命名、11 张 `jcc_*` 表；没有 `lol_*` 模型 |
| AC-1-07 | 本阶段不增加 `/jcc/*` API 且既有 API 无回归 | 通过 | app router 未修改；全量 74 passed |
| AC-1-08 | 定向测试、迁移、锁文件、diff 结果真实记录 | 通过 | 本执行记录第 5 节 |

## 7. 安全、兼容性与可观测性核对

### 安全

- 下载 endpoint 和资源名固定，mode/version/season/目录名和 manifest 必须一致；raw reader 拒绝文件符号链接；
- 所有解析/关系/事务错误 fail closed，不切换 current；
- CLI 错误只输出异常类型和安全摘要，不记录 payload、Token、Secret 或数据库连接串；
- 无 HTTP 写接口、raw 文件接口或权限改动。

### 兼容性

- 旧无 revision/hash manifest 可读取，且测试确认不回写；
- 现有 raw、用户 API、内部 API、用户/Service Token 行为保留；
- migration expand-first，应用回滚不自动删除新表或历史 raw。

### 可观测性

- CLI 成功输出 mode、version、revision、Hash 前缀、directory 和结果状态；失败返回非零；锁竞争打印 skipped 并返回 0；
- 第一阶段没有新增独立指标系统或 HTTP 状态接口。

## 8. 遗留问题与后续阶段入口

### 8.1 当前阶段遗留问题

| 问题 | 影响 | 负责人/条件 | 处理阶段 |
| --- | --- | --- | --- |
| 真实 CDN force-refresh 未执行 | 尚无外部 CDN 当次验证证据 | 受控临时 raw 目录和明确授权 | 发布前人工验证 |
| 生产迁移/部署未执行 | 不代表生产已就绪 | 备份、审批、生产配置 | 发布阶段 |

### 8.2 下一阶段可复用能力

- `jcc_current_snapshots` 和 `get_current_snapshot()` 提供稳定 current snapshot；
- 所有实体、tiers 和 recipe 关系均限定 snapshot，可供 `/jcc/*` 查询；
- 第二阶段必须继续使用用户 Token + `jcc:data:read`，不得改用 Service Token 或读取 raw；
- API 请求应先固定 snapshot ID，再执行分页和关系查询，避免一次响应跨 revision。

## 9. 文档同步记录

- [总实施方案](JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PLAN.md)：第一阶段更新为已完成并链接计划/执行记录；
- [第一阶段实现计划](JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PHASE_1_PLAN.md)：更新为已完成，验收项改为已满足，记录 mode、锁和 tier 调整；
- 本执行记录：记录实际代码、迁移、测试、未执行外部操作和阶段结论。

## 10. 阶段结论

第一阶段已完成：

- 统一 raw revision 和结构化 PostgreSQL snapshot 闭环已建立；
- 定向 25 tests、全量 68 tests、定向 Ruff、migration round-trip、Alembic metadata、锁文件和 diff 检查通过；
- 未执行真实 CDN 和生产操作，已明确留作发布前受控验证；
- 第二阶段可基于 current snapshot 和结构化关系实现用户 Token 保护的 `/jcc/*` 只读 API。
