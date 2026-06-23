# 项目与任务重构设计

> 日期：2026-06-23
> 状态：已确认，待写实施计划

## 背景与动机

当前系统对"项目"的理解是扁平的单层结构：`DocupipeProject` 同时是**组织单元**和**执行单元**——一个项目绑定一个 `config_yaml`、一个调度、一个 DWS 凭证。所有 API 由全局 `require_admin` 守卫，普通登录用户完全无法访问。

实际业务里，一个项目是**一组任务的容器**，应当：

- 支持多个任务（每个任务对应一个 docupipe yaml 文件，可独立调度）。
- 支持团队协作：项目有 Owner 和 Member，都能编辑项目内容；只有 Owner 能邀请 Member、删除项目。
- 支持多源头凭证：项目持有一个凭证池，任务按源头类型绑定对应凭证（DWS / 腾讯文档 / 未来更多）。

因此需要把当前的扁平模型重构为"项目（容器）→ 任务（执行单元）"两层结构，并引入项目级角色与协作能力。

## 目标

1. **数据模型**：项目变成纯容器；执行字段下沉到任务表；凭证改为项目级私有池（分类型多表）。
2. **权限模型**：引入项目级 Owner / Member 角色；平台 admin 保留系统级超级管理员身份。
3. **后端 API**：项目 / 成员 / 凭证 / 任务 / 运行 全套路由，按角色守卫。
4. **前端 UI**：项目列表 + 项目详情（任务、凭证、成员、运行历史 Tab），普通用户可见自己参与的项目。
5. **迁移策略**：全新开始，drop 旧表重建，不保留现有数据。

## 非目标

- 本次**不实现**腾讯文档凭证（`tencent_doc_credentials` 表）。架构（多态引用字段、枚举）预留扩展位，但只交付 DWS 凭证。
- 不做凭证导入/导出、不做跨项目凭证共享、不做运行结果的内容级 diff。

## 关键决策摘要

| 维度 | 决策 |
|---|---|
| 模型层次 | 项目（容器）→ 任务（执行单元），两层 |
| 项目角色 | Owner（= 创建者 admin）+ Members；都能编辑内容；仅 Owner 能邀请/删除 |
| 谁能创建项目 | 仅平台 admin，创建后成为 Owner |
| admin 边界 | 系统级超级管理员，看/操作所有项目 |
| Member 加入方式 | Owner 搜索平台用户，直接加入，立即生效（无邀请确认流程） |
| 凭证归属 | 项目级私有池，按类型分表 |
| 任务↔凭证 | 任务多态引用（`credential_id` + `credential_type`），运行时和配置一起注入 |
| 凭证表设计 | 分类型多表（方案 A）：保留 `dws_credentials`，未来按需加表 |
| 数据迁移 | 全新开始，drop 旧表重建 |
| 范围 | 端到端（模型 + API + UI） |

## 数据模型

所有表位于 `docupipe_manager` schema。时间戳带时区。

### `projects`（项目容器）

纯容器，不承载执行字段。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | |
| `name` | String(255), unique | 项目名 |
| `slug` | String(64), unique | URL 标识，`^[a-z0-9-]+$` |
| `description` | Text, nullable | |
| `owner_id` | UUID | 创建者（平台 admin） |
| `status` | enum `project_status`(active/paused/archived) | 默认 active |
| `created_at` | DateTime(tz) | server_default now |
| `updated_at` | DateTime(tz) | server_default now, onupdate now |

### `project_members`（项目成员）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | |
| `project_id` | UUID FK→projects.id | |
| `user_id` | UUID | 平台用户 ID |
| `added_by` | UUID | 邀请者（Owner） |
| `created_at` | DateTime(tz) | |
| | UNIQUE(project_id, user_id) | 同一用户在同一项目只一条记录 |

> Owner 不在 `project_members` 表里登记，通过 `projects.owner_id` 隐含。权限判定时"Owner"= `projects.owner_id == user_id`。

### `dws_credentials`（DWS 凭证 — 迁到项目级）

保留现有字段，新增 `project_id`，唯一约束调整。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | |
| `project_id` | UUID FK→projects.id | **新增**：项目私有 |
| `name` | String(255) | **唯一约束改为** UNIQUE(project_id, name) |
| `corp_id` | String(64) | |
| `auth_blob` | BYTEA | SM4 加密的认证数据 |
| `token_expires_at` | DateTime(tz), nullable | |
| `refresh_token_expires_at` | DateTime(tz), nullable | |
| `last_refreshed_at` | DateTime(tz), nullable | |
| `status` | enum `credential_status`(active/expired/revoked) | |
| `created_by` | UUID | |
| `created_at`, `updated_at` | DateTime(tz) | |

### `tasks`（任务 — 新表）

承接原 `docupipe_projects` 的执行字段，归属到项目。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | |
| `project_id` | UUID FK→projects.id | |
| `name` | String(255) | |
| `slug` | String(64) | UNIQUE(project_id, slug)，`^[a-z0-9-]+$` |
| `description` | Text, nullable | |
| `config_yaml` | Text | docupipe yaml，应用层校验含 `pipelines` 列表 |
| `credential_id` | UUID, nullable | 多态引用凭证（无 DB 外键） |
| `credential_type` | enum `credential_type`(dws), nullable | 本次仅 `dws`，预留扩展 |
| `schedule_cron` | String(64), nullable | 应用层 croniter 校验 |
| `schedule_enabled` | Boolean | 默认 true |
| `schedule_pipeline` | String(255), nullable | yaml 中指定运行的 pipeline |
| `schedule_mode` | String(16) | full/incremental/mirror，默认 incremental |
| `status` | enum `task_status`(active/paused/archived) | 默认 active |
| `created_by` | UUID | |
| `created_at`, `updated_at` | DateTime(tz) | |

### `pipeline_runs`（运行记录）

字段不变，仅把 `project_id` 改为 `task_id`（FK→tasks.id）。其余字段（trigger_type、triggered_by、pipeline_name、mode、status、pid、exit_code、started_at、completed_at、log_path、error_message、created_at）保持。

## 权限模型

### 三个角色

- **平台 admin**（`user.role == "admin"`）：系统级超级管理员。能创建项目、能看/操作所有项目（无论是否 Owner/Member）。
- **项目 Owner**（`projects.owner_id == user_id`）：能编辑项目内容、管理任务/凭证、邀请 Member、删除项目。
- **项目 Member**（`project_members` 存在该用户）：能编辑项目内容、管理任务/凭证、手动触发运行。**不能**邀请 Member、删除项目。

### 依赖函数（FastAPI Depends）

```python
require_admin                     # 系统级：仅 admin
require_project_access(pid)       # admin OR Owner OR Member
require_project_owner(pid)        # admin OR Owner
```

- `require_project_access` / `require_project_owner` 在 admin 时直接通过；否则查 `projects.owner_id`（Owner 判定）和 `project_members`（Member 判定）。
- admin 在所有项目级守卫里都被视为通过（运维权）。

## API 路由

### admin 专属

| 方法 | 路径 | 说明 | 守卫 |
|---|---|---|---|
| POST | `/admin/api/projects` | 创建项目，创建者成为 Owner | admin |

### 项目

| 方法 | 路径 | 说明 | 守卫 |
|---|---|---|---|
| GET | `/api/projects` | 列表：admin 看全部，普通用户看 Member 的 | login |
| GET | `/api/projects/{id}` | 详情 | access |
| PUT | `/api/projects/{id}` | 编辑 | access |
| DELETE | `/api/projects/{id}` | 归档 + 取消所有任务调度 | owner |

### 成员

| 方法 | 路径 | 说明 | 守卫 |
|---|---|---|---|
| GET | `/api/projects/{id}/members` | 列表 | access |
| POST | `/api/projects/{id}/members` | 添加（搜索平台用户后直接加入） | owner |
| DELETE | `/api/projects/{id}/members/{user_id}` | 移除 | owner |

### 凭证（项目内 DWS）

device flow 沿用现有 `CredentialService` 的 start_device_login / poll_device_login / finalize_login，只把存储改成带 `project_id`。

| 方法 | 路径 | 说明 | 守卫 |
|---|---|---|---|
| GET | `/api/projects/{id}/credentials` | 列表 | access |
| POST | `/api/projects/{id}/credentials/device-login/start` | 启动 device flow | access |
| GET | `/api/projects/{id}/credentials/device-login/poll` | 轮询登录状态 | access |
| POST | `/api/projects/{id}/credentials/device-login/finalize` | 完成登录并存储凭证 | access |
| GET | `/api/projects/{id}/credentials/{cid}/status` | 查询凭证 auth 状态 | access |
| DELETE | `/api/projects/{id}/credentials/{cid}` | 吊销（软删除，status=revoked） | access |

### 任务

| 方法 | 路径 | 说明 | 守卫 |
|---|---|---|---|
| GET | `/api/projects/{id}/tasks` | 列表（含最近一次 run 状态） | access |
| POST | `/api/projects/{id}/tasks` | 创建 | access |
| GET | `/api/projects/{id}/tasks/{tid}` | 详情 | access |
| PUT | `/api/projects/{id}/tasks/{tid}` | 编辑（改 schedule 时重置调度） | access |
| DELETE | `/api/projects/{id}/tasks/{tid}` | 归档 + 取消调度 | access |
| POST | `/api/projects/{id}/tasks/{tid}/trigger` | 手动触发运行 | access |

### 运行

| 方法 | 路径 | 说明 | 守卫 |
|---|---|---|---|
| GET | `/api/runs` | 列表（按可见项目过滤；admin 全部；否则 Member/Owner 项目内任务的 run） | login |
| GET | `/api/runs/{id}` | 详情 | 通过所属 task 的项目权限校验 |
| POST | `/api/runs/{id}/cancel` | 取消（pending 置 cancelled；running 发 SIGTERM） | 同上 |

## 服务层改动

### `RunnerService`
- `start_run` 入参从 `project_id` 改为 `task_id`。
- `_do_execute`：通过 task 取 `config_yaml` + `credential_id`/`credential_type`；按 `credential_type` 分派凭证解密与注入逻辑（本次只实现 dws 分支，复用现有 SM4 解密）。
- `PipelineRun` 关联 `task_id`。

### `SchedulerService`
- 调度键从 `project-{id}` 改为 `task-{id}`。
- `schedule_task(task_id)` / `unschedule_task(task_id)` / `_reload_all` 扫描 `tasks` 表（active + schedule_enabled + schedule_cron）。
- `_scheduled_run` 触发时按 task 启动 run。

### `CredentialService`
- 所有方法增加 `project_id` 参数，凭证带 `project_id` 存储。
- `list_credentials(project_id)` 只返回项目内的。
- device flow 逻辑不变。

## UI 结构（Jinja2 模板）

### 导航菜单调整（`main.py` 的 `DOCUPIPE_NAV_MENU`）

```
所有登录用户可见：
  - 项目：/docupipe/projects  （列表按身份过滤）
  - 运行：/docupipe/runs        （列表按可见项目过滤）
```

不再对"管理"分组要求 admin。普通用户能看到自己 Member 的项目。

### 页面

1. **项目列表** `projects.html`
   - admin：全部项目 + "创建项目"按钮。
   - 普通用户：只显示自己 Member 的项目（空态提示无项目）。

2. **项目详情** `project_detail.html`（Tab 式）
   - **任务 Tab**（默认）：任务列表，每行显示调度、最近 run 状态、"触发"按钮；"新建任务"入口。
   - **凭证 Tab**：DWS 凭证列表 + "添加凭证（device flow）"入口。
   - **成员 Tab**：成员列表（标注 Owner，成员显示删除按钮仅对 Owner 可见）；"添加成员"（搜索平台用户）入口仅 Owner 可见。
   - **运行历史 Tab**：跨任务的运行记录列表，可取消运行。

3. **任务表单** `task_form.html`：yaml 编辑+校验、选凭证（项目内 DWS）、cron 配置、mode 选择。

4. **运行列表/详情** `runs.html`：保留，改为按可见项目过滤，关联到任务名。

## 迁移策略

- 写全新 alembic 迁移：drop `docupipe_projects`、`dws_credentials`、`pipeline_runs`，创建 `projects`、`project_members`、`dws_credentials`(新版)、`tasks`、`pipeline_runs`(新版) 及对应枚举。
- 现有数据丢弃（项目处于早期 init 阶段，无生产数据）。
- 迁移版本表保持在 `docupipe_manager` schema。

## 测试策略

- **单元/服务层**：权限依赖（admin/Owner/Member 各场景）、Runner 按 task 执行、Scheduler 按 task 调度、Credential 按 project 隔离。
- **API 层**：每个路由的角色守卫（403/404/200），任务 CRUD + 触发，成员增删，凭证 device flow mock。
- 现有测试目录结构（`tests/api`、`tests/services`、`tests/unit`）沿用。

## 删除语义（统一）

所有"删除"操作均为**软删除**（置 `status=archived` 或 `revoked`），不物理删行，以保留运行历史可追溯：

- **删除项目**（DELETE `/api/projects/{id}`，owner）：`projects.status=archived`；取消项目内所有任务的调度。
- **删除任务**（DELETE `/api/projects/{id}/tasks/{tid}`，access）：`tasks.status=archived`；取消该任务调度。
- **吊销凭证**（DELETE `/api/projects/{id}/credentials/{cid}`，access）：`dws_credentials.status=revoked`。
- **移除成员**（DELETE `/api/projects/{id}/members/{user_id}`，owner）：物理删除 `project_members` 行（成员关系无需历史）。

归档后的项目/任务在列表里默认不显示（admin 可通过 query 参数查看归档项）。

## 风险与权衡

- **多态凭证引用无 DB 外键**：任务删除时凭证仍存在（符合预期，凭证属项目）；凭证被吊销时，引用它的任务运行会在 runner 里失败（需明确报错"凭证不可用"）。可接受。
- **Owner 不在 members 表**：减少冗余，但查询"项目所有人"需 union `owner_id` 与 `project_members`。权限依赖里已处理。
- **本次不实现腾讯文档**：架构预留，后续加表 + 枚举值 + runner 分派分支即可，无需改 task 结构。
