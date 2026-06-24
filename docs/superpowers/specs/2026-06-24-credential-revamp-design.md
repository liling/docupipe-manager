# 凭证功能改造设计

> 日期：2026-06-24
> 状态：已确认，待写实施计划

## 背景与动机

DocuPipe Manager 的凭证（`dws_credentials`）当前只支持**设备码登录**一种创建方式：`dws auth login --device` → `dws auth export --base64` → SM4 加密入库。存在三个问题：

1. **创建方式单一**：无法把「在别处已经 `dws auth export` 出来的凭证文件」直接导入，只能重新走设备码登录。
2. **有效期不可见**：数据模型已有 `token_expires_at` / `refresh_token_expires_at` 列，但 `finalize_login`（`credential_service.py:126`）创建时把两者**写死为 `None`**，且全程无更新——列表里的过期时间永远为空。
3. **可用性无法测试**：已有 `check_status` 能 import + status 验证凭证，但前端没有暴露入口，用户无从知道凭证是否还有效。

本次改造目标：支持「导入」与「设备码」两种创建方式；界面上增加凭证类型选择、有效期展示、可用性测试。

## 目标

1. **两种创建方式**：
   - 方式 A（导入）：粘贴 / 上传 `dws auth export --base64` 输出的文件内容。
   - 方式 B（设备码）：现有设备码流程（顺带修复过期时间持久化）。
2. **凭证类型选择**：添加凭证时选择凭证所属平台类型（目前仅 DWS/钉钉，预留扩展）。
3. **有效期展示**：列表展示 Access / Refresh Token 过期时间。
4. **可用性测试**：列表「测试」按钮，测试并回写最新状态/过期时间。

## 非目标

- 不做 token 自动刷新流程（`last_refreshed_at` 列本次保持 NULL，无写入方）。
- 不新增平台类型（仅预留 `credential_type` 字段，当前只有 `dws`）。
- 不做凭证编辑（名称/blob 修改），不支持改类型——需改则删除重建。
- 不做凭证历史/审计（沿用现有 `push_audit` 事件即可）。

## 关键决策摘要

| 维度 | 决策 |
|---|---|
| 凭证类型含义 | 凭证所属平台/系统（DWS/钉钉），区别于「创建方式」 |
| 凭证类型持久化 | 新增 `credential_type` 列（枚举，默认 `dws`） |
| 枚举类型 | `credential_type` 已在初始迁移 0001 创建（仅 `dws`），无需重建 |
| 创建方式 | 导入 + 设备码，两者最终存储格式一致（SM4 加密的 base64 blob） |
| 导入输入形态 | 粘贴文本 + 文件上传（前端 `FileReader` 读成文本，统一 JSON 提交） |
| 导入元数据来源 | 复用 `import + status` 提取 `corp_id` / 过期时间，并天然校验有效性 |
| 可用性测试 | 测试并回写 DB（最新 corp_id / 过期时间 / status） |
| status 判定 | refresh 未过期 → `active`；refresh 过期 → `expired`；import 失败 → `expired` |
| 测试失败语义 | 业务结果非 HTTP 错误，返回 `200` + `error` 字段 |
| 旧 `GET /{id}/status` | 移除（当前无人调用），由 `POST /{id}/test` 替代 |
| 有效期展示 | 拆 Access / Refresh 两列，绝对值 + 相对值，临期/过期配色 |
| UI 交互 | 统一添加对话框（仿 `env-dialog`），两种方式均在框内完成 |
| 权限守卫 | `_require_access_async`（与现有凭证一致） |
| 范围 | 端到端（模型 + 迁移 + service + API + UI + 测试） |

## 数据模型

所有表位于 `docupipe_manager` schema。

### `DwsCredential` 变更（`models/dws_credential.py`）

新增一列，**复用** `docupipe_manager.models.task.CredentialType` 枚举（已在 `task.py:20` 定义，值 `dws`，DB 枚举类型 `credential_type` 已在 0001 创建并被 `tasks.credential_type` 引用）。项目惯例即从 task 导入该枚举（见 `tests/unit/test_models.py:2`）。

```python
from docupipe_manager.models.task import CredentialType

credential_type: Mapped[CredentialType] = mapped_column(
    Enum(CredentialType, name="credential_type", schema=_SCHEMA, create_constraint=True),
    default=CredentialType.dws, nullable=False,
)
```

不新建枚举类（避免与 `task.CredentialType` 重名冲突、且同一 DB 枚举类型应共用一个 Python 枚举）。`task.py` 不导入 `dws_credential`，故无循环导入。

`token_expires_at` / `refresh_token_expires_at` / `last_refreshed_at` 列早已存在，本次只是让它们被正确写入（`last_refreshed_at` 除外，见非目标）。

### 迁移 `0004_add_credential_type.py`

- `revision = "0004"`，`down_revision = "0003"`。
- 枚举类型 `docupipe_manager.credential_type` 已在 0001 创建（仅 `dws`），**无需重建**。
- `upgrade`：`ALTER TABLE docupipe_manager.dws_credentials ADD COLUMN credential_type docupipe_manager.credential_type NOT NULL DEFAULT 'dws'`。
- `downgrade`：`ALTER TABLE ... DROP COLUMN credential_type`。
- 沿用现有手写 raw SQL 幂等风格（不依赖 autogenerate）。存量行由 `DEFAULT 'dws'` 自动回填。

## 后端服务变更（`services/credential_service.py`）

### 提取私有辅助 `_probe_auth_blob`

现有 `check_status` 已实现「写 base64 → import → status」。提取为可复用方法：

```python
async def _probe_auth_blob(self, auth_b64: str) -> dict:
    """把 base64 auth 写入临时 HOME，import 后调 status，返回元数据。
    返回 {corp_id, token_expires_at(str|None), refresh_token_expires_at(str|None), ...}
    失败抛 ValueError；finally 清理临时目录；子进程设合理超时。"""
```

新增时间解析小工具 `_parse_dt(s) -> datetime | None`：把 status 返回的 ISO 字符串解析为 timezone-aware datetime；解析失败容错为 `None`。

### 新增 `create_from_import`（方式 A）

```python
async def create_from_import(self, project_id, name, auth_b64, user_id) -> DwsCredential:
    # 1. 基础校验：auth_b64 非空、能 base64 解码
    # 2. meta = await self._probe_auth_blob(auth_b64)   # import+status 验证 + 提取元数据
    # 3. auth_blob_hex = encrypt_sm4(auth_b64, key_hex)
    # 4. 存 DwsCredential(credential_type=dws, corp_id=meta.corp_id,
    #                     token_expires_at=_parse_dt(meta.token_expires_at),
    #                     refresh_token_expires_at=_parse_dt(meta.refresh_token_expires_at), ...)
    # 5. push_audit("docupipe.credential.create", source="import")
```

- import 失败时 `_probe_auth_blob` 抛错 → 不入库。
- `corp_id` 沿用兜底：拿不到则空字符串（列 NOT NULL）。

### 改造 `check_status` → 测试并回写

```python
async def check_status(self, credential_id, project_id) -> dict:
    # 1. 取 credential，解密 auth_blob
    # 2. meta = await self._probe_auth_blob(auth_b64)
    # 3. 回写 corp_id / token_expires_at / refresh_token_expires_at
    # 4. 判定并回写 status（见下）
    # 5. 返回 {status, corp_id, token_expires_at, refresh_token_expires_at}
```

**status 判定规则：**
- `_probe_auth_blob` 成功：`refresh_token_expires_at < now` → `expired`；否则 `active`（access token 即便过期，refresh 未过期视为可用）。
- `_probe_auth_blob` 抛错（import 失败 / 凭证损坏）：回写 `status = expired`，返回 `{..., error: "原因"}`（**不抛异常**，让 `/test` 端点统一返回 200）。

> 契约：`check_status` 仅在「credential 不存在/越权」时抛 `ValueError`；其余情况一律返回 dict（`error` 可为 `None`）。这样 `/test` 端点能干净地区分 404 vs 200。`_probe_auth_blob` 本身仍抛 `ValueError`（import 失败），由 `check_status` 内部 `try/except` 捕获。

### 修复 `finalize_login` 过期时间 bug

现有代码（`credential_service.py:104-105`）已从 status 读到 `token_expires_at_str` / `refresh_expires_at_str`，但创建时写死 `None`。改为用 `_parse_dt` 解析后存入，并补 `credential_type=CredentialType.dws`。

## API 路由（`api/credentials.py`）

统一导入为 JSON（前端 `FileReader` 把上传文件读成文本，与粘贴殊途同归），后端只需一个 JSON 端点。

| 方法 | 路径 | 变更 |
|---|---|---|
| GET | `` | **增强**：响应补 `credential_type`、`refresh_token_expires_at` |
| POST | `/import` | **新增**：body `{name, auth_blob}` → `create_from_import`；无效 → `400` |
| POST | `/device-login/start` | 不变 |
| GET | `/device-login/poll` | 不变 |
| POST | `/device-login/finalize` | 不变（内部已修复回写过期时间） |
| POST | `/{credential_id}/test` | **新增**：测试并回写，**替代**旧 `GET /{id}/status` |
| DELETE | `/{credential_id}` | 不变 |

### 请求/响应模型

```python
class ImportRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    auth_blob: str = Field(..., min_length=1)
```

### `/test` 响应语义（关键）

测试失败属正常业务结果（凭证坏了），不当 HTTP 错误，统一返回 `200`：

```json
{
  "status": "active" | "expired",
  "corp_id": "...",
  "token_expires_at": "..." | null,
  "refresh_token_expires_at": "..." | null,
  "error": null | "import 失败的具体原因"
}
```

仅当 credential_id 不存在 / 不属于该项目时返回 `404`。前端总能渲染结果，失败时把 `error` 展示出来。

**端点实现：** `/test` 端点用 `try/except ValueError` 捕获 `check_status`——但按上面的契约，`ValueError` 只在「credential 不存在」时抛出，故直接转 `404`；其余情况 `check_status` 返回 dict（`error` 可为 `None`），端点直接以 `200` 返回。`/import` 端点则捕获 `create_from_import` 抛出的 `ValueError`（base64 无效 / import 失败）转 `400`。

## UI 结构（`project_detail.js` 的 `loadCredentials`）

复用现有 UI 模式：原生 `<dialog>`（仿 `showEnvEditor` 的 `env-dialog`）、`data-table`、`status-tag`、现有 device-flow 的 start/poll/finalize 逻辑。无需新 CSS 体系。

### 统一添加对话框（替代现在的 `prompt` + 内联卡片）

字段：
- **凭证类型**：`<select>`，目前仅「DWS（钉钉）」一项，默认选中，预留扩展。
- **创建方式**：radio——「导入已有凭证」/「设备码登录」。
- **凭证名称**：输入框。

根据创建方式切换下方区域：
- **导入**：`<textarea>`（粘贴 base64）+ `<input type="file">`（`FileReader.readAsText` 读内容填入 textarea）。提交时统一取 textarea 内容 → `POST /import`。
- **设备码**：把现有 device-flow 的 start/poll/finalize 逻辑搬进对话框内容区（逻辑不变，只换 DOM 挂载点），体验统一。

### 列表表格列调整

| 名称 | 类型 | CorpId | 状态 | Access 过期 | Refresh 过期 | 操作 |
|---|---|---|---|---|---|---|

- **类型**：徽标 `DWS`。
- **有效期拆两列**：Refresh 过期用次要（`text-muted`）样式；已过期红（`is-failed`）、临期 <24h 黄（`is-running`）。
- 时间显示绝对值 + 相对值（"还剩 3 天" / "已过期 2h"）。

### 操作列

「测试」按钮 + 「吊销」按钮。
- 测试 → `POST /{id}/test` → 成功刷新整表（`loadCredentials`）并 toast 最新状态；失败（`error` 非空）展示具体原因。

## 迁移策略

见「数据模型」节。纯新增一个带默认值的列，存量行自动回填 `dws`，无需数据搬运。

## 测试策略

沿用现有目录（`tests/api`、`tests/services`）与 `conftest.py` 的 fixture（`async_client` + `dependency_overrides` + service 层实例化 + patch `_session_factory`）。dws 子进程用 mock，真实 CLI 调用留给 `@pytest.mark.integration`（默认跳过）。

| 层 | 文件 | 覆盖点 |
|---|---|---|
| 服务 | `tests/services/test_credential_service.py`（追加） | `create_from_import` 成功存入 type/corp_id/expires（mock `_probe_auth_blob`）；无效 blob 不入库且抛错；`_probe_auth_blob` import 失败抛 `ValueError`（mock `create_subprocess_exec`）；`check_status` 成功回写元数据；refresh 过期 → `expired`；import 失败 → 回写 `expired` 且返回带 `error` 的 dict（不抛错）；credential 不存在 → 抛 `ValueError`；**回归**：`finalize_login` 持久化过期时间 |
| API | `tests/api/test_credentials.py`（追加+改写） | `POST /import` 成功(200)/无效(400)；`POST /{id}/test` 成功(200,带 status/error)；更新 `test_list` 断言新字段；旧 `GET /status` 测试**重写**为 `POST /test` |

## 风险与权衡

- **导入格式校验依赖 dws CLI**：仅做 base64 可解码的前置校验，真正的有效性由 `dws auth import` 判定。CLI 不可用时导入/测试都会失败，错误信息透传给用户。可接受（与设备码流程对 CLI 的依赖一致）。
- **`status` 主动改写**：测试会把 `expired` 凭证恢复为 `active`（refresh 未过期时）。这是期望行为——测试即「重新探测可用性」。不做「只允许 active→expired 单向降级」的限制，避免凭证恢复后状态卡死。
- **`credential_type` 预留字段（YAGNI 边界）**：当前只有 `dws`，加列有轻微「为未来预留」成分。但用户明确要求「凭证类型选择」，且枚举类型 0001 已建好、加列成本极低，让「选择」可持久化、可扩展，权衡后采纳。
- **统一对话框搬迁 device-flow**：设备码逻辑从内联卡片迁入对话框，改动量中等。换来两种创建方式 UI 一致，体验收益值得。
