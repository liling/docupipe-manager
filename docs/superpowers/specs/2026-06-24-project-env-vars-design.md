# 项目环境变量功能设计

> 日期：2026-06-24
> 状态：已确认，待写实施计划

## 背景与动机

DocuPipe Manager 通过子进程执行任务（`python -m docupipe run --config config.yaml`）。任务的 `config.yaml` 在运行时由 docupipe 解析，但当前子进程的环境仅注入了 `HOME`（用于凭证隔离），任务配置无法引用任何项目级的动态配置（如租户 ID、API endpoint、业务开关等）。

现有 `runner_service.py` 两处子进程的 env 构造为：

```python
env={**os.environ, "HOME": home_dir}
```

需要为项目增加一组「环境变量」，在任务执行前合并进子进程 env，使任务的配置文件能通过 `${VAR}` 或 `os.environ` 引用，从而让同一份 config.yaml 在不同项目/环境下产出不同行为。

## 目标

1. **数据模型**：新增项目级 `project_env_vars` 表，承载一个项目的环境变量集合。
2. **加密**：每个变量可选标记为 secret，secret 变量的值用现有 SM4 加密入库；非 secret 明文存储。
3. **注入**：`RunnerService` 在任务执行时加载所属项目的全部环境变量，解密 secret 值后合并进子进程 env。
4. **API**：项目内环境变量的逐条 CRUD（与凭证/任务路由风格一致）。
5. **UI**：项目详情页新增「环境变量」Tab，端到端管理（列表/新增/编辑/删除）。

## 非目标

- 不做任务级环境变量（仅项目级，与凭证归属一致）。
- 不做 `.env` 文件导出/导入。
- 不做变量值变更历史/审计日志。
- 不做跨项目变量共享/继承。
- 不做 `is_secret` 类型切换（创建时确定，需改类型则删除重建）。

## 关键决策摘要

| 维度 | 决策 |
|---|---|
| 归属层级 | 项目级（项目内所有任务共享，与凭证归属一致） |
| 加密策略 | per-variable 可选（`is_secret` 布尔标记；secret 走 SM4，复用 `crypto.py`） |
| 注入方式 | 合并进子进程 env（`{**os.environ, **project_env, "HOME": home_dir}`） |
| 优先级 | 项目变量覆盖系统变量；`HOME` 始终为隔离用的 `home_dir`（写在合并之后） |
| 编辑交互 | 逐条 CRUD（方案 A），与凭证/任务 Tab 风格一致 |
| key 命名 | 宽松 `^[A-Za-z_][A-Za-z0-9_]*$`（允许大小写） |
| 唯一性 | `UNIQUE(project_id, key)` |
| secret 脱敏 | 列表 API 对 `is_secret=true` 的 `value` 返回 `null` |
| secret 编辑 | 编辑时 value 留空表示不修改；`is_secret` 不可改 |
| 删除语义 | 物理删除（环境变量无需历史追溯，与 `project_members` 一致） |
| 权限守卫 | `_require_access_async`（admin/Owner/Member 均可管理，同凭证） |
| 范围 | 端到端（模型 + 迁移 + service + API + UI） |

## 数据模型

所有表位于 `docupipe_manager` schema，时间戳带时区。

### `project_env_vars`（项目环境变量）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | `default gen_random_uuid()` |
| `project_id` | UUID FK→projects.id | `ON DELETE CASCADE` |
| `key` | String(255) | 环境变量名，应用层校验 `^[A-Za-z_][A-Za-z0-9_]*$` |
| `value` | Text | `is_secret=true` 存 SM4 加密 hex；否则明文 |
| `is_secret` | Boolean | 默认 false |
| `description` | String(255), nullable | 可选说明 |
| `created_by` | UUID | |
| `created_at`, `updated_at` | TIMESTAMPTZ | server_default now / onupdate now |

约束：`UNIQUE(project_id, key)`；索引 `(project_id)`。

ORM：新增 `docupipe_manager/models/project_env_var.py`（`ProjectEnvVar`），注册到 `docupipe_manager/models/__init__.py`。

## 注入逻辑

### `RunnerService._do_execute` 改动

在加载 task 后、构建子进程 env 前，加载**所属 project 的全部环境变量**，解密 secret 值，构建 `project_env: dict[str, str]`：

- 查询 `project_env_vars WHERE project_id = task.project_id`（全量，无 status 过滤）。
- 对 `is_secret=true` 的行，用 `settings.encryption_key` 调 `decrypt_sm4(value, key_hex)` 解密；失败则让本次 run 失败，`error_message` 写明「环境变量 {key} 解密失败」。
- 非_secret 行直接取明文。
- 组装为 `{key: value}` 字典。

### 子进程 env 合并

两处子进程（`runner_service.py` 当前的 `env={**os.environ, "HOME": home_dir}`）统一改为：

```python
env={**os.environ, **project_env, "HOME": home_dir}
```

- `**project_env` 在 `**os.environ` 之后 → 项目变量覆盖系统变量（用户显式配置优先）。
- `"HOME": home_dir` 在最后 → 保证凭证隔离用的临时 HOME 不被项目变量误改。

注入点覆盖两处子进程：
1. dws `auth import` 子进程（凭证导入过程也能引用项目变量）。
2. docupipe `run` 子进程（任务执行本体）。

### 无环境变量时的行为

项目无环境变量时，`project_env` 为空 dict，合并后等价于原行为，向后兼容。

## API 路由

新增 `docupipe_manager/api/env_vars.py`，`APIRouter(prefix="/api/projects/{project_id}/env-vars", tags=["env-vars"])`，在 `main.py` 注册（`app.include_router(env_vars_router)`）。

权限：全部使用 `_require_access_async`（与凭证一致，admin/Owner/Member 均可管理）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `` | 列表；`is_secret=true` 的 `value` 返回 `null` |
| POST | `` | 新增（校验 key 命名 + 项目内唯一） |
| PUT | `/{var_id}` | 编辑（secret 变量 value 畺空表示不修改） |
| DELETE | `/{var_id}` | 物理删除 |

### 请求/响应模型

```python
class CreateEnvVarRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    value: str = Field(..., min_length=0)  # 允许空字符串值
    is_secret: bool = False
    description: Optional[str] = Field(None, max_length=255)

class UpdateEnvVarRequest(BaseModel):
    key: Optional[str] = Field(None, min_length=1, max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    value: Optional[str] = None
    description: Optional[str] = Field(None, max_length=255)
    # 注意：无 is_secret 字段，类型不可改
```

### Secret 语义（关键）

- **列表 API**：`is_secret=true` 的变量，响应里 `value` 字段为 `null`（绝不外泄明文）。
- **编辑 secret 变量**：
  - 请求未传 `value` 或 `value` 为空字符串 → 后端保持原加密值不变。
  - 请求传了非空 `value` → 加密后覆盖。
- **编辑非 secret 变量**：`value` 正常更新（传空则更新为空字符串）。
- **`is_secret` 不可改**：`UpdateEnvVarRequest` 不含 `is_secret` 字段；要改类型须删除重建。

### key 唯一冲突

新增/编辑时若违反 `UNIQUE(project_id, key)`，返回 `409 Conflict`（`detail: "变量名已存在"`）。

## UI 结构

### 项目详情页 Tab 调整（`project_detail.html`）

在现有 Tab 栏（任务/凭证/成员/运行历史）后新增：

```html
<button class="tab" data-tab="env-vars">环境变量</button>
```

并新增对应 panel：

```html
<div id="tab-env-vars" class="tab-panel hidden"></div>
```

Tab 切换与 URL hash 持久化逻辑无需改动（现有 `activateTab` 已通用）。

### 交互（`project_detail.js`）

新增 `loadEnvVars()` 函数，并加入文件末尾的调用列表：

```javascript
loadProject(); loadTasks(); loadCredentials(); loadMembers(); loadRuns(); loadEnvVars();
```

`loadEnvVars()` 行为：

1. `GET /api/projects/{pid}/env-vars` 取列表。
2. 渲染表格，列：`变量名` | `值`（secret 显示 `••••••` + 锁标识）| `类型`（普通/密钥）| `说明` | `操作`（编辑/删除）。
3. 表格上方「新增变量」按钮 → 显示行内编辑器卡片（清空表单，`is_secret` 复选框可用）。
4. 行内「编辑」按钮 → 显示编辑器卡片（预填；secret 变量的 value 留空 + placeholder「留空表示不修改」；`is_secret` 复选框禁用）。
5. 编辑器卡片字段：key / value / is_secret（复选框） / description / 保存 / 取消。
6. 保存：新增 `POST`、编辑 `PUT /{var_id}`，成功后隐藏卡片 + 刷新列表；失败 alert 返回的 `detail`。
7. 删除：`confirm` 后 `DELETE /{var_id}`，刷新列表。

编辑器卡片的显隐模式复用现有 credentials device-flow 卡片的 toggle 风格（`classList` 加/去 `hidden`）。

## 迁移策略

新增 `docupipe_manager/migrations/versions/0003_add_project_env_vars.py`：

- `revision = "0003"`，`down_revision = "0002"`。
- 沿用现有手写 raw SQL + `CREATE TABLE IF NOT EXISTS` 幂等风格（不依赖 autogenerate）。
- `upgrade`：建表 + 建索引。
- `downgrade`：`DROP TABLE IF EXISTS docupipe_manager.project_env_vars CASCADE`。

不涉及存量数据迁移（纯新增表）。

## 测试策略

沿用现有目录（`tests/api`、`tests/services`、`tests/unit`）与 `conftest.py` 的 fixture（`async_client` + `dependency_overrides` + `mock_session`）。

| 层 | 文件 | 覆盖点 |
|---|---|---|
| 单元 | `tests/unit/test_models.py`（追加） | `ProjectEnvVar` 映射正确；默认值 |
| API | `tests/api/test_env_vars.py`（新增） | CRUD 各路由 200；access 守卫；`is_secret` 列表返回 `value=null`；secret 编辑 value 留空保持原值、传新值覆盖；非 secret 正常更新；key 命名非法 → 422；项目内 key 重复 → 409 |
| 服务 | `tests/services/test_runner_service.py`（追加） | runner 把 project env 注入子进程 env；项目变量覆盖 `os.environ`；`HOME` 始终为 `home_dir`；secret 变量正确解密；无 env 变量的项目行为不变（空 dict）；解密失败导致 run 失败 |

## 风险与权衡

- **secret 解密失败**（如 `encryption_key` 后续变更）→ run 在 `_do_execute` 启动期失败，`error_message` 明确写「环境变量 {key} 解密失败」。可接受，与凭证解密失败的处理一致。
- **项目变量覆盖关键系统变量**（如 `PATH`）→ 已知行为，优先级按用户显式配置。`HOME` 已特殊保护；其余不做白名单拦截，UI 上以文案提示用户。
- **物理删除无历史**→ 环境变量是配置而非业务数据，无需审计追溯；若未来需要可加 `status` 软删除，当前 YAGNI。
- **`is_secret` 不可改**→ 删除重建略有不便，但避免了 secret↔明文互转时旧值不可回填、格式不一致的复杂度。
