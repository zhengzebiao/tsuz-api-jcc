# JCC 官方资料同步、结构化入库与只读 API 实施方案

> 状态：实施中（第一阶段已完成，第二阶段未开始）
>
> 本方案基于当前 `tsuz-api-jcc` 的 FastAPI、SQLAlchemy、Alembic、PostgreSQL、用户 Token 鉴权和官方 JSON 原始快照制定，并结合 [RAG 数据设计](jcc-ai-agent-rag-data-design.md) 与当前同步实现制定。
>
> 本文只描述实施方案，不代表代码、数据库迁移、真实 CDN 下载或部署已经执行。
>
> 相关文档：[RAG 数据设计](jcc-ai-agent-rag-data-design.md)、[原始同步历史方案](JCC_LOL_DATA_SYNC_IMPLEMENTATION_PLAN.md)、[用户鉴权实现](../app/deps/auth.py)。

## 1. 已确认业务配置与关键决策

| 项目 | 决策或配置 | 状态/来源 |
| --- | --- | --- |
| 数据来源 | 使用官方 CDN 资源，先保存到 `raw/<version>-<season>[-r<N>]/` 完整快照，再解析入库 | 已确认；用户需求、现有同步实现与 RAG 设计 |
| 同步命令 | 统一使用 `pdm run sync-jcc-data`，一个命令完成版本发现、资源下载、raw 保存、解析、结构化入库和 current 切换 | 已确认；用户确认合并下载、解析和入库 |
| 强制检查 | `pdm run sync-jcc-data --force-refresh` 显式重新下载当前官方版本，用于发现版本号不变的局部修订 | 已确认；用户确认采用强制检查机制 |
| 同版本修订 | 基础目录为 revision 1；内容变化时新建 `-r2`、`-r3` 等 revision，禁止删除或覆盖历史目录 | 已确认；用户确认采用修订版本机制 |
| 结构化数据 | 保存快照级元数据、规范化实体和精确关系，供数据库查询与后续 RAG 使用 | 已确认；RAG 设计 |
| 数据库发布 | 每个 revision 作为一套完整结构化快照，全部导入成功后才切换 current 指针 | 已确认；数据一致性设计 |
| HTTP API 形态 | 提供对外业务路径 `/jcc/*`，接口使用用户 Token | 已确认；用户确认 |
| HTTP API 权限 | 所有新资料接口统一要求用户权限 `jcc:data:read` | 已确认；用户确认 |
| API 调用方 | 面向带有用户登录上下文的外部业务客户端/JCC 子应用；不作为 main 对 JCC 的应用间 Service API | 已确认；用户确认 |
| 英雄接口 | 英雄提供列表和详情两个接口 | 已确认；用户确认 |
| 其他资源接口 | 羁绊、装备、强化符文、特殊机制、开局奇遇只提供列表接口；完整内容放在列表 `items` 中 | 已确认；用户确认 |
| 装备合成 | 装备合成材料直接包含在装备列表 item 的 `components` 中，不提供装备详情接口 | 已确认；用户确认 |
| 原始文件 API | 不提供 raw 文件读取、文件下载或任意路径访问接口 | 已确认；用户确认 |
| 向量化 | 本方案不实现 Embedding、全文检索或向量库；只为后续按语义文档 Hash 增量向量化保留稳定数据 | 已确认；当前阶段范围 |
| 阶段数量 | 分为两个阶段：统一同步与结构化入库、用户 Token 只读 API | 已确认；用户确认 |

## 2. 背景与现状

### 2.1 背景

[RAG 数据设计](jcc-ai-agent-rag-data-design.md) 已明确，英雄属性、装备合成和羁绊关系等内容需要结构化查询，不能只依赖向量相似度。当前仓库已有官方 JSON 原始快照和一次性同步逻辑，但尚未形成“完整快照 → 结构化数据库 → 用户 Token 保护的 `/jcc/*` API”闭环。

本方案把同步、解析和入库放在同一个受控命令中编排，但保持代码模块职责分离：下载模块只负责官方资源和 raw 原子保存，解析模块负责数据转换和关系校验，数据库模块负责事务导入和 current 指针，API 模块只负责用户鉴权和只读查询。

### 2.2 当前架构

已核对的仓库事实：

- 方案制定时 [scripts/sync_lol_data.py](../scripts/sync_lol_data.py) 已实现官方版本发现、8 个基础资源下载、JSON 校验、临时目录写入、目录级原子发布和 `fcntl.flock` 单实例锁；第一阶段已将其重命名并扩展为 [scripts/sync_jcc_data.py](../scripts/sync_jcc_data.py)。
- 方案制定时 [pyproject.toml](../pyproject.toml) 注册的是 `sync-lol-data`；第一阶段已改为 `sync-jcc-data`。
- 当前完整快照目录为 [raw/18.18.2-S19](../raw/18.18.2-S19)，包含 `versiondataconfig.json`、`chess.json`、`race.json`、`job.json`、`trait.json`、`equip.json`、`hex.json`、`adventure.json`、`galaxy.json` 和 `manifest.json`。
- 当前样本记录数为：`chess` 345、`race` 24、`job` 12、`trait` 90、`equip` 157、`hex` 257、`adventure` 350、`galaxy` 20。该数据只用于理解样本规模，实施时不得把记录数写成固定成功条件。
- 基础资源使用 `{version, season, setId, time, data}` 包装；当前资源关系包括：英雄 `species`/`class` 对应 race/job；`trait.checkId` 对应按档位拆分的羁绊记录；装备 `synthesis1`/`synthesis2` 对应材料。
- 当前 [app/deps/auth.py](../app/deps/auth.py) 提供用户 JWT 校验、用户黑名单/Session 检查和 `require_scope()`；现有 [app/api/example.py](../app/api/example.py) 的 `/api/profile` 使用用户 Token 和 `user:read` 权限。
- 当前 [app/api/internal.py](../app/api/internal.py) 和 [app/deps/service_auth.py](../app/deps/service_auth.py) 服务于已有内部 records 示例。本方案新增的 `/jcc/*` 资料 API 只复用用户 Token 认证链路，不接入既有 Service Token 依赖，避免混用鉴权模型。
- 当前 [alembic/env.py](../alembic/env.py) 通过显式导入模型生成迁移元数据；现有数据库只有 `app_settings` 和 `sample_profiles`。
- 方案制定时工作区已有同步历史方案等既有文档变更；第一阶段实施已保留这些变更，并兼容读取已有 raw 历史快照，没有重置、覆盖或删除历史目录。

### 2.3 现状差距

1. raw 快照还没有可由 SQL 查询的英雄—羁绊、装备—材料等关系；
2. 没有结构化快照表和 current 指针，API 无法稳定读取一套完整版本；
3. 当前同步命令名称和代码模块仍使用 `lol` 命名，尚未统一为 `jcc`；
4. 当前同步逻辑发现完整版本目录后会跳过，无法在版本号不变时发现官方局部修订；
5. 当前没有统一的用户权限 `jcc:data:read` 和 `/jcc/*` 业务 API；
6. 当前没有覆盖版本、revision、Hash、关系完整性、数据库事务和 API 版本元数据的测试。

## 3. 目标与非目标

### 3.1 目标

本方案完成：

1. 将现有同步实现重命名为 JCC 语义，并提供 `pdm run sync-jcc-data` 统一入口；
2. 让统一命令完成官方版本发现、资源下载、raw 完整快照保存、JSON 解析、关系校验、结构化数据库导入和 current 指针切换；
3. 增加显式 `--force-refresh`，支持版本号不变时全量重检，并通过 revision 目录保存新的完整 raw 快照；
4. 在 PostgreSQL 中建立 `jcc_*` 命名的快照、实体和关系表，保留历史 revision，不覆盖已发布数据；
5. 通过一次数据库事务发布一套完整结构化快照，失败时保留旧 current 和已保存的 raw revision；
6. 提供 `/jcc/snapshot`、`/jcc/heroes`、`/jcc/heroes/{hero_id}` 及其他资源列表接口；
7. 所有新 API 使用现有用户 Token 校验和 `jcc:data:read` 用户权限；
8. 除英雄外不提供资源详情路由，列表 item 返回对应资源的完整结构化内容，装备 item 直接包含合成材料；
9. 增加同步、revision、导入、数据库、用户权限、分页、关系聚合和 API 回归测试；
10. 为后续 RAG 文档生成和增量向量化保留 snapshot Hash、实体关系、来源和版本元数据。

### 3.2 非目标

本方案明确不实现：

- `POST`、`PUT`、`PATCH`、`DELETE` 等资料写接口；
- 通过 HTTP 手动导入、强制刷新、版本切换、删除、作废或恢复 current 的管理接口；
- 应用间 Service Token、应用间 Grant、应用 Actor 审计或 main→JCC 的内部资料调用链；
- 匿名公网接口；
- raw JSON 文件下载、任意文件路径读取或 CDN URL 代理；
- 除英雄外的资源详情接口；
- Embedding、向量库、全文检索、语义检索和 Agent 自动回答；
- 将同步、解析和入库扩展为本方案未定义的其他业务流程；
- 手动删除 raw 目录来触发更新；
- 自动清理历史 raw 目录、结构化历史 revision 或数据库历史快照；
- 图片下载、图片代理、图片授权处理和官方内容再分发合规结论。

## 4. 需求与核心流程

### 4.1 参与者和使用场景

| 参与者 | 前置条件 | 操作 | 预期结果 |
| --- | --- | --- | --- |
| 数据运维 | 已配置数据库和 raw 根目录 | 执行 `pdm run sync-jcc-data` | 检查官方版本，复用或保存完整 raw 快照，解析并导入数据库 |
| 数据运维 | 需要检查版本号不变的官方调整 | 执行 `pdm run sync-jcc-data --force-refresh` | 重新下载整套资源；无变化复用已有 revision，有变化生成新 revision |
| 数据库暂时不可用 | raw 下载和校验已经成功 | 重试同一个同步命令 | 复用已保存的完整 raw revision，重新执行结构化导入，不要求重新下载或删除 raw |
| 已登录用户/JCC 子应用 | 持有 main 签发的用户 Access Token，Token 含 `jcc:data:read` | 请求 `/jcc/*` | 返回当前结构化官方资料和快照元数据 |
| 已登录用户 | Token 有效但没有 `jcc:data:read` | 请求 `/jcc/*` | 返回 `403`，不返回资料内容 |
| API 服务 | 当前没有成功导入的结构化快照 | 请求 `/jcc/*` | 返回固定 `503`，不回退读取 raw 或部分数据 |

### 4.2 统一同步与入库流程

```text
pdm run sync-jcc-data [--force-refresh]
        ↓
发现并校验官方当前版本
        ↓
选择完整 raw 候选
  ├─ 普通模式：复用完整目录；缺少目录时下载新版本
  └─ 强制模式：重新下载当前版本全部资源到临时目录
        ↓
校验整套资源并计算 snapshot content_hash
        ↓
匹配同版本历史 revision
  ├─ Hash 已存在：复用该 revision
  └─ Hash 不存在：发布下一个 -rN revision
        ↓
解析所有资源、校验跨资源引用
        ↓
PostgreSQL 单事务写入 jcc snapshot、实体和关系
        ↓
原子更新 mode=18 的 current snapshot 指针
        ↓
输出 updated / matched / skipped / failed
```

统一命令的职责是**编排完整流程**，不是把所有逻辑堆在一个函数中。下载、raw 保存、解析、校验、数据库导入和结果日志仍应由独立模块承担。

关键行为：

1. 普通模式发现完整的当前版本 raw 目录时，不重复下载；如果该 raw revision 尚未成功入库，仍继续解析和入库，不能因为 raw 已存在就跳过数据库步骤。
2. `--force-refresh` 只影响是否重新向官方 CDN 检查和下载，不影响结构化数据的完整校验要求；不接受 HTTP 参数触发。
3. raw 下载和校验成功、数据库导入失败时，保留新 raw revision；数据库事务回滚，current 仍指向旧的完整 snapshot。
4. 下次运行同一个 `sync-jcc-data` 时，优先识别并复用已保存但尚未成功入库的完整 revision，重新尝试结构化导入。
5. 只有结构化解析、跨资源关系校验和数据库事务全部成功，才允许更新 current 指针。

### 4.3 失败与边界流程

- 官方版本配置缺少唯一当前版本：本轮失败，不改 raw 和数据库。
- 资源下载、JSON 编码、资源版本/赛季/模式校验失败：临时目录清理，旧 raw 和旧 current 保持不变。
- 完整 raw 目录缺少文件或 manifest 不自洽：不得当作可导入候选，不覆盖同名目录。
- 同版本强制检查得到已存在的 Hash：不新建 raw 目录；如果对应 revision 未入库，仍继续结构化入库。
- 同版本强制检查得到新的 Hash：在文件锁内分配下一个 revision，原子发布新目录；旧目录不变。
- 同一 revision 目录已存在但 Hash 不同：失败，不覆盖，保留现有目录并告警。
- 结构化关系引用缺失或数值类型非法：数据库事务不提交，旧 current 继续可用。
- 两次同步并发执行：使用现有文件锁保护下载和 raw revision 分配；数据库侧使用按 mode 的事务锁和唯一约束保护导入。
- API 查询过程中 current 切换：每个请求先取得一个 snapshot ID，后续查询全部限定该 ID，单次响应不混用两个 revision。
- current 回滚不是 HTTP 能力。受控运维确认目标 snapshot 完整后，在事务内更新 `jcc_current_snapshots.snapshot_id`；失败则保留原指针。

## 5. 当前架构适配与总体设计

### 5.1 设计原则

- **raw 是不可变源输入。** 新版本或同版本修订都保存完整资源集合，旧目录不就地更新、不覆盖、不删除。
- **统一命令，模块分层。** 一个 `sync-jcc-data` 命令串起下载、解析和入库，但每层保持可测试、可重试和可替换。
- **数据库是结构化投影。** 结构化表可以从 raw 重新生成，raw 不被数据库 BLOB 替代。
- **完整快照发布。** 即使只有一个英雄变化，也生成一套完整结构化 snapshot，避免实体和关系来自不同版本。
- **当前指针可切换。** API 查询 current pointer 指向的完整 snapshot；历史 snapshot 保留，回滚只切换指针，不复制或删除历史数据。
- **Hash 粒度分离。** snapshot Hash 判断整套官方资源是否变化；实体 Hash或后续语义文档 Hash 用于更细粒度的增量处理，不能互相替代。
- **用户权限与资源查询分离。** 用户 Token 只证明用户身份和权限；资料查询仍由 JCC 自己执行参数校验、版本过滤、字段白名单和数据关联。
- **禁止 raw 透传。** API 返回稳定的结构化 Schema，不把 ORM、JSONB 或原始 JSON 直接透传给调用方。

### 5.2 目标架构

```text
官方 CDN
   ↓
统一 sync-jcc-data 命令
   ├─ 版本发现与资源下载
   ├─ raw 完整快照与 revision 发布
   ├─ source adapter / 关系校验
   ├─ PostgreSQL 事务导入
   └─ current snapshot 切换
          ↓
PostgreSQL
   ├─ jcc_snapshots / jcc_current_snapshots
   ├─ jcc_heroes / jcc_traits / jcc_equipment
   └─ jcc_augments / jcc_adventures / jcc_galaxies
          ↓
用户 Access Token
   ↓（jcc:data:read）
/jcc/snapshot
/jcc/heroes
/jcc/heroes/{hero_id}
/jcc/traits
/jcc/equipment
/jcc/augments
/jcc/adventures
/jcc/galaxies
```

职责边界：

- 同步模块负责官方网络、固定 URL、超时重试、raw 文件完整性、revision 和 manifest；不处理 HTTP 请求。
- 解析模块负责官方字段归一化、嵌套 detail 解析、数值转换和跨资源引用；不决定用户权限。
- 数据库模块负责迁移、单事务导入、current 指针和只读查询；不从 CDN 请求。
- API 模块负责 `/jcc/*` 路由、用户 Token、`jcc:data:read`、分页和响应 Schema；不读取 raw 目录。

### 5.3 兼容策略

- 第一阶段已将方案制定时的 `scripts/sync_lol_data.py` 和 `sync-lol-data` 统一重命名为 `scripts/sync_jcc_data.py` 和 `sync-jcc-data`，并同步更新 PDM、README 和测试引用。
- 新表统一使用 `jcc_` 前缀；不创建 `lol_*` 结构化表。
- 新 API 使用 `/jcc/*`，不改变现有 `/api/profile` 和 `/internal/v1/records` 的行为。
- `/jcc/*` 使用用户 Token，旧的内部 records 路由仍按其现有内部鉴权运行，两者依赖和权限不能混用。
- 用户 Token 的权限由 main 的用户角色/权限体系产生；JCC 只声明并检查 `jcc:data:read`，不在 JCC 建立应用间 Grant。
- 新增数据库迁移采用 expand-first；应用镜像回滚不自动删除新表、不删除历史 raw、不自动 downgrade。
- 旧 raw manifest 缺少 revision 或 content_hash 时按 revision 1 兼容读取并在内存中补算，不要求手动改写或删除旧文件。

## 6. 同步、强制检查与 Hash 设计

### 6.1 统一命令契约

默认命令：

```bash
pdm run sync-jcc-data
```

显式强制检查：

```bash
pdm run sync-jcc-data --force-refresh
```

命令包含以下操作：

1. 读取官方 `versiondataconfig`，确认当前“自然之力”版本；
2. 复用完整本地 raw，或下载全部资源到临时目录；
3. 解析和校验所有官方资源；
4. 计算内容 Hash 并确定 raw revision；
5. 解析英雄、羁绊、装备、强化符文、特殊机制和奇遇；
6. 将完整结构化结果写入 PostgreSQL；
7. 原子更新 current snapshot；
8. 输出安全的结果日志。

统一使用 `pdm run sync-jcc-data`，不再提供第二套独立导入命令。数据库导入失败后的重试仍通过 `pdm run sync-jcc-data` 完成。

建议保留 `--dry-run`，其含义是执行版本选择、文件完整性校验、Hash、解析和关系校验，但不写 raw 新目录、不写数据库；真实网络使用必须在受控环境执行。

### 6.2 Raw revision 规则

目录约定：

```text
raw/
  18.18.2-S19/       # revision=1
  18.18.2-S19-r2/    # revision=2
  18.18.2-S19-r3/    # revision=3
```

规则：

1. 新的游戏版本使用无后缀基础目录并记录 `revision=1`。
2. 同一 `(mode, season, version)` 进行强制检查时，必须扫描该版本全部已发布 revision 的 Hash。
3. 新下载内容与任一历史 revision Hash 相同：清理临时目录，返回 `matched/skipped`，报告匹配 revision，不新建目录。
4. 新下载内容与所有历史 revision Hash 都不同：在文件锁内分配下一个未使用的 revision，使用临时目录写入后原子 rename 发布。
5. revision 编号不可复用；发布失败不得占用或覆盖现有目录。
6. 任意已发布目录均不可通过同步命令修改或删除；历史清理由另行批准的运维策略负责。
7. 同版本局部修订仍保存完整资源集合，不只保存变化的英雄文件。这样后续结构化快照和回滚都有完整输入。
8. raw 阶段匹配旧 revision 不代表数据库 current 自动改变；统一命令随后检查该 revision 是否已入库，只有成功导入才按 current 发布规则切换。

### 6.3 Snapshot content_hash 计算

`content_hash` 不是官方字段，而是本地根据整套资源计算的 SHA-256。建议使用以下确定性算法：

1. 读取固定资源集合：

   ```text
   versiondataconfig.json
   chess.json
   race.json
   job.json
   trait.json
   equip.json
   hex.json
   adventure.json
   galaxy.json
   ```

2. 每个文件以 UTF-8 解析 JSON，再用固定规则规范化序列化：
   - object key 按字典序排序；
   - 使用 UTF-8、无额外空白和固定 separators；
   - 保留 array 原有顺序；
   - 不把数组改成集合，不把官方字符串数字擅自改成另一种类型；
   - 禁止 NaN、Infinity 等非标准 JSON 值。
3. 对每个规范化文件计算：

   ```text
   file_hash = SHA256(canonical_json_bytes)
   ```

4. 按固定文件名顺序拼接文件名和 Hash，再计算整套 Hash：

   ```text
   snapshot_content_hash = SHA256(
     "adventure.json\0" + adventure_file_hash + "\n" +
     "chess.json\0" + chess_file_hash + "\n" +
     ...
   )
   ```

5. 不把 `revision`、目录临时名、Hash 自身、manifest 生成时间或其他易变运行字段计入 Hash。manifest 中的来源 URL、文件名和记录数参与校验，但不因生成时间变化而改变内容 Hash。

因此：

- 任意一个资源的有效内容变化都会改变整套 snapshot Hash；
- 重新下载但内容相同会得到相同 Hash；
- Hash 相同复用已有 revision；
- Hash 不同生成新 revision；
- 这与后续语义文档的 `content_hash = SHA256(normalized_text)` 是不同粒度，不能混用。

### 6.4 Manifest 契约

新发布 revision 的 `manifest.json` 至少包含：

```json
{
  "mode": "18",
  "mode_name": "自然之力",
  "season": "S19",
  "version": "18.18.2",
  "base_version": "18.18.2-S19",
  "revision": 2,
  "raw_directory_name": "18.18.2-S19-r2",
  "content_hash": "<sha256>",
  "source_updated_at": "2026-09-09 17:43:34",
  "generated_at": "<runtime timestamp>",
  "sources": {}
}
```

旧 manifest 如果没有 `base_version`、`revision`、`raw_directory_name` 或 `content_hash`：

- 目录无后缀时逻辑上按 revision 1；
- content_hash 根据资源文件在内存中补算；
- 不回写旧 raw 文件；
- 新的 raw revision 必须完整写入上述字段。

## 7. 数据模型、迁移与状态设计

### 7.1 快照和 current 指针

建议新增：

```text
jcc_snapshots
├── id: BigInteger PK
├── mode: String(32), non-null
├── mode_name: String(128), non-null
├── season: String(32), non-null
├── version: String(64), non-null
├── revision: Integer, >= 1, non-null
├── set_id: String(32), non-null
├── source_updated_at: String(64), nullable
├── version_start_time: String(64), nullable
├── raw_directory_name: String(160), non-null
├── content_hash: String(64), non-null
├── source_manifest: JSON/JSONB, non-null
├── imported_at: timezone-aware timestamp, non-null
├── UNIQUE(mode, season, version, revision)
└── UNIQUE(mode, season, version, content_hash)

jcc_current_snapshots
├── mode: String(32) PK
├── snapshot_id: FK → jcc_snapshots.id, non-null
└── updated_at: timezone-aware timestamp, non-null
```

约束：

- `jcc_snapshots` 只在一套完整实体和关系成功写入后提交，因此已提交 snapshot 必须是可查询的完整结构化快照；
- `jcc_current_snapshots` 只保存每个 mode 默认使用的 snapshot ID；
- current 表的 `snapshot_id` 必须指向同一 mode 的 snapshot；
- 同版本同 Hash 只能有一条结构化 snapshot；
- revision 和 Hash 均不是用户可提交的 API 字段。

### 7.2 规范化实体和关系

所有业务实体表都包含 `snapshot_id`、内部主键、官方外部 ID、结构化字段和必要的 `source_attributes`。外部 ID 使用字符串，内部主键不暴露为业务 ID。

```text
jcc_heroes
├── snapshot_id + external_id: UNIQUE
├── name, price, hero_type, map_id
├── health, attack_damage, armor, magic_resist, attack_speed
├── attack_range, initial_mana, max_mana
├── skill_name, skill_description, skill_values
├── image_url, skill_icon_url
└── source_attributes

jcc_traits
├── snapshot_id + kind(race|job) + external_id: UNIQUE
├── name, prefix, max_level, activation_list, image_url, map_id
└── source_attributes

jcc_trait_tiers
├── trait_id + tier_order: UNIQUE
├── external_id, activation_count, level
├── description, real_description
└── source_attributes

jcc_hero_traits
├── hero_id + trait_id: UNIQUE
├── relation_kind(race|job)
└── position

jcc_equipment
├── snapshot_id + external_id: UNIQUE
├── name, type, basic_description, description, image_url
└── source_attributes

jcc_equipment_recipes
├── equipment_id: UNIQUE
├── first_component_equipment_id
└── second_component_equipment_id

jcc_augments
├── snapshot_id + external_id: UNIQUE
├── name, level, description, icon_url
└── source_attributes

jcc_adventures
├── snapshot_id + external_id: UNIQUE
├── title, description, price, category, logo_url
└── source_attributes

jcc_galaxies
├── snapshot_id + external_id: UNIQUE
├── name, description, logo_url
└── source_attributes
```

说明：

- `trait.json` 的多条同名/同 `checkId` 档位记录归并为一条 `jcc_traits` 加多条 `jcc_trait_tiers`；
- `equip.synthesis1` 或 `synthesis2` 为 `"0"` 时不创建 ID `0` 伪实体；
- 同一 snapshot 内所有关系必须使用同一 snapshot 的内部实体，禁止跨版本关联。

### 7.3 解析和关系校验

导入流程使用独立 source adapter，不在 ORM Model 或 API 路由中直接解析官方字典：

1. 校验目录名、manifest、固定文件集合和 source URL；
2. 校验所有基础资源的 version、season、setId；兼容官方字段大小写差异（例如 `setid`/`setID`）；
3. 校验英雄 `species`、`class` 的 `|` 分隔值，拒绝空分段、重复分段和未知关系；
4. 将数值字段转换为明确的整数或 Decimal，禁止用不受控的 float 作为精确数值存储；
5. 聚合 `trait.checkId` 的 tiers，并校验所属 race/job；
6. 校验装备材料存在且 recipe 不形成伪 ID；
7. 先在内存形成完整中间表示，再开启数据库事务；
8. 任意资源或关系失败都拒绝整个 revision，不使用空关系或跨版本数据降级。

### 7.4 事务、幂等和 current 切换

统一命令导入的数据库顺序：

```text
读取完整 raw revision
  ↓
完整解析、关系校验、计算 content_hash
  ↓
按 (mode, season, version, revision/content_hash) 查询
  ├─ 相同 revision/hash：skipped
  ├─ 同版本已有相同 Hash：matched/skipped，复用已有 snapshot
  └─ 新 revision：继续
  ↓
取得 mode 级 PostgreSQL 事务锁
  ↓
写入 jcc_snapshots、全部实体和关系
  ↓
更新 jcc_current_snapshots[mode] = 新 snapshot
  ↓
commit
```

如果中间任意一步失败：

- 当前数据库事务整体回滚；
- raw 文件保留，但不被视为已入库；
- current 指针保持原值；
- 下次统一命令可以复用 raw revision 重试。

同版本已有 Hash 的处理：

- 如果已有数据库 snapshot 且已经是 current，整个导入返回 `skipped`；
- 如果已有数据库 snapshot 但不是 current，命令按正常成功导入结果将其切换为 current；
- 如果 raw revision Hash 已存在但数据库尚无对应 snapshot，仍需完整导入；
- Hash 匹配本身不删除目录、不改写 raw、不绕过关系校验。

### 7.5 current 回滚

回滚不是 HTTP 接口，也不删除数据。以 `mode=18`、当前 `snapshot_id=3` 回滚至 `snapshot_id=2` 为例：

1. 运维确认 snapshot 2 存在、属于 mode 18，并且实体与关系完整；
2. 在一个数据库事务中锁定 `jcc_current_snapshots` 的 mode 18 行；
3. 将 `snapshot_id` 从 3 更新为 2，并更新 `updated_at`；
4. 提交事务；
5. 读取 `/jcc/snapshot` 和一个资料列表接口，确认返回 revision 2 的元数据。

事务失败时仍读取 snapshot 3。该操作属于受控运维 runbook，不作为普通用户或外部客户端能力。

## 8. 模块与服务拆分

### `scripts/sync_jcc_data.py`

- 作为统一 CLI 入口，编排版本发现、下载、raw revision、解析、数据库导入和 current 切换；
- 保留 `--raw-dir`、`--lock-file`、`--timeout`、`--retries` 等必要运维参数；
- 增加 `--force-refresh` 和可选 `--dry-run`；
- 不接收 HTTP 请求、不读取用户输入作为 CDN URL、不提供资料写 API；
- 数据库导入失败时返回非零状态，但不得删除已经原子发布的 raw revision。

### `app/jcc_data/`

建议新增 JCC 领域包：

- `snapshot_reader.py`：固定资源白名单、manifest、目录和旧 manifest 兼容；
- `canonical_hash.py`：文件 Hash、snapshot Hash 和后续可复用的规范化规则；
- `adapters.py`：英雄、羁绊、装备、强化符文、特殊机制和奇遇 adapter；
- `validation.py`：版本、字段、ID 和跨资源引用校验；
- `models.py`：JCC SQLAlchemy ORM 模型；
- `repository.py`：导入写入、current 指针和只读查询；
- `sync_service.py`：统一同步流程、幂等、事务和结果状态。

该包不负责用户 Token、HTTP 响应或 CDN 网络请求细节。

### `app/api/jcc.py` 与 `app/schemas/jcc.py`

- 新增 `/jcc/*` 外部业务 API；
- 使用 `CurrentUser = Depends(require_scope("jcc:data:read"))`；
- 只注册 GET 路由；
- 只从 current snapshot 查询数据库；
- 不挂载 raw 目录，不读取原始文件，不执行同步或导入；
- 资源响应使用 `extra="forbid"`、字符串外部 ID、明确的可空字段和 snapshot envelope。

### `app/main.py`、配置和 PDM

- [app/main.py](../app/main.py) 注册 JCC API Router，不改变现有 profile 和 records Router；
- [pyproject.toml](../pyproject.toml) 将同步脚本命令改为 `sync-jcc-data`；
- [alembic/env.py](../alembic/env.py) 显式导入 JCC ORM 模型；
- API 使用既有 `DATABASE_URL`、用户 JWT 和 Redis 用户状态配置；
- API 进程不需要 raw 目录挂载，统一同步命令才需要 raw 读写权限。

## 9. 接口与外部契约设计

### 9.1 认证和权限

新接口使用用户 Access Token，与 `/api/profile` 采用同一类用户认证链路：

```http
Authorization: Bearer <user-access-token>
```

路由统一声明：

```python
current_user: CurrentUser = Depends(require_scope("jcc:data:read"))
```

语义：

- Token 缺失、签名错误、issuer/audience 错误、过期、黑名单命中或 Session 撤销：`401`；
- Token 有效但不含 `jcc:data:read`：`403`；
- `jcc:data:read` 是用户权限/Scope，不是应用间 ResourceScope，不需要 App Service Grant；
- main 负责用户角色和权限配置，JCC 负责在路由上强制该 Scope；
- 不信任 `X-App-ID`、请求体中的 caller 或其他客户端自报身份；
- 不使用 `get_service_principal()`、Service Token、Basic 凭证或应用间 Token。

此处“外部 API”表示路径不属于 `/internal`，并不表示匿名开放。默认仍只允许已登录且具有 `jcc:data:read` 的用户调用。

### 9.2 共同响应

所有成功响应包含调用时读取的 snapshot 元数据：

```json
{
  "snapshot": {
    "mode": "18",
    "mode_name": "自然之力",
    "season": "S19",
    "version": "18.18.2",
    "revision": 2,
    "content_hash": "<sha256>",
    "source_updated_at": "2026-09-09 17:43:34"
  },
  "data": {}
}
```

列表响应：

```json
{
  "snapshot": {},
  "items": [],
  "total": 0,
  "limit": 50,
  "offset": 0
}
```

分页约束：

```text
limit: 1..100，默认 50
offset: >= 0，默认 0
```

列表按稳定的官方外部 ID、发布时间或明确业务排序返回；客户端不能传入任意 SQL 排序表达式。

### 9.3 当前快照

```http
GET /jcc/snapshot
Authorization: Bearer <user-access-token>
```

返回当前 mode 18 的版本、revision、Hash、来源更新时间和资源来源数量。没有成功导入的 current snapshot 时返回：

```http
503 Service Unavailable
```

固定业务错误码为 `JCC_DATA_UNAVAILABLE`，不即时读取 raw 文件。

### 9.4 英雄

```http
GET /jcc/heroes?name=&trait_id=&class_id=&price=&limit=&offset=
GET /jcc/heroes/{hero_id}
```

英雄列表返回可用于筛选的英雄数据和已关联的特质/职业；英雄详情返回完整英雄资料，包括：

- 官方外部 ID、名称、费用和类型；
- 生命值、攻击力、护甲、魔抗、攻击速度、攻击距离、初始/最大法力；
- 技能名称、技能描述、技能数值和图片 URL；
- 已解析的 race/job 名称、ID 和类型；
- snapshot 版本和来源元数据。

`hero_id` 只接受字符串形式的官方外部 ID。不存在于当前 snapshot 的英雄返回 `404 JCC_HERO_NOT_FOUND`，不通过错误响应暗示历史版本是否存在同 ID。

### 9.5 羁绊和职业列表

```http
GET /jcc/traits?kind=race|job&name=&limit=&offset=
```

不提供 `/jcc/traits/{id}` 详情接口。每个 `items` 元素直接返回完整的羁绊/职业内容：

- `kind`、官方 ID、名称、前缀、图标和地图信息；
- 最大等级和激活人数；
- 完整 tiers 数组，包含每档人数、等级、描述和真实描述；
- snapshot 元数据由列表外层统一返回。

### 9.6 装备列表

```http
GET /jcc/equipment?name=&type=&limit=&offset=
```

不提供 `/jcc/equipment/{id}` 详情接口。每个装备列表 item 直接返回：

- 官方 ID、名称、装备类型、基础属性和效果；
- 图片 URL；
- `components` 合成材料数组，每项包含材料 ID 和名称；
- 对没有合成材料的装备返回空数组，不返回伪造的 ID `0`。

示例形状：

```json
{
  "id": "2001",
  "name": "无尽之刃",
  "type": "成型装备",
  "description": "...",
  "components": [
    {"id": "1001", "name": "暴风之剑"},
    {"id": "1009", "name": "拳套"}
  ]
}
```

### 9.7 强化符文、特殊机制和开局奇遇列表

```http
GET /jcc/augments?name=&level=&limit=&offset=
GET /jcc/adventures?title=&price=&limit=&offset=
GET /jcc/galaxies?name=&limit=&offset=
```

以上接口均不提供详情路由；列表 `items` 直接返回每项完整结构化内容：

- 强化符文：ID、名称、等级、描述、图标及可审计的官方字段；
- 特殊机制：`adventureId`、标题、描述、价格、分类、图片/视频 URL 等稳定字段；
- 开局奇遇：ID、名称、描述和图片/视频 URL 等稳定字段。

特殊机制使用官方 `adventureId` 作为业务外部 ID，不假设 JSON object key 是唯一业务 ID。

### 9.8 错误和兼容性

| 场景 | HTTP 结果 | 响应原则 |
| --- | --- | --- |
| 缺少或无效用户 Token | `401` | 沿用用户认证固定错误，不返回 JWT 解析细节 |
| Token 有效但缺少 `jcc:data:read` | `403` | 固定 `insufficient_scope`，不返回资料 |
| Query/Path 参数格式错误 | `422` | 严格 Pydantic 校验，限制长度、范围和 ID 格式 |
| current snapshot 不存在 | `503` | `JCC_DATA_UNAVAILABLE`，不回退 raw |
| 英雄不存在 | `404` | `JCC_HERO_NOT_FOUND` |
| 数据库暂时不可用 | `503` | 固定服务不可用错误，不输出 SQL、连接串或堆栈 |

新 API 只增加新路径，不修改现有用户 `/api/profile`、内部 records、用户 Token claims 或 Redis 黑名单/Session 语义。新增响应字段优先采用可选字段；不把字符串外部 ID 改成数字，不在未升级路径版本时删除字段。

## 10. 配置、依赖和外部服务

### 10.1 配置

统一命令复用现有数据库配置和 raw 路径参数：

```bash
pdm run sync-jcc-data
pdm run sync-jcc-data --force-refresh
```

建议新增或统一以下非敏感配置名：

```dotenv
JCC_DATA_RAW_DIR=raw
JCC_DATA_MODE=18
JCC_DATA_MODE_NAME=自然之力
JCC_DATA_SYNC_TIMEOUT_SECONDS=30
JCC_DATA_SYNC_RETRIES=2
```

规则：

- API 不接受 raw 路径、CDN URL、revision 或 mode 作为客户端输入；
- 统一命令使用固定官方 endpoint，不把环境变量直接解释为任意下载地址；
- API 不需要挂载 raw；同步命令需要对 raw 根目录拥有读写权限；
- 数据库 URL、JWT 公钥、Redis URL 等既有配置继续按当前环境注入；
- 不新增应用间 Service Token、App Secret 或应用间 Grant 配置；
- 同步命令失败不打印 payload、Authorization、用户 Token、数据库密码或完整异常响应。

### 10.2 依赖

- 复用 Python 标准库 JSON、Hash、文件锁和 URL 请求能力；
- 复用 `fastapi`、`pydantic-settings`、`sqlalchemy`、`alembic` 和现有 PostgreSQL 驱动；
- 不新增向量数据库、Embedding SDK、消息队列或独立调度框架；
- 如果只做命名和功能调整，`pdm.lock` 依赖内容原则上不变，但仍执行锁文件检查。

### 10.3 外部服务契约

- 官方 CDN 是无认证、只读、固定 endpoint；同步命令使用现有超时、有限重试和固定 User-Agent；
- `/jcc/*` 不直接请求官方 CDN，只查询本地结构化数据库；
- 用户登录和 Access Token 由现有认证体系提供，JCC 只验证用户 Token，不签发或刷新 Token；
- 普通 CI 使用 fixture/fake fetcher，不访问真实 CDN；真实下载在临时 raw 目录和受控环境中执行。

## 11. 代码变更清单

以下为**计划新增/修改**文件，实际文件和迁移 revision 以阶段执行记录为准。

### 配置和依赖

- 修改 [pyproject.toml](../pyproject.toml)：将 `sync-lol-data` 改为 `sync-jcc-data`，不增加独立 `ingest-*` 命令；
- 视实现需要修改 [.env.test.example](../.env.test.example)、[.env.product.example](../.env.product.example)、[.env.deploy.example](../.env.deploy.example)：补充 `JCC_DATA_*` 非敏感配置；
- 修改 [pdm.lock](../pdm.lock) 仅限确有依赖变化，否则执行一致性检查。

### 同步、解析和领域模块

- 重命名并修改 [scripts/sync_lol_data.py](../scripts/sync_lol_data.py) 为 [scripts/sync_jcc_data.py](../scripts/sync_jcc_data.py)：统一命令、`--force-refresh`、revision、Hash、解析和数据库导入编排；
- 新增 `app/jcc_data/__init__.py`、`snapshot_reader.py`、`canonical_hash.py`、`adapters.py`、`validation.py`、`models.py`、`repository.py`、`sync_service.py`；
- 不创建 `app/lol_data/` 或 `lol_*` 结构化模型；
- 不把下载、解析、数据库导入逻辑放入 FastAPI startup 或请求处理函数。

### API、Schema 和权限

- 新增 [app/api/jcc.py](../app/api/jcc.py)：注册 `/jcc/snapshot`、`/jcc/heroes`、`/jcc/heroes/{hero_id}` 及其他资源列表 GET 路由；
- 新增 [app/schemas/jcc.py](../app/schemas/jcc.py)：完整列表 item、英雄详情、snapshot metadata、分页和错误响应 Schema；
- 修改 [app/main.py](../app/main.py)：注册 JCC API Router；
- 复用 [app/deps/auth.py](../app/deps/auth.py) 的 `get_current_user()`/`require_scope()`，不修改用户认证机制；
- 不在新资料 API 中使用 [app/deps/service_auth.py](../app/deps/service_auth.py)，不新增应用间 Service Token 或应用间 Grant。

### 模型和迁移

- 新增 `alembic/versions/<revision>_jcc_structured_data.py`：创建 `jcc_*` 快照、current、实体和关系表；
- 修改 [alembic/env.py](../alembic/env.py)：显式导入 JCC ORM 模型；
- 修改 [app/models/__init__.py](../app/models/__init__.py)：按仓库惯例导入 JCC ORM 模型；
- 不修改现有 `app_settings`、`sample_profiles`、用户认证表或 raw 历史目录。

### 测试和文档

- 修改 [tests/test_sync_lol_data.py](../tests/test_sync_lol_data.py) 或重命名为 [tests/test_sync_jcc_data.py](../tests/test_sync_jcc_data.py)：覆盖统一命令、默认复用、强制重检、Hash 匹配、revision 发布和失败重试；
- 新增 [tests/test_jcc_data_import.py](../tests/test_jcc_data_import.py)：覆盖资源解析、关系校验、幂等、事务失败和 current 切换；
- 新增 [tests/test_jcc_api.py](../tests/test_jcc_api.py)：覆盖用户 Token、`jcc:data:read`、路由形态、列表完整 item、英雄详情和错误契约；
- 更新 [README.md](../README.md)：统一同步命令、用户权限、`/jcc/*` API、revision 和回滚说明；
- 本方案实现后创建对应阶段计划和阶段执行记录，真实记录测试与部署结果。

## 12. 异常处理与可观测性

### 12.1 同步和导入结果

| 场景 | 命令结果 | 内部处理 |
| --- | --- | --- |
| 官方版本配置无唯一当前版本 | `failed`，非零退出 | 不保存不完整资源，历史 raw/数据库不变 |
| 普通模式已有完整 raw 且已入库 | `skipped` | 不下载、不重复导入、不切换 current |
| 普通模式已有完整 raw 但未入库 | `updated` 或 `retry` | 复用 raw，继续解析和数据库导入 |
| 强制检查 Hash 命中历史 revision | `matched/skipped` | 不新建目录；若未入库则继续导入对应 revision |
| 强制检查 Hash 全新 | `updated` | 原子发布新 `-rN` raw 目录并执行完整导入 |
| 已发布 revision 目录 Hash 不匹配 | `failed` | 禁止覆盖，旧 revision 和 current 保持不变 |
| 解析/关系校验失败 | `failed` | 数据库事务不开始或整体回滚，raw 保留 |
| 数据库不可用 | `failed` | raw 保留，旧 current 可读，下次命令重试 |
| current 回滚失败 | `failed` | 原 current 指针不变 |

### 12.2 API 日志与监控

同步日志至少记录：`operation=sync_jcc_data`、结果、mode、season、version、revision、Hash 前缀、资源计数、耗时和错误类别。API 日志依赖现有 Request ID，记录路由模板、snapshot version/revision、状态码、分页大小和耗时。

禁止记录：完整 raw payload detail 全文、用户 Authorization、Access Token、数据库连接串、JWT 公钥内容、用户敏感信息或完整异常堆栈。

建议部署平台监控：同步成功版本和 revision、同步失败次数、导入耗时、current snapshot 是否存在、API 401/403/5xx/503、数据库容量、raw 磁盘容量和列表 API 延迟。第一版不新增独立监控系统。

## 13. 安全与权限要求

1. 新资料 API 使用现有用户 Token 校验链路，所有端点统一要求 `jcc:data:read`；不接受匿名请求、Service Token 或客户端自报身份。
2. `jcc:data:read` 应作为用户权限加入 main 的角色/权限配置，并由 main 签发到用户 Access Token；JCC 只做本地 Scope 检查，不保存或管理用户权限源数据。
3. 新路由使用 `/jcc/*`，但“外部路径”不等于匿名公开；生产是否允许公网到达仍由网关、网络和用户认证共同控制。
4. 不暴露 raw 路径、任意文件名、临时目录、未审计 `source_attributes` 或任意 CDN URL，避免路径遍历、文件泄露和 SSRF。
5. Query 参数使用严格 Pydantic 类型、长度和范围限制；排序字段使用固定白名单，数据库查询使用绑定参数。
6. 所有响应只返回白名单字段；外部 ID 统一以字符串表示；装备 components、英雄 traits/classes嵌套关系不能通过原始 JSON 透传。
7. API 进程不需要 raw 目录访问权限；统一同步命令需要 raw 读写和数据库写权限，部署时应限制其运行身份和文件权限。
8. 导入必须 fail closed：字段、版本、Hash 或关系异常时不发布 current，不用空关系或旧版本数据拼接新版本。
9. 用户 Token 的黑名单和 Session 撤销继续由现有 Redis 依赖执行；新资料 API 不绕过既有用户认证检查。
10. 本期不提供任何更新、删除、作废或恢复 HTTP 接口；current 回滚属于受控运维操作，不能由普通用户触发。

## 14. 测试与验收

### 14.1 单元测试

- 官方资源包装、字段别名、ID 字符串、Decimal/整数转换和未知非必需字段处理；
- `trait.checkId` tiers 聚合、英雄 race/job 关系、装备 recipe 和 `adventureId` 解析；
- canonical JSON 和 snapshot Hash 对 key 顺序、空白差异、数组顺序及任意单文件变化的结果；
- 普通模式复用 raw、强制检查 Hash 命中、Hash 新增生成 revision、revision 目录不可覆盖、失败清理和历史目录保留；
- raw 下载成功但数据库失败后再次运行可重试导入；
- 同 Hash 幂等、同 revision 不同 Hash 拒绝、current 原子切换和 current 回滚验证；
- `/jcc/heroes` 列表、`/jcc/heroes/{hero_id}` 详情、其他资源列表完整 item 和装备 components；
- 未提供 traits/equipment/augments/adventures/galaxies 详情路由；
- 缺用户 Token、错误 Token、缺 `jcc:data:read`、参数错误、资源不存在和无 current snapshot；
- 现有 `/api/profile`、`/internal/v1/records` 和 Redis 用户状态检查无回归。

### 14.2 集成与迁移测试

使用随机、一次性 PostgreSQL 测试数据库，不连接共享开发或生产数据库：

```text
现有 Alembic head
  → 新 jcc structured data migration
  → 验证 jcc_* 表、外键、唯一约束和索引
  → 导入最小完整 fixture
  → 验证 current 指针和列表/详情查询
  → downgrade（仅隔离环境）
  → 确认新增对象删除且既有表/数据不受影响
  → upgrade head
```

PostgreSQL 集成测试需覆盖 JSONB、外键、唯一约束、事务回滚和 mode 级并发锁。测试资源必须可靠清理，失败时报告遗留资源，不退回共享数据库。

### 14.3 API 与命令验证

```bash
pdm run pytest tests/test_sync_jcc_data.py tests/test_jcc_data_import.py tests/test_jcc_api.py -q
pdm run ruff check app/jcc_data app/api/jcc.py app/schemas/jcc.py scripts/sync_jcc_data.py tests/test_sync_jcc_data.py tests/test_jcc_data_import.py tests/test_jcc_api.py
pdm run alembic-current
pdm lock --check
pdm run test
git diff --check
```

如果实施阶段选择保留测试文件旧名称，执行记录必须使用实际路径；不存在的计划路径不能标记为已执行。

### 14.4 真实环境验证

以下项目需要明确授权，默认不进入普通 CI：

- 临时 raw 目录执行真实 CDN 下载和 `--force-refresh`，确认 Hash、revision 和 manifest；
- 隔离 PostgreSQL 执行 migration、完整 snapshot 导入、重复导入、同版本修订和 current 回滚；
- 使用测试用户 Token 验证 `/jcc/*` 外部路径和 `jcc:data:read` 权限；
- 在部署环境验证统一同步命令的 raw/数据库权限隔离、失败重试、历史保留和应用回滚；
- 生产迁移、生产 CDN 下载、生产权限变更、生产 current 回滚、历史数据清理或匿名公网开放。

## 15. 部署、迁移与回滚检查清单

### 配置与权限

- [ ] main 用户权限体系已配置 `jcc:data:read`，批准的用户角色可获得该 Scope；
- [ ] API 的 JWT issuer、audience、公钥和 Redis 黑名单/Session 配置已在 test 环境验证；
- [ ] 未配置任何新资料 API 的 Service Token、App Secret 或应用间 Grant；
- [ ] 同步命令使用安全 raw 路径，API 不挂载 raw；
- [ ] 真实用户 Token、数据库密码和其他凭证通过安全渠道注入，不写入文档、raw 或日志。

### 数据与迁移

- [ ] Alembic 新迁移已在隔离 PostgreSQL 完成 upgrade/downgrade/upgrade；
- [ ] 至少一个完整 snapshot 成功导入，所有核心关系和列表查询有验证证据；
- [ ] 同版本强制检查的 Hash 命中和新 revision 发布有验证证据；
- [ ] raw 下载成功但数据库失败时旧 current 保持可读，重试可完成导入；
- [ ] 历史 raw 和结构化 snapshot 不因普通发布或重启被删除；
- [ ] 磁盘、索引、列表响应体和历史保留容量已评估。

### API 与兼容性

- [ ] 新接口路径为 `/jcc/*`，不是 `/internal/v1/*`；
- [ ] 所有接口要求用户 `jcc:data:read`，仅英雄有详情接口；
- [ ] 非英雄资源详情路由不存在，完整内容位于列表 item；
- [ ] 装备 components 位于装备列表 item；
- [ ] `/api/profile`、`/internal/v1/records` 和既有用户认证回归通过；
- [ ] 401/403/404/422/503 和 Request ID 行为已验证。

### 回滚策略

1. 应用镜像回滚不删除 `jcc_*` 表、历史 raw 目录或已导入 snapshot。
2. 新 raw revision 导入失败时，不切换 current；API 继续读取旧 current snapshot。
3. 已切换的 current 发现问题时，运维确认目标旧 snapshot 完整，在事务内锁定 mode 记录并将 `snapshot_id` 切回旧值，再用 `/jcc/snapshot` 和列表接口验证。
4. 生产优先使用前向修复和受控 current 回滚，不执行未经备份和批准的 Alembic downgrade。
5. 结构化解析器修复后，重新运行统一 `sync-jcc-data`；保留原始 revision 作为重建输入，不手动删除来“触发”更新。

## 16. 分阶段实施顺序

本方案分为两个阶段。每个阶段完成并验证后再进入下一阶段。

### 第一阶段：统一同步与结构化入库

> 状态：已完成
>
> 阶段计划：[JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PHASE_1_PLAN.md](JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PHASE_1_PLAN.md)
>
> 执行记录：[JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PHASE_1_EXECUTION.md](JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PHASE_1_EXECUTION.md)
>
> 验收结论：统一 `sync-jcc-data`、raw revision/hash、结构化模型、事务导入和 current 切换已经实现；定向测试、全量测试、定向 Ruff、隔离 PostgreSQL migration round-trip、真实仓库 raw 快照导入、Alembic metadata check、锁文件和 diff 检查通过。未执行真实 CDN 或生产环境操作，不影响本地第一阶段代码验收。

前置依赖：

- 当前官方 raw 快照格式和基础同步逻辑可复用；
- PostgreSQL/Alembic 隔离测试环境可用；
- 已确认统一命令同时负责下载、解析和入库；
- 不执行生产 CDN、生产数据库或生产权限变更。

开发内容：

1. 将同步代码和命令统一重命名为 `sync_jcc_data.py`/`sync-jcc-data`；
2. 增加 `--force-refresh`、canonical JSON Hash、全历史 revision 匹配和不可覆盖的 `-rN` raw 目录；
3. 新增 `jcc_*` ORM 模型、Alembic migration、source adapter、关系校验和事务导入；
4. 使一次 `pdm run sync-jcc-data` 完成版本发现、资源下载、raw 保存、解析、数据库导入和 current 切换；
5. 增加数据库失败后复用 raw 重试、同 Hash 幂等和 current 回滚的测试；
6. 更新环境/README 和创建本阶段计划、执行记录。

本阶段不实现：

- `/jcc/*` HTTP 资料 API；
- 用户权限 `jcc:data:read` 的生产配置变更；
- Service Token、应用间 Grant 或 raw 文件 HTTP 访问；
- Embedding、全文/向量检索。

阶段验收：

- 普通同步复用完整 raw，强制检查能发现同版本局部修订；
- Hash 命中旧 revision 时不重复保存，Hash 新增时生成新的完整 `-rN` revision；
- 任意历史目录不被覆盖或删除，下载/入库失败不会污染旧数据；
- 完整 raw revision 可形成完整结构化 snapshot，事务失败后 current 不变；
- `pdm run sync-jcc-data` 定向测试、迁移测试、lint 和 diff 检查结果真实记录。

### 第二阶段：用户 Token 保护的 `/jcc/*` 只读 API

> 状态：未开始
>
> 阶段计划：待创建
>
> 执行记录：待创建

前置依赖：

- 第一阶段结构化 snapshot 和 current 查询能力已通过自动化测试；
- 用户 Token 中的 `jcc:data:read` 权限可在 test 环境配置或通过受控 fixture 模拟；
- 现有 `/api/profile` 用户认证回归通过。

开发内容：

1. 新增 `/jcc/snapshot`、`/jcc/heroes`、`/jcc/heroes/{hero_id}` 和其他资源列表接口；
2. 所有路由复用用户 `get_current_user`/`require_scope("jcc:data:read")`；
3. 英雄提供详情接口，其他资源只提供完整列表 item；装备 components 直接放在列表 item；
4. 增加分页、固定过滤、snapshot envelope、字段白名单和错误处理；
5. 更新 README、总方案、阶段计划和执行记录。

本阶段不实现：

- Service Token、应用间 Grant、main 调用 JCC 的内部资料链路；
- `/internal/v1` 资料路由、raw 文件读取、HTTP 写入/作废/版本切换；
- 非英雄资源详情接口；
- 全文、向量、Embedding、Agent 回答和匿名公网开放。

阶段验收：

- 新 API 使用 `/jcc/*` 路径和用户 Token，不使用 Service Token；
- 所有新 API 统一要求 `jcc:data:read`，无权限返回 403；
- 英雄列表/详情可用，其他资源只有列表，列表 item 包含完整内容；
- 装备列表 item 包含合成 components；
- current 切换后响应 snapshot metadata 与实际查询版本一致；
- 既有 `/api/profile`、`/internal/v1/records`、用户黑名单/Session 行为无回归。

## 17. 风险、待确认项与决策记录

### 17.1 风险

| 风险 | 影响 | 缓解措施 | 状态 |
| --- | --- | --- | --- |
| 官方字段结构变化 | 解析失败或关系不完整 | adapter 分层、必需字段严格校验、关系 fail closed、fixture/真实快照契约测试 | 开放 |
| 官方版本号不变但局部资源改变 | 旧 raw 与官方实际内容不一致 | 仅显式 `--force-refresh` 全量重检，Hash 变化保存新的 `-rN` revision | 已缓解于设计 |
| 完整列表响应过大 | 延迟和内存增长 | 分页上限 100、字段白名单、按资源评估响应体，必要时后续新增专门分页契约 | 开放 |
| 全量结构化 snapshot 占用空间 | PostgreSQL 和 raw 容量增长 | 历史保留不自动删除，监控容量，后续单独制定保留策略 | 开放 |
| 用户权限配置遗漏 | 合法用户无法调用 API | main 角色配置、test Token fixture 和 403 回归测试 | 开放 |
| 对外接口误被理解为匿名公网 | 数据暴露或滥用 | 保持用户 Token、网关访问控制和限流；文档明确“外部路径不等于匿名” | 已缓解于设计 |
| 图片/官方资料再分发限制 | 对外扩展存在合规风险 | 首期只返回官方 URL、内部受保护调用；上线前确认使用许可 | 开放 |

### 17.2 待确认项

当前没有阻塞实施的业务待确认项。以下属于上线前运维或合规核对，不改变已确认的接口和数据边界：

| 项目 | 建议默认值 | 影响阶段 |
| --- | --- | --- |
| raw/结构化历史保留周期 | 第一版不自动删除，先监控 | 第一阶段上线前 |
| `/jcc/*` 网关暴露范围 | 仅登录用户和受信任业务入口 | 第二阶段上线前 |
| `jcc:data:read` 角色归属 | 由 main 管理的指定用户角色 | 第二阶段联调 |
| 官方图片和内容的对外展示许可 | 首期不代理图片、不匿名公开 | 第二阶段上线前 |

### 17.3 方案决策记录

| 决策 | 原因 | 替代方案 | 确认来源 |
| --- | --- | --- | --- |
| 文件、表和模块统一使用 `jcc` 命名 | 资源属于 JCC，避免 `lol` 命名继续扩散 | 保留 `lol_*`；不采用 | 用户确认 |
| 一个 `sync-jcc-data` 命令编排下载、解析和入库 | 调用入口简单，失败重试路径统一 | 下载、解析和入库分别由多个命令触发；不采用 | 用户确认 |
| 强制检查重新下载整套资源并按 Hash 生成 revision | 发现同版本局部修订且不破坏历史 | 手动删除 raw 或就地覆盖；不采用 | 用户确认 |
| raw 和结构化 snapshot 均保存完整集合 | 关系一致、回滚简单，RAG 可按文档增量 | 只更新当前表中变化记录；不采用 | 数据一致性设计 |
| 新 API 使用用户 Token 和 `jcc:data:read` | 与 `/api/profile` 一致，适用于登录用户场景 | Service Token/应用间 Grant；不采用 | 用户确认 |
| 只有英雄提供详情接口 | 控制接口数量，其余资源列表一次返回完整 item | 所有资源都提供详情；不采用 | 用户确认 |
| 装备 components 放在列表 item | 调用方一次获取装备和合成材料 | 单独装备详情或合成接口；不采用 | 用户确认 |

## 18. 完成标准

最终端到端链路：

```text
pdm run sync-jcc-data [--force-refresh]
  ↓
官方版本发现与整套资源下载
  ↓
raw/<version>-<season>[-r<N>]/ 完整快照 + manifest + content_hash
  ↓
JCC adapter 解析、关系校验和数据库事务导入
  ↓
jcc_current_snapshots[mode=18] 指向完整结构化 snapshot
  ↓
用户登录获得包含 jcc:data:read 的 Access Token
  ↓
/jcc/* 读取 current snapshot
  ↓
返回结构化资源、关系、版本和来源元数据
```

方案完成至少要求：

- 统一 `sync-jcc-data` 真实包含版本发现、下载、raw 保存、解析、入库和 current 切换；
- 普通模式、`--force-refresh`、全历史 Hash 匹配、`-rN` revision、失败重试和历史保留均有测试证据；
- 文件、模块和表按 `jcc` 命名，不新增 `lol_*` 结构化模型；
- 数据库以完整 snapshot 方式发布，current 切换可验证、可回滚，不提供 HTTP 更新/删除/作废接口；
- 新接口路径为 `/jcc/*`，使用用户 Token 和统一 `jcc:data:read`；
- 英雄有列表和详情，其他资源只有列表且 item 返回完整内容，装备 item 包含 components；
- 不存在应用间 Service Token 资料接口、raw 文件接口、匿名开放接口或独立部署进程；
- 现有用户认证、`/api/profile`、`/internal/v1/records` 和 Redis 用户状态无回归；
- 迁移、定向测试、全量测试、lint、锁文件、回滚和真实环境验证结果均如实记录；
- 未执行的 CDN、生产迁移、权限发布和公网开放验证明确标为待执行，不伪造为已完成。
