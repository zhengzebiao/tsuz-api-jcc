# JCC 官方资料同步、结构化入库与只读 API：第二阶段“用户 Token 保护的 `/jcc/*` 只读 API”执行记录

> 状态：已完成
>
> 执行日期：2026-09-13
>
> 总实施方案：[JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PLAN.md](JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PLAN.md)
>
> 阶段实现计划：[JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PHASE_2_PLAN.md](JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PHASE_2_PLAN.md)

## 1. 执行范围与结论

本次根据总方案完成第二阶段“用户 Token 保护的 `/jcc/*` 只读 API”。

阶段结论：八个 `/jcc/*` GET 接口、用户 `jcc:data:read`、固定 current snapshot 查询、筛选分页、完整关系响应、字段白名单、固定错误与安全日志均已落地；定向测试、相关回归、全量测试、真实仓库 raw 的一次性内存 API smoke 和质量检查通过。本地代码与自动化验收已完成，不存在进入后续功能开发的代码阻塞。

本阶段实际完成：

1. 新增 snapshot、英雄列表/详情、羁绊、装备、强化符文、特殊机制和开局奇遇共八个用户 Token 只读接口；
2. 新增严格响应 Schema、当前快照限定的分页/筛选/批量关系查询、固定 404/422/503 行为和带 Request ID 的安全查询日志；
3. 新增 26 个 API 测试，完成 51 个相关回归、100 个全量测试、真实仓库 raw 完整导入/API smoke，并同步 README 与三类方案文档。

本阶段明确未实现或未执行：

- 未实现 Service Token 资料接口、`/internal/v1` 资料路由、非英雄详情、HTTP 写入/同步/current 切换、raw 文件访问、向量检索或匿名开放；
- 未变更数据库 schema、依赖、配置或 `pdm.lock`；
- 未修改 main 的真实角色权限，未使用真实跨服务用户 Token，未执行生产网关、官方内容许可、生产迁移或部署验证；
- 未调用真实 CDN，也未写入或迁移长期开发数据库。

## 2. 实际代码与配置变更

### 2.1 current snapshot 只读查询

- [repository.py](../app/jcc_data/repository.py)：新增 snapshot 分页/详情结果、英雄关系、羁绊 tiers、装备 components 和五类列表查询；
- 查询先通过 `_required_current_snapshot()` 固定 snapshot，后续 count、主实体和关联都显式限定同一个 ID；
- 英雄关系筛选使用 `EXISTS`，避免多关系 join 造成重复 total；当前页关系批量查询，不逐条 N+1；
- 名称/标题使用 SQLAlchemy 自动转义的包含匹配，所有其他过滤字段固定，不接受任意排序或 SQL 表达式。

关键链路：

```text
读取 mode current snapshot 一次
  → 固定 snapshot.id
  → snapshot 限定的 count + 分页实体
  → snapshot 限定的批量关系查询
  → 与同一 snapshot metadata 共同返回
```

排序最终约定：主实体先按 `external_id` 长度、再按字符串值升序；这使当前纯数字官方 ID 符合数值直觉，同时对一般字符串 ID 保持确定性。英雄关系、tiers 和装备材料分别按 `position`、`tier_order` 和 recipe 槽位返回。

### 2.2 Schema 与 API

- [jcc.py Schema](../app/schemas/jcc.py)：新增严格 `extra="forbid"` 的 snapshot、分页、英雄、羁绊/tier、装备/component、augment、adventure、galaxy 模型；
- [jcc.py Router](../app/api/jcc.py)：新增 `/jcc/snapshot`、`/jcc/heroes`、`/jcc/heroes/{hero_id}`、`/jcc/traits`、`/jcc/equipment`、`/jcc/augments`、`/jcc/adventures`、`/jcc/galaxies`；
- [main.py](../app/main.py)：注册 JCC Router，不改变 health、profile 或 internal Router；
- 所有端点通过 Router 级 `Depends(require_scope("jcc:data:read"))` 复用用户鉴权；没有导入 Service Auth；
- 所有列表默认 `limit=50`、范围 `1..100`、`offset>=0`，ID 使用长度与安全字符限制，查询文本 trim 后拒绝纯空白；
- 英雄返回完整属性、技能、`traits` 和 `classes`；其他资源只提供完整列表 item；装备无 recipe 时 `components=[]`，重复材料按两个槽位保留两项；
- 对外 ID 均为字符串，攻速为 JSON number；内部主键、snapshot_id、raw 路径、manifest 和 source_attributes 不进入响应。

固定错误：

- current 不存在或数据库失败：`503 {"detail":"JCC_DATA_UNAVAILABLE"}`；
- 当前 snapshot 中英雄不存在：`404 {"detail":"JCC_HERO_NOT_FOUND"}`；
- 参数违反类型、范围、长度、枚举、非空或 ID 字符约束：`422`；
- 认证错误继续由现有用户链路返回 401/403。

### 2.3 日志和可观测性

- [logging.py](../app/core/logging.py)：扩展既有 request completion 日志，增加解析后的路由模板和可用时的 snapshot version/revision、limit、offset；
- handler 只向 `request.state` 写入可信、非敏感上下文；middleware 使用真实状态码和耗时输出一条完成日志；
- 现有 Request ID 生成/回传和敏感值脱敏保留；测试确认 Token、实体内容、SQL 和数据库参数不进入 JCC 错误/完成日志。

### 2.4 数据、迁移和状态

不涉及数据结构或持久状态变更：

- 没有新增 Alembic revision；当前长期开发数据库仍为 `0002_jcc_structured_data (head)`；
- API 只读 `jcc_current_snapshots` 所指的结构化表，不修改 current、snapshot 或业务实体；
- current 切换一致性测试只发生在一次性 SQLite 测试数据库中，测试结束全部清理。

### 2.5 配置、依赖和外部服务

- 没有新增配置项；API 复用 `JCC_DATA_MODE`、数据库和现有用户 JWT/Redis 配置；
- 没有新增依赖，`pdm.lock` 未修改且一致性检查通过；
- API 不请求官方 CDN、不读取 raw、不调用 main HTTP API；
- [README.md](../README.md) 已增加路由、筛选、分页、认证、错误和边界说明。

## 3. 关键设计结果

1. `list_current_*`/`get_current_hero()` 返回实体数据和所用 `JccSnapshot`；API 不会在响应组装时重新读取 current。
2. 即使 current 在分页主查询与关系查询之间切换，当前响应仍继续限定最初 snapshot；自动化测试模拟切换并验证 metadata、英雄和关系都来自旧 snapshot。
3. 英雄 `trait_id` 只匹配 race，`class_id` 只匹配 job；多个条件使用 AND，关系不会重复 total。
4. 只有英雄有详情路由；其他资源的完整字段位于列表 item，装备 components 位于装备 item。
5. 无 current 和数据库异常统一 fail closed 为固定 503，不回退读取 raw 或部分数据；错误日志只记录异常类型。
6. `/jcc/*` 统一使用现有用户 Bearer 安全方案，Service Token 因 issuer/audience/用户 claims 不匹配而不能冒充用户 Token。

## 4. 与阶段计划的差异

| 差异 | 计划内容 | 实际实施 | 原因 | 影响与处理 |
| --- | --- | --- | --- | --- |
| 主实体排序 | 按字符串 external_id 升序 | 按 ID 长度再按字符串值升序 | 当前官方 ID 是不等长数字字符串，单纯字典序不符合数字直觉 | 仍为稳定官方 ID 排序；已同步阶段计划 |
| 真实样本 smoke | 长期数据库已有 current 时可只读 smoke | raw 导入一次性 SQLite 后执行八个 API smoke | 不触碰长期数据库即可验证完整官方样本 | 增强本地证据；不替代真实 PostgreSQL/用户 Token/生产验证 |

其余实现与阶段计划一致，没有扩大到 Service API、raw API、写接口或 RAG。

## 5. 测试与验证结果

### 5.1 验证汇总

| 检查 | 命令或方法 | 结果 | 证据/说明 |
| --- | --- | --- | --- |
| JCC API 定向测试 | `pdm run pytest tests/test_jcc_api.py -q` | 通过 | 26 passed，2 条第三方 deprecation warnings |
| 相关回归 | `pdm run pytest tests/test_jcc_data_import.py tests/test_profile_api.py tests/test_internal_api.py tests/test_redis_state_services.py tests/test_logging.py -q` | 通过 | 51 passed，2 条第三方 warnings |
| 定向 Ruff | `pdm run ruff check app/api/jcc.py app/schemas/jcc.py app/jcc_data/repository.py app/main.py app/core/logging.py tests/test_jcc_api.py tests/test_logging.py` | 通过 | All checks passed；只检查新增/修改文件 |
| Python 编译 | `python -m compileall -q app tests` | 通过 | 无输出 |
| 全量测试 | `pdm run test -q` | 通过 | 100 passed，2 条第三方 warnings |
| Alembic 状态 | `pdm run alembic-current` | 通过 | `0002_jcc_structured_data (head)`；本阶段无迁移 |
| 锁文件 | `pdm lock --check` | 通过 | 无输出，`pdm.lock` 未修改 |
| Diff | `git diff --check` | 通过 | 无空白错误 |
| OpenAPI | API 测试读取 `/openapi.json` | 通过 | 仅八个预期 JCC GET；统一 HTTPBearer；无非英雄详情或 internal JCC 资料路由 |
| 真实仓库 raw API smoke | `raw/18.18.2-S19` → 一次性 SQLite import → 八个接口 | 通过 | snapshot `18.18.2`；snapshot、英雄列表/11500 详情及其余五类列表均 200；数据库随后删除 |

### 5.2 失败与未执行项

- 第一次真实 raw smoke 使用默认 SQLite 连接池，TestClient 线程拿到另一条内存连接，`/jcc/snapshot` 返回固定 503；这是 smoke harness 隔离方式错误，不是 API 缺陷。改用 `StaticPool` + `check_same_thread=False` + 外键开启后，完整 smoke 通过并清理。
- main 真实用户 Token/角色权限、网关、生产数据库和部署未执行；不属于普通本地验收，继续进入上线前清单。
- 测试仅有现有 FastAPI/Starlette 关于 TestClient/httpx/anyio 的两条第三方 deprecation warnings，无失败。

### 5.3 真实环境或人工验证

| 验证项 | 环境 | 副作用/授权 | 结果 |
| --- | --- | --- | --- |
| 完整官方 raw 的八接口 smoke | 本机一次性 SQLite 内存库 | 只读仓库 raw；内存写入后清理 | 通过 |
| 本地长期 PostgreSQL Alembic 状态 | 本机开发数据库 | 只读 revision 查询 | 通过，位于 head；未写入数据 |
| main 真实测试用户 Token | 跨服务 test 环境 | 需要 main 角色配置和受控账号 | 未执行 |
| 网关/官方内容许可/生产部署 | 生产或发布环境 | 需要审批、凭证和合规确认 | 未执行 |

## 6. 阶段验收结果

| 编号 | 验收标准 | 结果 | 验证证据 |
| --- | --- | --- | --- |
| AC-2-01 | 八个新接口均位于 `/jcc/*` 且只注册 GET | 通过 | `test_openapi_contains_only_intended_read_routes` |
| AC-2-02 | 所有新接口使用用户 Token、要求 `jcc:data:read` 且不接受 Service Token | 通过 | `test_all_routes_require_user_scope`、`test_service_token_is_not_accepted_as_user_token` |
| AC-2-03 | 英雄列表/详情完整，其他资源只有完整列表 item | 通过 | heroes、traits 和 simple resource API 测试及 OpenAPI 测试 |
| AC-2-04 | 装备 item 包含有序 components，无材料为空数组 | 通过 | `test_equipment_returns_ordered_components_and_empty_components` |
| AC-2-05 | 分页、固定筛选、稳定排序和参数 422 有效 | 通过 | heroes filter/pagination、资源筛选及参数化 422 测试 |
| AC-2-06 | 数据与 metadata 使用同一固定 snapshot | 通过 | current switch 与请求中途 current 切换测试 |
| AC-2-07 | 无 current、未知英雄、数据库异常符合固定错误且不泄露底层信息 | 通过 | no-current、unknown hero、OperationalError 测试 |
| AC-2-08 | profile、internal records、黑名单、Session、Request ID 无回归 | 通过 | 相关 51 passed 与全量 100 passed |
| AC-2-09 | README、总方案、阶段计划和执行记录同步 | 通过 | 本次文档文件和链接 |

## 7. 安全、兼容性与可观测性核对

### 安全

- Router 统一要求 `jcc:data:read`，缺 Token/错误 Token 返回 401，缺 Scope 返回 403；Service Token 不走用户认证；
- 路径和查询参数有类型、范围、枚举、长度、非空和安全 ID 字符限制；SQLAlchemy 使用绑定表达式，包含匹配启用通配符自动转义；
- 响应为显式白名单，不暴露内部 ID、raw、manifest、source_attributes、SQL 或连接信息；
- 数据库失败 fail closed 为固定 503；无 raw 或历史数据降级；
- 只注册 GET，不存在用户可触发的 current 切换、同步、写入或删除接口。

### 兼容性

- 不修改 schema、current 导入语义、用户 JWT claims、Redis 黑名单/Session、Service Token 或已有 API；
- `/api/profile`、`/internal/v1/records` 与全量测试通过；
- 应用镜像回滚不需要数据库 downgrade，第一阶段结构化表、raw 和 snapshot 保留。

### 可观测性

- 完成日志包含 Request ID、方法、真实路径、路由模板、状态码、耗时及可用时的 snapshot version/revision、limit、offset；
- JCC 数据库异常日志仅包含错误类型；
- 日志测试确认不包含 Authorization 或响应实体内容，现有全局脱敏测试继续通过。

## 8. 遗留问题与后续阶段入口

### 8.1 当前阶段遗留问题

| 问题 | 影响 | 负责人/条件 | 处理阶段 |
| --- | --- | --- | --- |
| main 尚未发布 `jcc:data:read` 到真实测试角色 | 无法完成真实跨服务用户 Token smoke | main 管理员、受控测试账号 | 上线前联调 |
| 网关暴露范围和官方内容展示许可未验证 | 不代表可匿名或生产公开 | 网关/安全/合规审批 | 上线前 |
| 生产数据库/API 部署未执行 | 本记录不代表生产已上线 | 备份、迁移/发布审批、凭证 | 发布阶段 |

### 8.2 下一阶段可复用能力

- `/jcc/*` 提供稳定、带版本元数据的结构化资料查询，可供受信任登录客户端使用；
- repository 的固定 snapshot 查询和关系聚合可供后续 RAG 文档生成复用，但后续不得通过 API 暴露 raw/source_attributes；
- 后续全文/向量或 Agent 功能必须单独制定方案，不得把本阶段用户资料 API 改为 Service Token 或匿名开放。

## 9. 文档同步记录

- [总实施方案](JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PLAN.md)：总状态和第二阶段更新为已完成，补充计划/执行记录链接与验收结论；
- [第二阶段实现计划](JCC_LOL_STRUCTURED_DATA_API_IMPLEMENTATION_PHASE_2_PLAN.md)：状态更新为已完成，AC 更新为已满足，记录排序和真实 raw smoke 调整；
- 本执行记录：记录实际代码、测试数字、第一次 smoke harness 失败、最终结果、未执行外部验证和阶段结论；
- [README.md](../README.md)：新增 JCC API 使用和安全边界。

## 10. 阶段结论

第二阶段已完成：

- 用户 Token + `jcc:data:read` 保护的八个只读接口、固定 snapshot、关系聚合、筛选分页、白名单和错误契约已经落地；
- 定向 26 passed、相关回归 51 passed、全量 100 passed，Ruff、编译、Alembic head、锁文件、diff、OpenAPI 和完整官方 raw API smoke 通过；
- 没有新增迁移、依赖、Service Token 资料链路、raw 接口或写能力，既有认证和 API 无回归；
- main 真实角色权限、跨服务 Token、网关/合规和生产发布仍需上线前受控验证，但不影响本阶段本地代码与自动化验收结论。
