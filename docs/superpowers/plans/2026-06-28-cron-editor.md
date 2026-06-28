# Cron 友好编辑器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 task 表单增加一个图形化 cron 编辑器（模板 + 可视化字段 + 实时中文描述 + 下次执行预览），不调用大模型。

**Architecture:** 后端新增 `POST /api/cron/preview`（复用 croniter 算下次执行，时区 Asia/Shanghai）；前端引入本地托管的 cronstrue（cron→中文）+ 新建 `cron_editor.js`（dialog 内模板/字段/预览/写回）。保留现有 input 手动输入与提交契约，后端 `_validate_cron` 兜底不动。

**Tech Stack:** Python 3.12 / FastAPI / croniter（后端）；原生 HTML+JS（无构建）/ cronstrue 3.21.0 UMD + zh_CN locale（前端）

## Global Constraints

- cron 表达式必须为标准 **5 字段**（APScheduler `from_crontab` 只吃 5 字段）。
- 时区统一 **Asia/Shanghai**（与 `scheduler_service.py:30` 一致）。
- 前端**无构建步骤、无 npm**；第三方 JS 以 UMD 文件本地托管于 `static/vendor/`。
- CSP 已允许 `script-src 'self' 'unsafe-inline'`，本地 vendor 脚本合规。
- 保留提交契约：`schedule_cron = enabled ? input.value : null`（`task_form.js:41`）。
- 后端 `_validate_cron`（`api/tasks.py:31-36`）保留不动，作为兜底。
- 范围仅限 task 表单；不改全局 keepalive cron。
- 文案硬编码简体中文（项目无 i18n）。

## 文件结构

| 文件 | 操作 | 贌责 |
|---|---|---|
| `docupipe_manager/api/cron.py` | 新增 | `POST /api/cron/preview`，croniter 算下次执行 |
| `docupipe_manager/main.py` | 改 | 注册 cron router（:259, :272 附近） |
| `tests/api/test_cron.py` | 新增 | preview API 测试 |
| `docupipe_manager/static/vendor/cronstrue/cronstrue.min.js` | 新增 | cronstrue 主库 UMD |
| `docupipe_manager/static/vendor/cronstrue/zh_CN.min.js` | 新增 | 中文 locale UMD |
| `docupipe_manager/static/js/cron_editor.js` | 新增 | dialog 渲染/模板/字段/预览/写回 |
| `docupipe_manager/templates/docupipe/task_form.html` | 改 | schedule-row 加编辑按钮 + 底部内嵌 dialog + 引入脚本 |
| `docupipe_manager/static/js/task_form.js` | 改 | 编辑按钮打开 dialog 联动 |
| `docupipe_manager/static/css/docupipe.css` | 改 | `.cron-dialog`、模板按钮组、预览区样式 |

---

### Task 1: 后端 cron preview API

**Files:**
- Create: `docupipe_manager/api/cron.py`
- Modify: `docupipe_manager/main.py:259`（import 行附近）、`:272`（include_router 附近）
- Test: `tests/api/test_cron.py`

**Interfaces:**
- Produces: `POST /docupipe/api/cron/preview`
  - Request body: `{"cron": "0 3 * * *"}`
  - Response (合法): `{"valid": true, "next_runs": ["2026-06-29T03:00:00+08:00", ...]}`
  - Response (非法): `{"valid": false, "error": "无效的 cron 表达式（需为 5 字段）"}`
  - 鉴权: `Depends(get_current_user)`
- 依赖: `croniter`（已在 `pyproject.toml:12`）、`zoneinfo`（标准库）

- [ ] **Step 1: 写失败测试 — 合法 cron**

Create `tests/api/test_cron.py`:

```python
"""Tests for cron preview API."""
import uuid
from unittest.mock import patch

import pytest

from tests.conftest import override_get_current_user, clear_overrides


@pytest.mark.asyncio
async def test_preview_valid_cron(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    r = await async_client.post("/docupipe/api/cron/preview", json={"cron": "0 3 * * *"})
    assert r.status_code == 200
    data = r.json()
    assert data["valid"] is True
    assert len(data["next_runs"]) == 5
    # 时间递增
    runs = data["next_runs"]
    assert runs == sorted(runs)
    # 时区 +08:00
    assert "+08:00" in runs[0]
    clear_overrides()


@pytest.mark.asyncio
async def test_preview_invalid_cron(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    r = await async_client.post("/docupipe/api/cron/preview", json={"cron": "99 * * * *"})
    assert r.status_code == 200
    data = r.json()
    assert data["valid"] is False
    assert "error" in data
    clear_overrides()


@pytest.mark.asyncio
async def test_preview_rejects_six_fields(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    r = await async_client.post("/docupipe/api/cron/preview", json={"cron": "0 0 3 * * *"})
    assert r.status_code == 200
    assert r.json()["valid"] is False
    clear_overrides()


@pytest.mark.asyncio
async def test_preview_requires_auth(async_client):
    # 不调用 override_get_current_user → 未鉴权
    r = await async_client.post("/docupipe/api/cron/preview", json={"cron": "0 3 * * *"})
    assert r.status_code == 401
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/api/test_cron.py -v`
Expected: FAIL（路由 404，`cron.py` 尚未创建）

- [ ] **Step 3: 实现 `api/cron.py`**

Create `docupipe_manager/api/cron.py`:

```python
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from croniter import croniter
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from docupipe_manager.auth.dependencies import get_current_user

router = APIRouter(prefix="/api/cron", tags=["cron"])

_TZ = ZoneInfo("Asia/Shanghai")
_NEXT_COUNT = 5


class CronPreviewRequest(BaseModel):
    cron: str


@router.post("/preview")
async def preview_cron(body: CronPreviewRequest, user: dict = Depends(get_current_user)):
    cron = body.cron.strip()
    parts = cron.split()
    if len(parts) != 5 or not croniter.is_valid(cron):
        return {"valid": False, "error": "无效的 cron 表达式（需为 5 字段）"}
    now = datetime.now(_TZ)
    itr = croniter(cron, now)
    runs = [itr.get_next(datetime).isoformat() for _ in range(_NEXT_COUNT)]
    return {"valid": True, "next_runs": runs}
```

- [ ] **Step 4: 在 `main.py` 注册 router**

Modify `docupipe_manager/main.py`，在 schedules 的 import 行（:259）后新增 import：

```python
from docupipe_manager.api.cron import router as cron_router
```

在 include_router 区块（:272 `app.include_router(schedules_router, ...)` 之后）新增：

```python
app.include_router(cron_router, prefix="/docupipe")
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `pytest tests/api/test_cron.py -v`
Expected: 4 passed

- [ ] **Step 6: 运行全量测试，确认无回归**

Run: `pytest -q`
Expected: 全部通过（无新增失败）

- [ ] **Step 7: Commit**

```bash
git add docupipe_manager/api/cron.py docupipe_manager/main.py tests/api/test_cron.py
git commit -m "feat(cron): add POST /api/cron/preview endpoint"
```

---

### Task 2: 前端 cron 编辑器

**Files:**
- Create: `docupipe_manager/static/vendor/cronstrue/cronstrue.min.js`（下载）
- Create: `docupipe_manager/static/vendor/cronstrue/zh_CN.min.js`（下载）
- Create: `docupipe_manager/static/js/cron_editor.js`
- Modify: `docupipe_manager/templates/docupipe/task_form.html:24-31`（schedule-row）、末尾（dialog + 脚本）
- Modify: `docupipe_manager/static/js/task_form.js:14-33`（编辑按钮联动）
- Modify: `docupipe_manager/static/css/docupipe.css:298`（dialog 区块后扩展）

**Interfaces:**
- Consumes: `POST /docupipe/api/cron/preview`（Task 1）、`window.cronstrue`（vendor）
- Produces: `window.CronEditor.open(inputElement)` —— 打开 dialog，确认后写回 input.value

**说明：** 前端无自动化测试框架，本任务以手动验证清单作为验收。

- [ ] **Step 1: 下载 cronstrue vendor 文件**

```bash
mkdir -p docupipe_manager/static/vendor/cronstrue
curl -fL -o docupipe_manager/static/vendor/cronstrue/cronstrue.min.js \
  https://unpkg.com/cronstrue@3.21.0/dist/cronstrue.min.js
curl -fL -o docupipe_manager/static/vendor/cronstrue/zh_CN.min.js \
  https://unpkg.com/cronstrue@3.21.0/locales/zh_CN.min.js
```

验证：两个文件存在，`cronstrue.min.js` 约 22KB，`zh_CN.min.js` 约 5KB。

- [ ] **Step 2: 改造 `task_form.html` schedule-row**

Modify `docupipe_manager/templates/docupipe/task_form.html`，替换 `:24-31` 的 schedule form-group 为：

```html
    <div class="form-group">
      <div class="schedule-row">
        <label class="check-row">
          <input type="checkbox" name="schedule_enabled" checked> 调度 cron
        </label>
        <input name="schedule_cron" placeholder="如 0 3 * * *" class="form-control" disabled>
        <button type="button" id="cron-edit-btn" class="btn btn-secondary" disabled>图形编辑</button>
      </div>
    </div>
```

- [ ] **Step 3: 在 `task_form.html` 底部内嵌 dialog + 引入脚本**

Modify `docupipe_manager/templates/docupipe/task_form.html`，在 `</div>`（:56 content 闭合）之前、`<script src="...task_form.js">`（:57）之前插入：

```html
<dialog id="cron-editor-dialog" class="cron-dialog">
  <form method="dialog" id="cron-editor-form">
    <h3 style="margin-top:0">Cron 调度编辑器</h3>

    <div class="cron-section">
      <div class="cron-section-label">快捷模板</div>
      <div class="cron-templates" id="cron-templates"></div>
    </div>

    <div class="cron-section">
      <div class="cron-section-label">字段</div>
      <div id="cron-fields"></div>
    </div>

    <div class="cron-section cron-preview">
      <div class="cron-section-label">预览</div>
      <div class="cron-expr" id="cron-expr">0 3 * * *</div>
      <div class="cron-desc" id="cron-desc">每天凌晨 3:00</div>
      <div class="cron-next-label">下次执行</div>
      <ul class="cron-next" id="cron-next"></ul>
    </div>

    <div class="form-actions" style="margin-top:14px">
      <button type="button" value="cancel" id="cron-cancel" class="btn btn-secondary">取消</button>
      <button type="button" value="ok" id="cron-ok" class="btn btn-primary">确定</button>
    </div>
  </form>
</dialog>
```

然后把 `:57` 的 script 行替换为（按顺序加载）：

```html
<script src="/docupipe/static/vendor/cronstrue/cronstrue.min.js"></script>
<script src="/docupipe/static/vendor/cronstrue/zh_CN.min.js"></script>
<script src="/docupipe/static/js/cron_editor.js"></script>
<script src="/docupipe/static/js/task_form.js"></script>
```

- [ ] **Step 4: 扩展 `docupipe.css`**

Modify `docupipe_manager/static/css/docupipe.css`，在文件末尾（`:309` `dialog::backdrop` 之后）追加：

```css

/* ── Cron 编辑器 dialog ── */
.cron-dialog { max-width: 560px; }
.cron-dialog h3 { font-size: 17px; font-weight: 700; color: var(--text); }
.cron-section { margin-bottom: 16px; }
.cron-section-label {
  font-size: 12.5px; font-weight: 600; color: var(--text-secondary);
  margin-bottom: 8px; text-transform: uppercase; letter-spacing: .03em;
}
.cron-templates { display: flex; flex-wrap: wrap; gap: 8px; }
.cron-tpl-btn {
  padding: 6px 12px; font-size: 13px; font-family: var(--font);
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  background: var(--surface); color: var(--text); cursor: pointer;
  transition: border-color var(--transition), background var(--transition);
}
.cron-tpl-btn:hover { border-color: var(--primary); }
.cron-tpl-btn.is-active {
  background: var(--primary-light); border-color: var(--primary);
  color: var(--primary); font-weight: 600;
}
.cron-fields-row { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
.cron-field { display: flex; flex-direction: column; gap: 4px; }
.cron-field label { font-size: 12px; color: var(--text-secondary); }
.cron-field input, .cron-field select {
  padding: 6px 10px; border: 1px solid var(--border);
  border-radius: var(--radius-sm); font-size: 13px; width: 90px;
  font-family: var(--font); background: var(--surface); color: var(--text);
}
.cron-weekdays { display: flex; gap: 6px; }
.cron-wd {
  width: 32px; height: 32px; display: inline-flex; align-items: center;
  justify-content: center; border: 1px solid var(--border); border-radius: 50%;
  background: var(--surface); cursor: pointer; font-size: 13px; color: var(--text);
  transition: background var(--transition), color var(--transition);
}
.cron-wd.is-active { background: var(--primary); color: #fff; border-color: var(--primary); }
.cron-expr {
  font-family: 'SF Mono', 'Fira Code', monospace; font-size: 14px;
  background: var(--bg); padding: 8px 12px; border-radius: var(--radius-sm);
  color: var(--text); margin-bottom: 8px;
}
.cron-desc { font-size: 14px; color: var(--primary); font-weight: 600; margin-bottom: 10px; }
.cron-desc.is-error { color: var(--error-text); }
.cron-next-label { font-size: 12px; color: var(--text-muted); margin-bottom: 4px; }
.cron-next { list-style: none; margin: 0; padding: 0; }
.cron-next li {
  font-size: 12.5px; color: var(--text-secondary); padding: 2px 0;
  font-family: 'SF Mono', 'Fira Code', monospace;
}
```

- [ ] **Step 5: 创建 `cron_editor.js`**

Create `docupipe_manager/static/js/cron_editor.js`:

```javascript
(function () {
  "use strict";

  var TEMPLATES = [
    { id: "daily", label: "每天" },
    { id: "weekly", label: "每周" },
    { id: "monthly", label: "每月" },
    { id: "hourly", label: "每小时" },
    { id: "every_n", label: "每 N 分钟" },
    { id: "weekday", label: "工作日" },
    { id: "custom", label: "自定义" },
  ];
  var WD_LABELS = ["日", "一", "二", "三", "四", "五", "六"];

  // 当前编辑状态
  var state = {
    template: "daily",
    hour: 3, minute: 0,           // 用于 daily/weekly/monthly/weekday
    weekdays: [1],                 // 0=日 .. 6=六
    day: 1,                        // 每月几号
    n: 5,                          // 每 N 分钟
    fMin: "*", fHour: "*", fDay: "*", fMonth: "*", fDow: "*",  // 自定义 5 字段
  };
  var targetInput = null;
  var previewTimer = null;
  var dialog, templatesEl, fieldsEl, exprEl, descEl, nextEl, okBtn;

  function $(sel) { return dialog.querySelector(sel); }

  // 根据当前 template + 字段组装 cron 字符串
  function buildCron() {
    var h = state.hour, m = state.minute;
    switch (state.template) {
      case "daily":    return m + " " + h + " * * *";
      case "weekly":   return m + " " + h + " * * " + sortedWd(state.weekdays);
      case "monthly":  return m + " " + h + " " + state.day + " * *";
      case "hourly":   return m + " * * * *";
      case "every_n":  return "*/" + state.n + " * * * *";
      case "weekday":  return m + " " + h + " * * 1-5";
      case "custom":   return [state.fMin, state.fHour, state.fDay, state.fMonth, state.fDow].join(" ");
    }
    return "";
  }

  function sortedWd(arr) {
    return arr.slice().sort(function (a, b) { return a - b; }).join(",");
  }

  // 尝试把 cron 反解析回 state.template + 字段（尽力而为）
  function parseIntoState(expr) {
    var parts = String(expr || "").trim().split(/\s+/);
    state.fMin = parts[0] || "*";
    state.fHour = parts[1] || "*";
    state.fDay = parts[2] || "*";
    state.fMonth = parts[3] || "*";
    state.fDow = parts[4] || "*";
    if (parts.length !== 5) { state.template = "custom"; return; }

    function isStar(s) { return s === "*"; }
    function isInt(s) { return /^\d+$/.test(s); }

    var m = parts[0], h = parts[1], d = parts[2], mo = parts[3], dow = parts[4];
    var hmInt = isInt(m) && isInt(h);

    // 每天 M H * * *
    if (hmInt && isStar(d) && isStar(mo) && isStar(dow)) {
      state.template = "daily"; state.minute = +m; state.hour = +h; return;
    }
    // 工作日 M H * * 1-5
    if (hmInt && isStar(d) && isStar(mo) && dow === "1-5") {
      state.template = "weekday"; state.minute = +m; state.hour = +h; return;
    }
    // 每周 M H * * D[,D...]
    if (hmInt && isStar(d) && isStar(mo) && /^[\d,]+$/.test(dow) && dow !== "1-5") {
      state.template = "weekly"; state.minute = +m; state.hour = +h;
      state.weekdays = dow.split(",").map(Number); return;
    }
    // 每月 M H D * *
    if (hmInt && isInt(d) && isStar(mo) && isStar(dow)) {
      state.template = "monthly"; state.minute = +m; state.hour = +h; state.day = +d; return;
    }
    // 每小时 M * * * *
    if (isInt(m) && isStar(h) && isStar(d) && isStar(mo) && isStar(dow)) {
      state.template = "hourly"; state.minute = +m; return;
    }
    // 每 N 分钟 */N * * * *
    if (/^\*\/\d+$/.test(m) && isStar(h) && isStar(d) && isStar(mo) && isStar(dow)) {
      state.template = "every_n"; state.n = parseInt(m.slice(2), 10); return;
    }
    state.template = "custom";
  }

  // 渲染模板按钮
  function renderTemplates() {
    templatesEl.innerHTML = "";
    TEMPLATES.forEach(function (t) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "cron-tpl-btn" + (state.template === t.id ? " is-active" : "");
      b.textContent = t.label;
      b.addEventListener("click", function () {
        state.template = t.id;
        renderTemplates();
        renderFields();
        schedulePreview();
      });
      templatesEl.appendChild(b);
    });
  }

  // 渲染字段区（随 template 变化）
  function renderFields() {
    fieldsEl.innerHTML = "";
    var row = document.createElement("div");
    row.className = "cron-fields-row";

    function timeField() {
      var wrap = document.createElement("div");
      wrap.className = "cron-field";
      var lab = document.createElement("label"); lab.textContent = "时间 (时:分)";
      var inp = document.createElement("input");
      inp.type = "time";
      inp.value = pad(state.hour) + ":" + pad(state.minute);
      inp.addEventListener("input", function () {
        var p = inp.value.split(":");
        state.hour = +p[0] || 0; state.minute = +p[1] || 0;
        schedulePreview();
      });
      wrap.appendChild(lab); wrap.appendChild(inp);
      return wrap;
    }
    function minuteField() {
      var wrap = document.createElement("div");
      wrap.className = "cron-field";
      var lab = document.createElement("label"); lab.textContent = "分钟 (0-59)";
      var inp = document.createElement("input");
      inp.type = "number"; inp.min = 0; inp.max = 59; inp.value = state.minute;
      inp.addEventListener("input", function () { state.minute = clamp(inp.value, 0, 59); schedulePreview(); });
      wrap.appendChild(lab); wrap.appendChild(inp);
      return wrap;
    }
    function nField() {
      var wrap = document.createElement("div");
      wrap.className = "cron-field";
      var lab = document.createElement("label"); lab.textContent = "N (分钟)";
      var inp = document.createElement("input");
      inp.type = "number"; inp.min = 1; inp.max = 59; inp.value = state.n;
      inp.addEventListener("input", function () { state.n = clamp(inp.value, 1, 59); schedulePreview(); });
      wrap.appendChild(lab); wrap.appendChild(inp);
      return wrap;
    }
    function dayField() {
      var wrap = document.createElement("div");
      wrap.className = "cron-field";
      var lab = document.createElement("label"); lab.textContent = "几号 (1-28)";
      var inp = document.createElement("input");
      inp.type = "number"; inp.min = 1; inp.max = 28; inp.value = state.day;
      inp.addEventListener("input", function () { state.day = clamp(inp.value, 1, 28); schedulePreview(); });
      wrap.appendChild(lab); wrap.appendChild(inp);
      return wrap;
    }
    function weekdaysField() {
      var wrap = document.createElement("div");
      wrap.className = "cron-field";
      var lab = document.createElement("label"); lab.textContent = "星期"; lab.style.marginBottom = "2px";
      var group = document.createElement("div"); group.className = "cron-weekdays";
      WD_LABELS.forEach(function (lbl, idx) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "cron-wd" + (state.weekdays.indexOf(idx) >= 0 ? " is-active" : "");
        b.textContent = lbl;
        b.addEventListener("click", function () {
          var i = state.weekdays.indexOf(idx);
          if (i >= 0) { if (state.weekdays.length > 1) state.weekdays.splice(i, 1); }
          else state.weekdays.push(idx);
          renderFields(); schedulePreview();
        });
        group.appendChild(b);
      });
      wrap.appendChild(lab); wrap.appendChild(group);
      return wrap;
    }
    function customField(label, key) {
      var wrap = document.createElement("div");
      wrap.className = "cron-field";
      var lab = document.createElement("label"); lab.textContent = label;
      var inp = document.createElement("input");
      inp.value = state[key];
      inp.addEventListener("input", function () { state[key] = inp.value.trim() || "*"; schedulePreview(); });
      wrap.appendChild(lab); wrap.appendChild(inp);
      return wrap;
    }

    switch (state.template) {
      case "daily":    row.appendChild(timeField()); break;
      case "weekly":   row.appendChild(weekdaysField()); row.appendChild(timeField()); break;
      case "monthly":  row.appendChild(dayField()); row.appendChild(timeField()); break;
      case "hourly":   row.appendChild(minuteField()); break;
      case "every_n":  row.appendChild(nField()); break;
      case "weekday":  row.appendChild(timeField()); break;
      case "custom":
        row.appendChild(customField("分", "fMin"));
        row.appendChild(customField("时", "fHour"));
        row.appendChild(customField("日", "fDay"));
        row.appendChild(customField("月", "fMonth"));
        row.appendChild(customField("周", "fDow"));
        break;
    }
    fieldsEl.appendChild(row);
  }

  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function clamp(v, lo, hi) {
    var n = parseInt(v, 10);
    if (isNaN(n)) return lo;
    return Math.max(lo, Math.min(hi, n));
  }

  // 预览（debounce）
  function schedulePreview() {
    var expr = buildCron();
    exprEl.textContent = expr;
    // 中文描述：cronstrue（纯前端，降级容错）
    try {
      descEl.textContent = window.cronstrue
        ? cronstrue.toString(expr, { locale: "zh_CN" })
        : "（描述不可用）";
      descEl.classList.remove("is-error");
    } catch (e) {
      descEl.textContent = "表达式无效";
      descEl.classList.add("is-error");
    }
    okBtn.disabled = false;
    // 下次执行：后端（debounce 300ms）
    if (previewTimer) clearTimeout(previewTimer);
    previewTimer = setTimeout(function () { fetchNextRuns(expr); }, 300);
  }

  function fetchNextRuns(expr) {
    nextEl.innerHTML = '<li>计算中…</li>';
    fetch(API_PREFIX + "/api/cron/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cron: expr }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        nextEl.innerHTML = "";
        if (!data.valid) {
          var li = document.createElement("li");
          li.textContent = data.error || "无效";
          li.style.color = "var(--error-text)";
          nextEl.appendChild(li);
          okBtn.disabled = true;
          return;
        }
        data.next_runs.forEach(function (t) {
          var li = document.createElement("li");
          li.textContent = formatTime(t);
          nextEl.appendChild(li);
        });
      })
      .catch(function () {
        nextEl.innerHTML = '<li style="color:var(--error-text)">预览失败</li>';
      });
  }

  function formatTime(iso) {
    // 后端返回带时区 ISO，转 Asia/Shanghai 友好显示
    try {
      var d = new Date(iso);
      return d.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai",
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", hour12: false });
    } catch (e) { return iso; }
  }

  // 写回
  function confirmDialog() {
    if (okBtn.disabled) return;
    if (targetInput) targetInput.value = buildCron();
    dialog.close();
  }

  // 打开
  function open(input) {
    targetInput = input;
    parseIntoState(input.value);
    renderTemplates();
    renderFields();
    schedulePreview();
    dialog.showModal();
  }

  // 初始化（DOM 就绪后绑定）
  function init() {
    dialog = document.getElementById("cron-editor-dialog");
    if (!dialog) return;
    templatesEl = $("#cron-templates");
    fieldsEl = $("#cron-fields");
    exprEl = $("#cron-expr");
    descEl = $("#cron-desc");
    nextEl = $("#cron-next");
    okBtn = $("#cron-ok");

    $("#cron-cancel").addEventListener("click", function () { dialog.close(); });
    okBtn.addEventListener("click", confirmDialog);
    // 点 backdrop 关闭
    dialog.addEventListener("click", function (e) { if (e.target === dialog) dialog.close(); });

    window.CronEditor = { open: open };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
```

- [ ] **Step 6: 改 `task_form.js` 联动编辑按钮**

Modify `docupipe_manager/static/js/task_form.js`，把 `:14-33` 的启用/禁用联动块替换为（同时控制编辑按钮）：

```javascript
  const cronInput = document.querySelector('[name="schedule_cron"]');
  const enabledCheck = document.querySelector('[name="schedule_enabled"]');
  const cronEditBtn = document.getElementById("cron-edit-btn");

  if (tid) {
    const r = await fetch(`${API_PREFIX}/api/projects/${pid}/tasks/${tid}`);
    const t = await r.json();
    const f = document.getElementById("task-form");
    Object.entries(t).forEach(([k, v]) => {
      const el = f.elements[k];
      if (el && typeof v !== "object") el.value = v;
    });
    if (t.schedule_enabled === false) f.elements.schedule_enabled.checked = false;
    if (t.credential_id) sel.value = t.credential_id;
    f.elements.slug.readOnly = true;
  }

  function syncCronEnabled() {
    var on = enabledCheck.checked;
    cronInput.disabled = !on;
    cronEditBtn.disabled = !on;
  }
  syncCronEnabled();
  enabledCheck.addEventListener("change", syncCronEnabled);
  cronEditBtn.addEventListener("click", function () {
    if (window.CronEditor) CronEditor.open(cronInput);
  });
```

- [ ] **Step 7: 手动验证清单**

启动服务后，逐项验证：

1. 访问 `/docupipe/projects/<pid>` → 新建任务页。
2. 调度复选框**未勾选** → input + "图形编辑"按钮均 disabled。
3. 勾选 → 按钮启用，点"图形编辑" → dialog 打开。
4. 依次点 7 个模板，确认字段区与表达式切换正确：
   - 每天 → 仅时间，表达式 `M H * * *`
   - 每周 → 星期多选 + 时间，选多个时表达式用逗号
   - 每月 → 几号 + 时间，表达式 `M H D * *`
   - 每小时 → 分钟，表达式 `M * * * *`
   - 每 N 分钟 → N，表达式 `*/N * * * *`
   - 工作日 → 时间，表达式 `M H * * 1-5`
   - 自定义 → 5 字段
5. 预览区：表达式、中文描述（cronstrue）、下次执行 5 条（来自后端，时区 Asia/Shanghai）实时更新。
6. 改任意字段 → 预览 debounce 后刷新（~300ms）。
7. 点"确定" → dialog 关闭，input 值更新为当前 cron。
8. 提交表单 → 保存成功，跳转回项目页。
9. 编辑已有任务（有 cron） → 打开 dialog 反解析高亮对应模板（如 `0 3 * * *` 高亮"每天"）。
10. 编辑已有任务（schedule_cron 为 null） → dialog 默认"每天 03:00"。
11. 自定义模式输入非法（如分填 `99`）→ 后端预览返回 invalid，确定按钮禁用。
12. 点 backdrop / Esc / 取消 → 不写回 input，关闭。
13. 手动改 input 值再开 dialog → 按新值反解析。

- [ ] **Step 8: Commit**

```bash
git add docupipe_manager/static/vendor/cronstrue docupipe_manager/static/js/cron_editor.js \
        docupipe_manager/static/js/task_form.js docupipe_manager/templates/docupipe/task_form.html \
        docupipe_manager/static/css/docupipe.css
git commit -m "feat(cron): add graphical cron editor dialog with live preview"
```

---

## Self-Review

**1. Spec coverage:**
- 模板 + 可视化字段 → Task 2 Step 3/5 ✓
- 实时中文描述（cronstrue）→ Task 2 Step 5 `schedulePreview` ✓
- 实时下次执行（后端 croniter）→ Task 1 + Task 2 Step 5 `fetchNextRuns` ✓
- 保留手动 input → Task 2 Step 2（input 保留）✓
- 提交契约 / null 语义 → Task 2 Step 6 未改提交逻辑（`task_form.js:36-48` 不动）✓
- 后端 `_validate_cron` 不动 → 未触及 `api/tasks.py` ✓
- 时区 Asia/Shanghai → Task 1 `_TZ` ✓
- 6 字段拒绝 → Task 1 `len(parts) != 5` + 测试 ✓
- cronstrue 加载降级 → Task 2 Step 5 try/catch ✓
- 鉴权 get_current_user → Task 1 + 401 测试 ✓

**2. Placeholder scan:** 无 TBD/TODO；前端代码为完整实现；测试为完整代码。

**3. Type consistency:** `window.CronEditor.open(input)` 在 Step 5 定义、Step 6 调用一致；`CronPreviewRequest.cron` 在 Task 1 测试与实现一致；后端路径 `/docupipe/api/cron/preview` 与前端 `API_PREFIX + "/api/cron/preview"` 一致。

## 执行说明

- Task 1（后端）有 pytest 自动化测试，应先完成。
- Task 2（前端）依赖 Task 1 的 preview API，且无自动化测试，以手动验证清单为验收。
- 两个 Task 各自一次 commit。
