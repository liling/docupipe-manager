# Cron 友好编辑器设计

- **日期**: 2026-06-28
- **状态**: 已批准（待 spec 审查）
- **范围**: task 表单的 cron 编辑体验改造

## 背景与动机

当前 task 表单（`templates/docupipe/task_form.html:24-31`）的 cron 输入只是一个普通 `<input>` 文本框 + 启用复选框，零校验、零预览。用户必须手写标准 5 字段 cron 表达式，错误只能靠提交后服务端返回 422 + `alert` 弹窗发现，体验差且容易错配。

目标：增加一个友好的图形编辑器，用户无需理解 cron 语法即可配置调度，同时保留对高级用户手动输入的兼容。

## 核心决策（不调用大模型）

- **完全开放自然语言 → cron 无法脱离 LLM 可靠完成**（歧义太多）。
- 本设计采用业内主流的**混合方案**：预设模板快捷选择 + 可视化字段微调 + 实时中文预览 + 下次执行时间预览。
- 中文描述由前端 **cronstrue** 库生成；下次执行时间由后端 **croniter** 计算。**全程不调用大模型。**

## 目标 / 非目标

**目标**
- 提供模板化、可视化的 cron 编辑 dialog，覆盖每天/每周/每月/每小时/每 N 分钟/工作日/自定义等常见场景。
- 实时中文描述预览（cronstrue）。
- 实时下次执行时间预览（后端 croniter，时区 Asia/Shanghai，与调度器一致）。
- 保留现有手动 input 兼容高级用户。
- 保留现有提交契约与禁用态/null 语义，后端校验不动。

**非目标**
- 不做自由文本自然语言解析。
- 不改全局 keepalive cron 的编辑入口（仍由环境变量控制，schedules 页只读）。
- 不改后端调度逻辑、数据模型、字段长度。
- 不引入前端构建步骤或 npm 依赖。

## 现状参考

- 数据模型：`models/task.py:40` `schedule_cron: str | None`（最长 64 字符）。
- 后端校验：`api/tasks.py:31-36` `_validate_cron` 用 `croniter.is_valid`。
- 调度执行：`services/scheduler_service.py:60` `CronTrigger.from_crontab`（5 字段），时区写死 `Asia/Shanghai`（`:30`）。
- 提交契约：`static/js/task_form.js:41` `body.schedule_cron = enabled ? input.value : null`。
- 禁用态：`task_form.js:30-33` 复选框控制 input.disabled。
- 样式：`static/css/docupipe.css` 已有 `dialog`（`:298`，max-width 480px）、`.form-control`、`.check-row`、`.btn-*`、CSS 变量体系。
- dialog 惯用模式：`project_detail.js` 动态 `createElement("dialog")` + `showModal/close` + 点 backdrop 关闭。

## 设计

### 1. 架构与依赖落点

**新增文件**
- `static/vendor/cronstrue/cronstrue.min.js`（22KB，主库 UMD）
- `static/vendor/cronstrue/zh_CN.min.js`（5.1KB，中文 locale UMD）
- `static/js/cron_editor.js`（编辑器逻辑）
- `docupipe_manager/api/cron.py`（预览 API）

**改动文件**
- `templates/docupipe/task_form.html`（schedule-row 改造 + 底部内嵌 dialog + 引入脚本）
- `static/js/task_form.js`（编辑按钮联动、打开 dialog）
- `docupipe_manager/main.py`（注册 cron router）
- `static/css/docupipe.css`（`.cron-dialog` 宽度、模板按钮组、预览区样式，复用现有 CSS 变量）

**cronstrue 引入：本地托管**
- 下载到 `static/vendor/cronstrue/`（不依赖 CDN，符合内网/离线部署）。
- `task_form.html` 加载顺序：主库 → locale → 编辑器脚本。
- locale 文件名用下划线 `zh_CN`；调用 `cronstrue.toString(expr, { locale: 'zh_CN' })`。

**职责划分**
- 中文描述：前端 cronstrue（纯前端，零延迟）。
- 下次执行：后端 croniter（准确，时区一致）。

**后端预览 API**
```
POST /api/cron/preview
鉴权: Depends(get_current_user)  # 登录即可，非 admin（任务编辑者需用）
Body: { "cron": "0 3 * * *" }
Resp(合法): { "valid": true, "next_runs": ["2026-06-29T03:00:00+08:00", ...5条] }
Resp(非法): { "valid": false, "error": "无效的 cron 表达式" }
```
- 用 `croniter.is_valid` 校验；合法时 `croniter(expr).get_next(datetime)` 连续取 5 次。
- 时区 `Asia/Shanghai`，返回带时区的 ISO 字符串。
- 仅校验 5 字段（APScheduler 只吃 5 字段，拒绝 6 字段）。
- 纯计算，无数据访问。

### 2. 前端编辑器

**触发落点：schedule-row 改造**（`task_form.html:24-31`）

当前：`[复选框 调度cron] [input 框]`
改为：
```
[复选框 调度 cron] [input（保留，可手动输入）] [⚙ 编辑按钮]
```
- input 仍可手动输入（兼容高级用户 + 不破坏现有行为）。
- "编辑"按钮打开 dialog；dialog 确认后写回 input 值。
- 复选框未勾选 → input + 编辑按钮均 `disabled`（保持"未启用→null"语义）。

**dialog 落点：模板内嵌静态 HTML**（`task_form.html` 底部加 `<dialog id="cron-editor-dialog" class="cron-dialog">`）
- 结构复杂，用静态 HTML 比 `createElement` 更可读。
- 宽度：`.cron-dialog { max-width: 560px }`（覆盖默认 480px）。

**dialog 内部三段式布局**
```
┌─ Cron 调度编辑器 ─────────────────────┐
│ ① 快捷模板（按钮组，单选）            │
│   [每天] [每周] [每月] [每小时]        │
│   [每 N 分钟] [工作日] [自定义]        │
│                                        │
│ ② 字段编辑（随模板变化）              │
│   - 每天 → [时间 HH:MM]               │
│   - 每周 → [周几多选][时间]           │
│   - 每月 → [几号][时间]               │
│   - 自定义 → 5 字段全展开              │
│                                        │
│ ③ 实时预览                            │
│   表达式: 0 3 * * *（等宽字体）        │
│   描述: 每天凌晨 3:00（cronstrue）     │
│   下次执行:                           │
│     • 2026-06-29 03:00 …（5 条）       │
│                                        │
│         [取消]  [确定]                 │
└────────────────────────────────────────┘
```

**预览联动逻辑**（`cron_editor.js`）
- 模板/字段任意变化 → 组装 cron 字符串 → 并行：
  - `cronstrue.toString(expr, { locale: 'zh_CN' })` 即时生成中文（纯前端）。
  - `POST /api/cron/preview` 获取下次 5 次（debounce 300ms）。
- 表达式非法 → 预览区红字提示，确定按钮禁用。

**模板 → 字段映射**（点模板按钮自动填充字段）
| 模板 | 字段 | 生成表达式 |
|---|---|---|
| 每天 | 时间 HH:MM | `M H * * *` |
| 每周 | 周几多选（一~日）+ 时间 | `M H * * D` |
| 每月 | 几号（1-28，避开 29-31 防跳月歧义）+ 时间 | `M H D * *` |
| 每小时 | 分钟 | `M * * * *` |
| 每 N 分钟 | N | `*/N * * * *` |
| 工作日 | 时间 | `M H * * 1-5` |
| 自定义 | 5 字段全展开，各自支持单值/列表/范围/步长 | 用户组装 |

**写回机制**
- "确定"：组装当前 cron → 写入 `[name="schedule_cron"]` input → `dialog.close()`。
- "取消"/点 backdrop/Esc：不写回，关闭。
- 打开时：读当前 input 值，尽力反解析匹配模板；匹配不上 → 默认"自定义"，字段填入当前值。反解析是尽力而为，不强求精确。

### 3. 边界与错误处理

**禁用态 / null 语义**
- 复选框未勾选 → input + 编辑按钮 `disabled` → 提交 `schedule_cron = null`（与 `task_form.js:41` 一致）。
- 编辑现有任务：`schedule_cron` 为 null → dialog 默认"每天 03:00"；非 null → 反解析。

**校验链路（三层）**
1. 前端编辑器内：`cronstrue.toString()` 抛错 → 红字 + 确定按钮禁用。
2. 前端手动 input（可选增强）：`onblur` 调 `/api/cron/preview` 即时校验。
3. 后端兜底：现有 `_validate_cron`（`api/tasks.py:34`）保留不动。

**时区一致性**
- `/api/cron/preview` 用 `Asia/Shanghai`（与 `scheduler_service.py:30` 一致）。
- 前端不在前端再算时区，直接展示后端结果，避免双端不一致。

**cronstrue 加载降级**
- locale 加载失败 → 回落英文描述（编辑器仍可用）。
- 主库加载失败 → catch 异常，描述区显示"描述不可用"，下次执行预览（后端）仍正常，不阻塞主流程。

## 数据流

```
打开 dialog
  → 读 input 现值 → 反解析 → 高亮模板 / 填字段
用户点模板 / 改字段
  → 组装 cron → cronstrue 生成中文（前端）
                → POST /api/cron/preview（debounce 300ms）→ 渲染下次执行
点确定
  → 组装 cron → 写回 input → close dialog
表单提交（既有逻辑不变）
  → schedule_cron = enabled ? input.value : null
  → 后端 _validate_cron 兜底
```

## 测试策略

**后端**（pytest，新增 `tests/api/test_cron.py`，参照 `test_tasks.py` 的 client fixture）
- 合法 cron（`0 3 * * *`）→ 200，`valid:true`，返回 5 条 next_runs，时间递增，时区 +08:00。
- 非法 cron（`99 * * * *`）→ 200，`valid:false`，含 error。
- 边界：5 字段合法；6 字段拒绝；空字符串拒绝。
- 未登录 → 401（鉴权）。

**前端**（无测试框架，手动验证清单）
- 7 个模板各生成正确 cron 表达式。
- 自定义模式 5 字段组合（单值/列表/范围/步长）。
- 预览联动：中文描述 + 下次执行实时更新。
- 写回 input + 表单提交保存成功。
- 新建任务（schedule_cron=null）→ dialog 默认值。
- 编辑现有任务（有 cron）→ 反解析高亮模板。
- 复选框禁用态 → input + 编辑按钮 disabled。
- 点 backdrop / Esc / 取消按钮 → 不写回。
- cronstrue 主库加载失败 → 描述区降级，下次执行仍正常。

## 文件级落点清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `static/vendor/cronstrue/cronstrue.min.js` | 新增 | 主库 UMD（22KB） |
| `static/vendor/cronstrue/zh_CN.min.js` | 新增 | 中文 locale UMD（5.1KB） |
| `static/js/cron_editor.js` | 新增 | dialog 渲染、模板、字段联动、预览、写回 |
| `docupipe_manager/api/cron.py` | 新增 | `POST /api/cron/preview` |
| `docupipe_manager/main.py` | 改 | 注册 cron router |
| `templates/docupipe/task_form.html` | 改 | schedule-row 加编辑按钮 + 底部内嵌 dialog + 引入脚本 |
| `static/js/task_form.js` | 改 | 编辑按钮打开 dialog、写回联动 |
| `static/css/docupipe.css` | 改 | `.cron-dialog`、模板按钮组、预览区样式 |
| `tests/api/test_cron.py` | 新增 | 预览 API 测试 |

## 风险与未决

- **cronstrue 中文描述质量**：zh_CN locale 对部分复杂表达式描述可能偏机器味；但有下次执行时间兜底，不影响正确性。
- **反解析精度**：尽力而为，无法覆盖所有 cron 形态；匹配失败回退"自定义"，用户可手动调整。
- **静态资源版本管理**：cronstrue 文件本地托管，后续升级需手动替换；在 vendor 目录记录版本号（目录名或注释）。
