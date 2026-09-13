# JCC 官方资料同步、结构化入库与只读 API：第二阶段“用户 Token 保护的 `/jcc/*` 只读 API”实现计划

> 状态：已完成
>
> 总实施方案：[JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PLAN.md](JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PLAN.md)
>
> 阶段执行记录：[JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PHASE_2_EXECUTION.md](JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PHASE_2_EXECUTION.md)
>
> 范围：基于第一阶段 current snapshot 提供用户 Token 保护的 `/jcc/*` 只读 API；不提前实现 Service Token 资料接口、raw 读取、HTTP 版本管理或向量检索。

## 1. 背景与阶段基准

### 1.1 前置阶段状态

第一阶段已经完成，并由[第一阶段执行记录](JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PHASE_1_EXECUTION.md)提供验证证据：

- `sync-jcc-data` 已将完整 raw revision 解析并事务导入 11 张 `jcc_*` 表；
- `jcc_current_snapshots` 与 `get_current_snapshot()` 已提供 mode 级 current 指针；
- 英雄—羁绊、羁绊档位、装备—材料关系均限定在同一个 snapshot；
- 定向测试、全量测试、隔离 PostgreSQL migration round-trip 和真实仓库 raw 快照导入已经通过；真实 CDN 与生产操作未执行，但不阻塞本阶段本地实现。

### 1.2 当前仓库事实

- [app/jcc_data/models.py](../app/jcc_data/models.py) 已包含本阶段全部实体与关系字段，本阶段不需要新迁移；
- [app/jcc_data/repository.py](../app/jcc_data/repository.py) 已有 `get_current_snapshot()` 与 `read_snapshot_entities()`，但尚无分页、筛选和关系聚合查询；
- [app/deps/auth.py](../app/deps/auth.py) 已实现用户 JWT、黑名单、Session 撤销和 `require_scope()`；`/api/profile` 已使用同一用户认证链路；
- [app/deps/service_auth.py](../app/deps/service_auth.py) 只服务于现有内部接口，本阶段不得复用到 `/jcc/*`；
- [app/main.py](../app/main.py) 尚未注册 JCC 资料路由；`app/api/jcc.py`、`app/schemas/jcc.py` 和 `tests/test_jcc_api.py` 尚不存在；
- 实施开始前 Git 工作区干净，分支为 `chore/jcc-local-infra`；总方案与代码没有阻塞本阶段的冲突。

### 1.3 本阶段目标

1. 提供 `/jcc/snapshot`、英雄列表/详情和其余五类资源列表接口，所有成功响应携带同一请求固定的 snapshot 元数据；
2. 所有新接口统一要求用户 `jcc:data:read`，实现固定筛选、稳定分页、字段白名单以及 404/422/503 错误契约；
3. 补齐 API、current 切换、关系聚合、认证、日志和既有接口回归测试，并同步 README、总方案、阶段计划和执行记录。

## 2. 范围与约束

### 2.1 本阶段实现

- 八个 `/jcc/*` GET 接口：snapshot、heroes 列表/详情、traits、equipment、augments、adventures、galaxies；
- 用户 Access Token 与 `jcc:data:read` Scope；
- 当前快照固定、分页统计、固定筛选、关系批量聚合和稳定排序；
- 严格 Pydantic 响应模型，不暴露 ORM、内部 ID、raw 路径、manifest 或未审计字段；
- 无 current、英雄不存在、数据库异常、参数错误的固定 HTTP 行为；
- 请求编号关联的安全 JCC 查询日志；
- SQLite 隔离 API 测试及现有认证、内部接口、Redis 状态和日志回归。

### 2.2 本阶段明确不实现

- Service Token、应用间 Grant、main→JCC 内部资料调用链或 `/internal/v1` 资料路由；
- traits、equipment、augments、adventures、galaxies 详情接口；
- POST/PUT/PATCH/DELETE、HTTP 同步、导入、current 切换、回滚、删除或作废能力；
- raw 文件下载、任意路径读取、CDN 代理或 API 进程 raw 挂载；
- 阵容结构化 API、全文检索、Embedding、向量检索、Agent 回答或匿名公网开放；
- main 的生产角色权限配置、真实 CDN 调用、生产迁移、部署或公网网关变更。

### 2.3 已确认约束

- 路径固定为 `/jcc/*`，只使用用户 Token；所有端点统一要求 `jcc:data:read`；
- 英雄同时提供列表和详情，其他资源只提供完整列表 item；装备合成材料位于列表 item 的 `components`；
- 每个请求只解析一次 current snapshot，后续 count、实体和关系查询都限定其 `snapshot_id`；
- 对外 ID 保持字符串；所有列表默认 `limit=50`，范围 `1..100`，`offset>=0`；
- 不把 `source_attributes`、`source_manifest`、raw 目录名、数据库内部 ID 或 SQL 错误暴露给客户端；
- 不改变 `/api/profile`、`/internal/v1/records`、用户 JWT、Service Token 或 Redis 用户状态语义。

### 2.4 临时数据与隔离测试规则

- API 单元/集成测试使用 SQLite 内存数据库、`StaticPool` 和 FastAPI `get_db` 依赖覆盖，不连接长期开发或生产 PostgreSQL；
- 数据通过第一阶段 `import_snapshot()` 写入测试数据库，测试结束关闭 Session、删除 metadata 并释放 engine；
- 认证测试使用固定非生产 RSA 测试密钥，黑名单和 Session 检查使用测试替身，不连接共享 Redis；
- 不使用 `FLUSHDB`、`FLUSHALL`，不清理共享 Key，不保存或输出真实 Token、Secret、数据库连接串或响应数据全文；
- 如执行真实 PostgreSQL 验证，只能使用随机一次性数据库并在 `finally` 中清理；本阶段没有 schema 变化，因此默认无需重复 migration round-trip。

### 2.5 前置依赖与环境条件

| 依赖 | 所需状态 | 当前状态 | 不满足时的处理 |
| --- | --- | --- | --- |
| 第一阶段结构化表/current | 模型、关系和查询入口可用 | 已满足 | 若测试发现不一致则 fail closed，不绕过数据库读取 raw |
| 用户认证 fixture | 可签发含 `jcc:data:read` 的测试 Token | 已满足 | 只记录真实跨服务 Token 联调为待执行 |
| Python/PDM | 可执行 pytest、Ruff 和锁文件检查 | 已有项目环境 | 未执行项如实记录，不预设通过 |
| 长期开发数据库 | 仅用于可选只读 smoke | 待运行时确认 | 不迁移、不写入；不可用则记录未执行 |

## 3. 详细设计与修改文件

### 3.1 API Schema 与字段白名单

新增 [app/schemas/jcc.py](../app/schemas/jcc.py)：

- 所有响应模型继承 `extra="forbid"` 的严格基类；
- `JccSnapshotMetadata` 只返回 mode、mode_name、season、version、revision、content_hash、source_updated_at；
- 通用 `JccDataResponse[T]` 和 `JccListResponse[T]` 分别表示详情与分页响应；
- 英雄返回完整结构化属性、技能、图片、`traits` 和 `classes`；关系项返回字符串 ID、名称和 kind；
- 羁绊 item 包含 activation list 和按 tier_order 排序的完整 tiers；
- 装备 item 包含按合成槽位排序的 components；无 recipe 时为空数组；
- augment、adventure、galaxy 只返回显式列出的稳定字段；不返回 source_attributes。

`attack_speed` 使用 JSON 数值；其他 Decimal、ORM 或 manifest 对象不能直接透传。

### 3.2 current snapshot 只读仓储

修改 [app/jcc_data/repository.py](../app/jcc_data/repository.py)：

1. 复用 `_current_snapshot()`，由每个公共只读查询在开始时固定一次 snapshot；无 current 抛出 `LookupError`；
2. 增加通用 snapshot、分页和详情结果对象，携带 ORM snapshot 与已聚合数据；
3. heroes 支持 name、race `trait_id`、job `class_id`、price；关系筛选使用关联子查询，避免联表产生重复 total；
4. traits/equipment/augments/adventures/galaxies 使用 snapshot 条件、固定字段筛选、count 和分页；
5. 名称/标题使用 SQLAlchemy 绑定参数及自动转义的包含匹配；
6. 所有主列表按 `external_id` 长度再按值升序，兼顾当前纯数字官方 ID 的数值直觉和一般字符串 ID 的确定性；英雄关系、tiers 和 components 分别按 position、tier_order 和 recipe 槽位；
7. 当前页实体确定后批量读取关系，避免逐条 N+1；英雄详情只查固定 snapshot，不检查历史快照。

本阶段不修改持久数据、事务导入、current 切换或数据库 schema。

### 3.3 `/jcc/*` 路由和错误边界

新增 [app/api/jcc.py](../app/api/jcc.py)，修改 [app/main.py](../app/main.py)：

- router prefix 为 `/jcc`，统一挂载 `Depends(require_scope("jcc:data:read"))`；
- 注册 `GET /snapshot`、`GET /heroes`、`GET /heroes/{hero_id}`、`GET /traits`、`GET /equipment`、`GET /augments`、`GET /adventures`、`GET /galaxies`；
- 参数使用 FastAPI/Pydantic 的长度、范围、Literal 和安全 ID 正则；查询文本 trim 后不得为空；
- `LookupError` 转换为 `503 {"detail":"JCC_DATA_UNAVAILABLE"}`；英雄空结果转换为 `404 {"detail":"JCC_HERO_NOT_FOUND"}`；
- `SQLAlchemyError` 先安全回滚 Session，再记录错误类型并转换为固定 503；
- 路由只负责参数、鉴权、仓储调用、错误转换和 Schema 映射，不读取 raw、不执行导入、不调用 Service Auth。

### 3.4 请求日志与可观测性

修改 [app/core/logging.py](../app/core/logging.py)：

- 保留现有 `X-Request-ID` 生成/回传和敏感值脱敏；
- FastAPI handler 将可信、非敏感的 snapshot version/revision、limit、offset 写入 request state；
- middleware 在既有 request completion 日志中加入解析后的路由模板和上述 JCC 上下文；
- 状态码和耗时使用 middleware 的真实结果，不单独生成可能不一致的完成日志；
- 不记录 Authorization、完整查询结果、SQL、连接串或异常堆栈。

### 3.5 测试与文档

新增 [tests/test_jcc_api.py](../tests/test_jcc_api.py)：

- 构造一次性 FastAPI + SQLite 上下文，复用用户 Token fixture 和第一阶段导入入口；
- 覆盖八个接口、所有筛选、分页、排序、关系、snapshot 一致性、current 切换、错误、鉴权和 OpenAPI 边界；
- 验证非英雄详情与写方法不存在，Service Token 不能用于用户资料接口；
- 验证请求日志只包含安全元数据与 Request ID。

更新 [README.md](../README.md)、总方案和本阶段三类文档，区分本地验证与未执行的真实跨服务/生产验证。

## 4. 实施步骤

1. 创建本阶段计划和执行记录，更新总方案第二阶段链接与实施状态；
2. 新增严格响应 Schema；
3. 扩展 repository 的固定 snapshot 查询、筛选、分页和批量关系聚合；
4. 新增 JCC router、错误转换和 app 注册；
5. 扩展请求完成日志的安全 JCC 上下文；
6. 新增 API 测试并按失败结果修正实现；
7. 更新 README，执行定向测试、定向 Ruff、编译、全量测试、Alembic 状态、锁文件和 diff 检查；
8. 按真实结果更新总方案、阶段计划和执行记录；未授权的外部或生产验证继续标记为未执行。

## 5. 测试与验证计划

### 5.1 定向测试

| 测试文件/范围 | 覆盖行为 | 预期结果 |
| --- | --- | --- |
| `tests/test_jcc_api.py` | 八个接口、筛选、分页、排序、关系、current、错误、认证、OpenAPI、日志 | 全部接口只读且固定 snapshot，契约与权限符合总方案 |
| `tests/test_jcc_data_import.py` | 第一阶段 current、导入、关系和回滚 | 仓储扩展不破坏导入行为 |
| `tests/test_profile_api.py` | 用户 Token、Scope、黑名单、Session、OpenAPI | 既有用户认证无回归 |
| `tests/test_internal_api.py` | Service Token 与内部 records | 鉴权模型保持隔离 |
| `tests/test_redis_state_services.py`, `tests/test_logging.py` | 用户状态和日志脱敏/Request ID | 既有安全行为无回归 |

### 5.2 回归与质量检查

```bash
pdm run pytest tests/test_jcc_api.py -q
pdm run pytest tests/test_jcc_data_import.py tests/test_profile_api.py tests/test_internal_api.py tests/test_redis_state_services.py tests/test_logging.py -q
pdm run ruff check app/api/jcc.py app/schemas/jcc.py app/jcc_data/repository.py app/main.py app/core/logging.py tests/test_jcc_api.py tests/test_logging.py
python -m compileall -q app tests
pdm run test -q
pdm run alembic-current
pdm lock --check
git diff --check
```

Ruff 只检查本阶段新增或修改的 Python 文件，不执行全仓 lint。

### 5.3 真实环境验证

- 普通 CI/本地测试不访问真实 CDN、不调用生产 main、不修改生产角色或 Token；
- 如本地长期数据库已经迁移且已有 current snapshot，可在不写入的前提下用受控测试用户 Token 执行只读 smoke；否则标记为未执行；
- main 角色配置、真实跨服务用户 Token、网关访问、官方内容许可、生产迁移与部署均属于上线前受控验证，不是本阶段自动化通过项。

## 6. 验收标准与追踪

| 编号 | 验收标准 | 实现位置 | 验证方式 | 状态 |
| --- | --- | --- | --- | --- |
| AC-2-01 | 八个新接口均位于 `/jcc/*` 且只注册 GET | `app/api/jcc.py`, `app/main.py` | 路由/OpenAPI 测试 | 已满足 |
| AC-2-02 | 所有新接口使用用户 Token 并统一要求 `jcc:data:read`，不接受 Service Token | `app/api/jcc.py`, `app/deps/auth.py` | 401/403/Service Token 测试 | 已满足 |
| AC-2-03 | 英雄列表/详情完整，其他资源只有完整列表 item | repository、Schema、router | 成功响应和路由不存在测试 | 已满足 |
| AC-2-04 | 装备 item 包含有序 components，无材料时为空数组 | repository、Schema | recipe 测试 | 已满足 |
| AC-2-05 | 分页、固定筛选、稳定排序和参数 422 契约有效 | repository、router | 参数化 API 测试 | 已满足 |
| AC-2-06 | 每个响应的数据与 snapshot metadata 属于同一固定 current snapshot | repository | current 切换/固定 snapshot 测试 | 已满足 |
| AC-2-07 | 无 current、未知英雄、数据库异常分别符合 503/404/503 固定错误且不泄露底层信息 | router | 错误路径测试 | 已满足 |
| AC-2-08 | `/api/profile`、`/internal/v1/records`、黑名单、Session、Request ID 无回归 | 现有模块 | 定向回归和全量测试 | 已满足 |
| AC-2-09 | README、总方案、阶段计划和执行记录同步，验证结果真实记录 | 文档 | 文件核对 | 已满足 |

## 7. 风险、回滚与异常处理

| 风险或失败场景 | 影响 | 预防/检测 | 回滚或恢复 |
| --- | --- | --- | --- |
| current 在多次查询间切换 | 响应混用版本 | 请求先固定 snapshot ID，所有查询显式限定 | 当前请求继续旧 snapshot；下个请求读取新 current |
| 关系 join 重复实体 | total/分页错误 | EXISTS 筛选、先分页实体再批量关系 | 修正查询，不修改数据 |
| 完整 item 响应过大 | 延迟和内存增加 | 默认 50、上限 100、字段白名单 | 客户端减小 limit；后续另行评估契约 |
| 数据库不可用 | 资料接口失败 | 固定 503、安全日志、旧数据不变 | 数据库恢复后重试 GET |
| Scope 配置遗漏 | 合法用户 403 | test fixture 和上线前 main 角色检查 | 修复 main 权限配置，不放宽 JCC 鉴权 |
| 应用回滚 | 新 API 暂时不可用 | 本阶段无迁移、只增加路由 | 回滚镜像；结构化表与 raw 不变 |

本阶段没有数据库写入或外部副作用；应用回滚不删除 `jcc_*` 表、raw 或 snapshot。

## 8. 阶段交付物

代码与配置：

- `app/api/jcc.py`、`app/schemas/jcc.py`；
- `app/jcc_data/repository.py`、`app/main.py`、`app/core/logging.py` 的最小配套修改；
- 不新增依赖、配置或迁移。

测试：

- `tests/test_jcc_api.py`；
- 现有 JCC 导入、用户认证、内部认证、Redis 状态和日志回归。

文档：

- 更新[总实施方案](JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PLAN.md)的阶段状态与链接；
- 更新本阶段计划的状态和设计调整；
- 创建或更新[第二阶段执行记录](JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PHASE_2_EXECUTION.md)；
- 更新 [README.md](../README.md) 的接口使用说明。

## 9. 计划调整记录

| 调整项 | 原计划 | 调整后 | 原因 | 对总方案/后续阶段的影响 |
| --- | --- | --- | --- | --- |
| 官方 ID 排序 | 按字符串 `external_id` 升序 | 按 ID 长度、再按字符串值升序 | 当前官方 ID 是不等长数字字符串，单纯字典序会把 `100` 排在 `20` 前；长度优先能保持稳定且符合数字直觉，并仍兼容一般字符串 ID | 仅明确排序语义，不改变字段或分页契约 |
| 真实样本 smoke | 只在长期数据库已有 current 时执行 | 将现有仓库 raw 导入一次性 SQLite，并对八个接口执行内存 smoke | 无需写入长期数据库即可验证完整真实样本的 Schema 和关系查询 | 增加无长期副作用的验收证据；生产联调仍未执行 |
