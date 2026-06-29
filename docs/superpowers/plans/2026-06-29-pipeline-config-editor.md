# 流水线配置编辑器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 task 表单的 `config.yaml` 增加图形编辑器，可视化呈现 source → steps → destination 的文档流向，点节点编辑参数，确认后写回 textarea。纯前端实现，零后端改动。

**Architecture:** 前端引入本地托管的 jsyaml + 手写节点 schema（`pipeline_schema.js`）+ 编辑器逻辑（`pipeline_editor.js`）。编辑器内部维护 state（`_preserved` 保留非 pipelines 字段 + `pipelines` 数组），YAML ↔ state 双向转换由 jsyaml 驱动。dialog 内 HTML/CSS 节点链 + 内嵌参数表单。保留现有 textarea 与提交契约，后端 `_validate_yaml` 兜底不动。

**Tech Stack:** 原生 HTML+JS（无构建、无 npm）/ js-yaml 4.1.0 UMD（本地托管）/ CSS（复用现有 CSS 变量）

## Global Constraints

- 前端**无构建步骤、无 npm**；第三方 JS 以 UMD 文件本地托管于 `static/vendor/`。
- CSP 已允许 `script-src 'self' 'unsafe-inline'`，本地 vendor 脚本合规（`main.py:199`）。
- 前端安全约定：DOM 构造用 `static/js/dom.js` 的 `DP.el`/`DP.fill`/`DP.clear`，**禁用 innerHTML**（历史 commit `31fbe2e` 防 XSS）。
- 保留提交契约：`body.config_yaml` 取自 textarea（`task_form.js:46`），后端 `_validate_yaml`（`api/tasks.py:16`）兜底不动。
- 编辑器只操作 `pipelines` 数组；config 其余顶层字段（`variables`/`converters`/`plugin_dirs`/组件默认值）经 `_preserved` 原样保留。
- 文案硬编码简体中文（项目无 i18n）。
- 节点 schema 基于 docupipe 0.1.4 各节点 `__init__` 签名手写；docupipe 升级新增节点时手动同步 `pipeline_schema.js`。
- dialog 惯用模式参考 `cron_editor.js`：IIFE 暴露 `window.XxxEditor.open(input)`，`task_form.js` 按钮联动。

## 文件结构

| 文件 | 操作 | 职责 |
|---|---|---|
| `docupipe_manager/static/vendor/js-yaml/js-yaml.min.js` | 新增 | YAML 解析/序列化 UMD（39KB） |
| `docupipe_manager/static/js/pipeline_schema.js` | 新增 | 节点 schema 静态数据，暴露 `window.PipelineSchema` |
| `docupipe_manager/static/js/pipeline_editor.js` | 新增 | dialog 渲染、节点链、参数表单、YAML 双向转换、写回 |
| `docupipe_manager/templates/docupipe/task_form.html` | 改 | config.yaml 行加编辑按钮 + 底部内嵌 dialog + 引入脚本 |
| `docupipe_manager/static/js/task_form.js` | 改 | 编辑按钮打开 dialog 联动 |
| `docupipe_manager/static/css/docupipe.css` | 改 | `.pipeline-dialog`、节点链、参数表单样式 |

---

### Task 1: 引入 jsyaml vendor 库

**Files:**
- Create: `docupipe_manager/static/vendor/js-yaml/js-yaml.min.js`

**Interfaces:**
- Produces: 全局 `window.jsyaml`（UMD，`jsyaml.load(text)` → object，`jsyaml.dump(obj)` → string）

- [ ] **Step 1: 下载 js-yaml 4.1.0 UMD 到 vendor 目录**

Run:
```bash
mkdir -p docupipe_manager/static/vendor/js-yaml
curl -sL -o docupipe_manager/static/vendor/js-yaml/js-yaml.min.js \
  "https://cdn.jsdelivr.net/npm/js-yaml@4.1.0/dist/js-yaml.min.js"
```

- [ ] **Step 2: 验证文件有效（UMD 头 + 体积约 39KB）**

Run:
```bash
wc -c docupipe_manager/static/vendor/js-yaml/js-yaml.min.js
head -c 80 docupipe_manager/static/vendor/js-yaml/js-yaml.min.js
```
Expected: 体积约 39430 字节；头部含 `/*! js-yaml 4.1.0`。

- [ ] **Step 3: 用 node 验证 load/dump 可用**

Run:
```bash
node -e "
globalThis.window = globalThis;
require('./docupipe_manager/static/vendor/js-yaml/js-yaml.min.js');
var o = window.jsyaml.load('a: 1\nb:\n  - 2\n  - 3');
console.log(JSON.stringify(o));
console.log(window.jsyaml.dump({a:1,b:[2,3]}));
"
```
Expected: 输出 `{"a":1,"b":[2,3]}` 和 dump 的 YAML 文本（`a: 1\nb:\n  - 2\n  - 3\n`）。

- [ ] **Step 4: Commit**

```bash
git add docupipe_manager/static/vendor/js-yaml/js-yaml.min.js
git commit -m "feat(editor): vendor js-yaml 4.1.0 for pipeline config editor"
```

---

### Task 2: 节点 schema 静态数据

**Files:**
- Create: `docupipe_manager/static/js/pipeline_schema.js`

**Interfaces:**
- Produces: `window.PipelineSchema` = `{ sources: [...], destinations: [...], steps: [...] }`
  - 每条：`{ type: string, label: string, params: Param[] }`
  - 每个 Param：`{ name: string, label: string, type: "str"|"enum"|"bool"|"list"|"int", default?: any, required?: bool, help?: string, options?: string[], envHint?: string }`
- 后续 Task 3+ 通过 `PipelineSchema.sources` 等查找节点定义。

- [ ] **Step 1: 写 pipeline_schema.js（完整 schema）**

Create `docupipe_manager/static/js/pipeline_schema.js`:

```javascript
(function () {
  "use strict";

  var sources = [
    {
      type: "dingtalk", label: "钉钉知识库",
      params: [
        { name: "mode", label: "模式", type: "enum", options: ["wiki", "doc"], default: "wiki", required: true, help: "wiki=知识库模式，doc=文件夹模式" },
        { name: "space", label: "知识库名", type: "str", help: "与 space_id 二选一（wiki 模式必填其一）" },
        { name: "space_id", label: "知识库 ID", type: "str", help: "与 space 二选一（wiki 模式必填其一）" },
        { name: "folder_id", label: "文件夹 ID", type: "str", help: "doc 模式必填" },
        { name: "folders", label: "文件夹路径", type: "list", help: "可多行，如 产品规划/解决方案" },
        { name: "include_types", label: "仅含类型", type: "list", help: "内容类型白名单，如 DOCUMENT,FILE" }
      ]
    },
    {
      type: "localdrive", label: "本地目录",
      params: [
        { name: "input_dir", label: "输入目录", type: "str", required: true, help: "本地文件系统绝对路径" },
        { name: "include", label: "包含 glob", type: "list", help: "如 *.md" },
        { name: "exclude", label: "排除 glob", type: "list", help: "如 *.tmp" }
      ]
    },
    {
      type: "tencent", label: "腾讯文档",
      params: [
        { name: "space_id", label: "知识库 ID", type: "str", required: true },
        { name: "folder_id", label: "文件夹 ID", type: "str" },
        { name: "include_types", label: "仅含类型", type: "list" }
      ]
    }
  ];

  var destinations = [
    {
      type: "hindsight", label: "Hindsight",
      params: [
        { name: "bank_id", label: "Bank ID", type: "str", envHint: "HINDSIGHT_BANK_ID" },
        { name: "api_url", label: "API URL", type: "str", envHint: "HINDSIGHT_API_URL" },
        { name: "api_key", label: "API Key", type: "str", envHint: "HINDSIGHT_API_KEY" },
        { name: "context_prefix", label: "上下文前缀", type: "str", envHint: "HINDSIGHT_CONTEXT" },
        { name: "document_id_template", label: "文档 ID 模板", type: "str", help: "可选，模板语法" },
        { name: "context_template", label: "上下文模板", type: "str" },
        { name: "extra_tags", label: "额外标签", type: "list" },
        { name: "extra_metadata", label: "额外元数据", type: "list", help: "每行 key: value" }
      ]
    },
    {
      type: "localdrive", label: "本地目录",
      params: [
        { name: "output_dir", label: "输出目录", type: "str", required: true },
        { name: "replace_extension", label: "替换扩展名为 .md", type: "bool", default: false },
        { name: "save_sidecar", label: "保存 sidecar .json", type: "bool", default: true },
        { name: "path_template", label: "路径模板", type: "str" }
      ]
    }
  ];

  var steps = [
    {
      type: "convert", label: "格式转换",
      params: [
        { name: "_note", label: "说明", type: "str", help: "由全局 converters.extensions 驱动，此处无参数。转换规则在 config 顶层 converters 配置。" }
      ]
    },
    {
      type: "image_description", label: "图片描述",
      params: [
        { name: "api_key", label: "API Key", type: "str", envHint: "OPENAI_API_KEY" },
        { name: "base_url", label: "Base URL", type: "str" },
        { name: "model", label: "模型", type: "str", default: "gpt-4o" },
        { name: "concurrency", label: "并发数", type: "int", default: 1 }
      ]
    },
    {
      type: "resolve_attachments", label: "附件解析",
      params: [
        { name: "_note", label: "说明", type: "str", help: "无参数。解析 markdown 中的本地附件引用并加入 Bundle。" }
      ]
    },
    {
      type: "tencent_delete", label: "腾讯文档删除",
      params: [
        { name: "remove_type", label: "删除类型", type: "enum", options: ["current", "all"], default: "current" }
      ]
    },
    {
      type: "excel_structured", label: "Excel 结构化",
      params: [
        { name: "fill_merged", label: "填充合并单元格", type: "bool", default: true },
        { name: "skip_hidden", label: "跳过隐藏表", type: "bool", default: true },
        { name: "skip_empty", label: "跳过空表", type: "bool", default: true }
      ]
    },
    {
      type: "s3_upload", label: "S3 上传",
      params: [
        { name: "endpoint_url", label: "Endpoint", type: "str", default: "http://localhost:9000" },
        { name: "region", label: "Region", type: "str", default: "us-east-1" },
        { name: "bucket", label: "Bucket", type: "str", required: true },
        { name: "access_key", label: "Access Key", type: "str" },
        { name: "secret_key", label: "Secret Key", type: "str" },
        { name: "prefix", label: "前缀", type: "str", default: "attachments" },
        { name: "url_prefix", label: "URL 前缀", type: "str" },
        { name: "roles", label: "处理角色", type: "list", help: "如 image，默认 image" }
      ]
    }
  ];

  function findByType(kind, type) {
    var list = kind === "source" ? sources : kind === "destination" ? destinations : steps;
    for (var i = 0; i < list.length; i++) {
      if (list[i].type === type) return list[i];
    }
    return null;
  }

  window.PipelineSchema = {
    sources: sources,
    destinations: destinations,
    steps: steps,
    findByType: findByType
  };
})();
```

- [ ] **Step 2: 用 node 验证 schema 结构**

Run:
```bash
node -e "
globalThis.window = globalThis;
require('./docupipe_manager/static/js/pipeline_schema.js');
var s = window.PipelineSchema;
console.log('sources:', s.sources.map(function(x){return x.type}).join(','));
console.log('destinations:', s.destinations.map(function(x){return x.type}).join(','));
console.log('steps:', s.steps.map(function(x){return x.type}).join(','));
console.log('find dingtalk:', !!s.findByType('source','dingtalk'));
console.log('find unknown:', s.findByType('source','nope'));
"
```
Expected:
```
sources: dingtalk,localdrive,tencent
destinations: hindsight,localdrive
steps: convert,image_description,resolve_attachments,tencent_delete,excel_structured,s3_upload
find dingtalk: true
find unknown: null
```

- [ ] **Step 3: Commit**

```bash
git add docupipe_manager/static/js/pipeline_schema.js
git commit -m "feat(editor): add pipeline node schema static data"
```

---

### Task 3: YAML 双向转换纯函数

**Files:**
- Create: `docupipe_manager/static/js/pipeline_editor.js`（本任务只写转换函数 + IIFE 骨架，UI 在后续任务补）

**Interfaces:**
- Produces:
  - `window.PipelineEditor._parseFromYaml(text)` → `{ _preserved: object, pipelines: Pipeline[] }`（解析失败抛 Error）
  - `window.PipelineEditor._buildToYaml(state)` → string
  - Pipeline 结构：`{ name, source: {type: kwargs}, destination: {type: kwargs}, steps: [{name, kwargs}], post_steps: [...], finalize_steps: [...], mode, change_detection, state_file, options }`
- 消费：`window.jsyaml`（Task 1）

- [ ] **Step 1: 写 pipeline_editor.js 骨架 + 转换函数**

Create `docupipe_manager/static/js/pipeline_editor.js`:

```javascript
(function () {
  "use strict";

  function normalizeStep(spec) {
    if (typeof spec === "string") return { name: spec, kwargs: {} };
    if (spec && typeof spec === "object") {
      var keys = Object.keys(spec);
      if (keys.length === 1) return { name: keys[0], kwargs: spec[keys[0]] || {} };
    }
    return { name: "", kwargs: {} };
  }

  function normalizePipeline(p) {
    p = p || {};
    var src = p.source || {};
    var srcKeys = Object.keys(src);
    var dst = p.destination || {};
    var dstKeys = Object.keys(dst);
    return {
      name: p.name || "",
      source: srcKeys.length === 1 ? { type: srcKeys[0], kwargs: src[srcKeys[0]] || {} } : { type: "", kwargs: {} },
      destination: dstKeys.length === 1 ? { type: dstKeys[0], kwargs: dst[dstKeys[0]] || {} } : { type: "", kwargs: {} },
      steps: (p.steps || []).map(normalizeStep),
      post_steps: (p.post_steps || []).map(normalizeStep),
      finalize_steps: (p.finalize_steps || []).map(normalizeStep),
      mode: p.mode || "full",
      change_detection: p.change_detection || "",
      state_file: p.state_file || "",
      options: p.options || {}
    };
  }

  function parseFromYaml(text) {
    var raw = window.jsyaml.load(text);
    if (raw == null) raw = {};
    if (typeof raw !== "object" || Array.isArray(raw)) {
      throw new Error("YAML 顶层必须是映射");
    }
    var preserved = {};
    var pipelines = raw.pipelines;
    Object.keys(raw).forEach(function (k) {
      if (k !== "pipelines") preserved[k] = raw[k];
    });
    if (pipelines != null && !Array.isArray(pipelines)) {
      throw new Error("pipelines 必须是列表");
    }
    pipelines = (pipelines || []).map(normalizePipeline);
    return { _preserved: preserved, pipelines: pipelines };
  }

  function stepToYaml(s) {
    var empty = true;
    for (var k in s.kwargs) { if (Object.prototype.hasOwnProperty.call(s.kwargs, k)) { empty = false; break; } }
    return empty ? s.name : (function () { var o = {}; o[s.name] = s.kwargs; return o; })();
  }

  function pipelineToYaml(p) {
    var out = { name: p.name };
    if (p.source.type) { var so = {}; so[p.source.type] = p.source.kwargs; out.source = so; }
    if (p.destination.type) { var do_ = {}; do_[p.destination.type] = p.destination.kwargs; out.destination = do_; }
    if (p.steps.length) out.steps = p.steps.map(stepToYaml);
    if (p.post_steps.length) out.post_steps = p.post_steps.map(stepToYaml);
    if (p.finalize_steps.length) out.finalize_steps = p.finalize_steps.map(stepToYaml);
    if (p.mode && p.mode !== "full") out.mode = p.mode;
    if (p.change_detection) out.change_detection = p.change_detection;
    if (p.state_file) out.state_file = p.state_file;
    var hasOpt = false; for (var k in p.options) { if (Object.prototype.hasOwnProperty.call(p.options, k)) { hasOpt = true; break; } }
    if (hasOpt) out.options = p.options;
    return out;
  }

  function buildToYaml(state) {
    var out = {};
    Object.keys(state._preserved).forEach(function (k) { out[k] = state._preserved[k]; });
    out.pipelines = state.pipelines.map(pipelineToYaml);
    return window.jsyaml.dump(out, { lineWidth: -1, noRefs: true });
  }

  window.PipelineEditor = {
    _parseFromYaml: parseFromYaml,
    _buildToYaml: buildToYaml
  };
})();
```

- [ ] **Step 2: 用 node 验证双向转换（多 pipeline + preserved + steps 字符串/dict 混用）**

Run:
```bash
node -e "
globalThis.window = globalThis;
require('./docupipe_manager/static/vendor/js-yaml/js-yaml.min.js');
require('./docupipe_manager/static/js/pipeline_editor.js');
var PE = window.PipelineEditor;
var yaml = 'variables:\n  script: return {}\nconverters:\n  extensions:\n    .docx: markitdown\npipelines:\n  - name: p1\n    source:\n      dingtalk:\n        space: 知识库A\n        folders:\n          - 目录1\n    destination:\n      hindsight:\n        bank_id: b1\n    steps:\n      - convert\n      - image_description:\n          model: gpt-4o\n    mode: incremental\n    change_detection: mtime\n  - name: p2\n    source:\n      localdrive:\n        input_dir: /tmp\n    destination:\n      localdrive:\n        output_dir: /out\n    steps: []\n';
var st = PE._parseFromYaml(yaml);
console.log('pipelines:', st.pipelines.length);
console.log('p1 source:', st.pipelines[0].source.type, JSON.stringify(st.pipelines[0].source.kwargs));
console.log('p1 steps:', JSON.stringify(st.pipelines[0].steps));
console.log('p1 mode:', st.pipelines[0].mode);
console.log('preserved keys:', Object.keys(st._preserved).join(','));
console.log('---roundtrip---');
var back = PE._buildToYaml(st);
console.log(back);
"
```
Expected:
- `pipelines: 2`
- `p1 source: dingtalk {"space":"知识库A","folders":["目录1"]}`
- `p1 steps: [{"name":"convert","kwargs":{}},{"name":"image_description","kwargs":{"model":"gpt-4o"}}]`
- `p1 mode: incremental`
- `preserved keys: variables,converters`
- roundtrip YAML 含 `variables`/`converters`/两条 pipeline，`steps` 中 `convert` 输出为字符串 `- convert`，`image_description` 输出为 dict。

- [ ] **Step 3: 用 node 验证解析失败抛错**

Run:
```bash
node -e "
globalThis.window = globalThis;
require('./docupipe_manager/static/vendor/js-yaml/js-yaml.min.js');
require('./docupipe_manager/static/js/pipeline_editor.js');
var PE = window.PipelineEditor;
try { PE._parseFromYaml('- a\n- b'); console.log('NO THROW (bad)'); }
catch(e) { console.log('threw:', e.message); }
try { PE._parseFromYaml('pipelines: notalist'); console.log('NO THROW (bad)'); }
catch(e) { console.log('threw:', e.message); }
"
```
Expected: 两次均 `threw:`（"YAML 顶层必须是映射" / "pipelines 必须是列表"）。

- [ ] **Step 4: 用 node 验证空配置**

Run:
```bash
node -e "
globalThis.window = globalThis;
require('./docupipe_manager/static/vendor/js-yaml/js-yaml.min.js');
require('./docupipe_manager/static/js/pipeline_editor.js');
var PE = window.PipelineEditor;
var st = PE._parseFromYaml('pipelines:\n  - name: default');
console.log('pipelines:', st.pipelines.length);
console.log('p0 source type:', JSON.stringify(st.pipelines[0].source.type));
console.log('p0 steps:', st.pipelines[0].steps.length);
var empty = PE._parseFromYaml('');
console.log('empty pipelines:', empty.pipelines.length);
"
```
Expected: `pipelines: 1`、`p0 source type: ""`、`p0 steps: 0`、`empty pipelines: 0`。

- [ ] **Step 5: Commit**

```bash
git add docupipe_manager/static/js/pipeline_editor.js
git commit -m "feat(editor): add YAML<->state bidirectional conversion"
```

---

### Task 4: dialog 骨架与 pipeline 选择器

**Files:**
- Create: `docupipe_manager/static/html/pipeline_editor_test.html`（临时测试页，不提交；用于 chrome-devtools 验证 UI，因 task_form 需登录+Postgres）
- Modify: `docupipe_manager/static/js/pipeline_editor.js`（追加 dialog 绑定 + 渲染 pipeline 选择器）

**Interfaces:**
- Produces: `window.PipelineEditor.open(textarea)` — 打开 dialog，读 textarea 值解析为 state，渲染 pipeline 下拉；切换/新建/删除/重命名更新 state。
- 消费：`window.PipelineSchema`（Task 2）、`window.jsyaml`（Task 1）、`window.DP`（`dom.js`）

- [ ] **Step 1: 写临时测试页**

Create `docupipe_manager/static/html/pipeline_editor_test.html`:

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>pipeline editor test</title>
<style>
body { font-family: sans-serif; padding: 20px; }
textarea { width: 100%; height: 200px; font-family: monospace; }
</style>
</head>
<body>
<h3>config.yaml</h3>
<textarea id="cfg">pipelines:
  - name: default
    source:
      dingtalk:
        space: 测试知识库
    destination:
      hindsight:
        bank_id: b1
    steps:
      - convert
    mode: incremental</textarea>
<p><button id="open-btn">图形编辑</button></p>
<dialog id="pipeline-editor-dialog" class="pipeline-dialog"></dialog>
<script src="/docupipe/static/vendor/js-yaml/js-yaml.min.js"></script>
<script src="/docupipe/static/js/pipeline_schema.js"></script>
<script src="/docupipe/static/js/dom.js"></script>
<script src="/docupipe/static/js/pipeline_editor.js"></script>
<script>
document.getElementById("open-btn").addEventListener("click", function () {
  window.PipelineEditor.open(document.getElementById("cfg"));
});
</script>
</body>
</html>
```

- [ ] **Step 2: 追加 dialog 骨架与 pipeline 选择器到 pipeline_editor.js**

在 `pipeline_editor.js` 的 IIFE 内、`window.PipelineEditor = {...}` 之前追加（state 变量、DOM 引用、渲染函数、open/init），并扩展 `window.PipelineEditor` 暴露 `open`：

```javascript
  var state = null;
  var targetInput = null;
  var dialog, pipelineSelect, flowEl, paramsEl, okBtn, errEl;
  var activeIdx = 0;

  function $(sel) { return dialog.querySelector(sel); }

  function activePipeline() {
    return state.pipelines[activeIdx] || null;
  }

  function renderPipelineSelector() {
    DP.fill(pipelineSelect,
      state.pipelines.map(function (p, i) {
        return DP.el("option", { value: String(i), text: p.name || "(未命名)" });
      })
    );
    pipelineSelect.value = String(activeIdx);
  }

  function renderError(msg) {
    if (msg) {
      DP.fill(errEl, DP.el("div", { class: "pipeline-error", text: msg }));
      okBtn.disabled = true;
    } else {
      DP.clear(errEl);
      okBtn.disabled = false;
    }
  }

  function loadFromInput() {
    try {
      state = parseFromYaml(targetInput.value);
      if (!state.pipelines.length) {
        state.pipelines.push(normalizePipeline({ name: "default" }));
      }
      activeIdx = 0;
      renderError("");
      renderPipelineSelector();
      renderFlow();
    } catch (e) {
      state = null;
      renderError("YAML 解析失败：" + e.message);
      DP.clear(pipelineSelect);
      DP.clear(flowEl);
      DP.clear(paramsEl);
    }
  }

  function open(input) {
    targetInput = input;
    loadFromInput();
    dialog.showModal();
  }

  function confirmDialog() {
    if (okBtn.disabled || !state) return;
    targetInput.value = buildToYaml(state);
    dialog.close();
  }

  function init() {
    dialog = document.getElementById("pipeline-editor-dialog");
    if (!dialog) return;
    // 静态结构（dialog 内内容由 JS 构造，避免 innerHTML）
    var bar = DP.el("div", { class: "pipeline-bar" },
      DP.el("label", { text: "pipeline: " }),
      pipelineSelect = DP.el("select", { id: "pe-pipeline-select" }),
      DP.el("button", { type: "button", class: "btn btn-secondary", text: "+新建", onClick: function () {
        state.pipelines.push(normalizePipeline({ name: "pipeline-" + (state.pipelines.length + 1) }));
        activeIdx = state.pipelines.length - 1;
        renderPipelineSelector(); renderFlow();
      } }),
      DP.el("button", { type: "button", class: "btn btn-secondary", text: "删除", onClick: function () {
        if (state.pipelines.length <= 1) return;
        state.pipelines.splice(activeIdx, 1);
        activeIdx = Math.min(activeIdx, state.pipelines.length - 1);
        renderPipelineSelector(); renderFlow();
      } }),
      DP.el("button", { type: "button", class: "btn btn-secondary", text: "重命名", onClick: function () {
        var p = activePipeline(); if (!p) return;
        var n = prompt("pipeline 名称", p.name);
        if (n != null) { p.name = n; renderPipelineSelector(); }
      } })
    );
    errEl = DP.el("div", { class: "pipeline-err" });
    flowEl = DP.el("div", { class: "pipeline-flow" });
    paramsEl = DP.el("div", { class: "pipeline-params" });
    var actions = DP.el("div", { class: "form-actions" },
      DP.el("button", { type: "button", class: "btn btn-secondary", text: "取消", onClick: function () { dialog.close(); } }),
      okBtn = DP.el("button", { type: "button", class: "btn btn-primary", text: "确定", onClick: confirmDialog })
    );
    DP.fill(dialog, bar, errEl, flowEl, paramsEl, actions);
    pipelineSelect.addEventListener("change", function () {
      activeIdx = parseInt(pipelineSelect.value, 10) || 0;
      renderFlow();
    });
    dialog.addEventListener("click", function (e) { if (e.target === dialog) dialog.close(); });
  }

  function renderFlow() {
    // Task 5 实现
    DP.clear(flowEl);
    DP.clear(paramsEl);
  }
```

然后修改 `window.PipelineEditor` 赋值为：

```javascript
  window.PipelineEditor = {
    _parseFromYaml: parseFromYaml,
    _buildToYaml: buildToYaml,
    open: open
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
```

- [ ] **Step 3: 启动 dev server 验证 dialog 打开与 pipeline 选择器**

Run（后台起服务，需本地 Postgres + .env；若环境不具备，跳过本步并在 Task 8 集成时统一验证）:
```bash
uv run uvicorn docupipe_manager.main:app --port 8002 &
```

用 chrome-devtools 导航到 `http://localhost:8002/docupipe/static/html/pipeline_editor_test.html`，点"图形编辑"按钮，截图。Expected: dialog 打开，顶部 pipeline 下拉显示"default"，有"+新建/删除/重命名"按钮，底部"取消/确定"。

- [ ] **Step 4: 删除临时测试页，Commit**

```bash
rm docupipe_manager/static/html/pipeline_editor_test.html
git add docupipe_manager/static/js/pipeline_editor.js
git commit -m "feat(editor): add dialog skeleton and pipeline selector"
```

（注：测试页不提交，仅用于验证。后续 UI 任务同理用临时测试页验证后删除。）

---

### Task 5: 节点链渲染

**Files:**
- Modify: `docupipe_manager/static/js/pipeline_editor.js`（实现 `renderFlow` + 节点卡片渲染）

**Interfaces:**
- Produces: `renderFlow()` 渲染当前 pipeline 的 source → steps → destination → post_steps → finalize_steps 节点链；点节点高亮选中并调 `renderParams`（Task 6）。
- 消费：`window.PipelineSchema.findByType`（取 label）

- [ ] **Step 1: 实现 renderFlow 与节点卡片**

在 `pipeline_editor.js` 内追加（`renderFlow` 占位替换为如下实现），并新增辅助函数。`selectedNode` 用 `{segment, index}` 标识当前选中节点（segment ∈ "source"/"steps"/"destination"/"post_steps"/"finalize_steps"，index 为 steps 类段内的序号，source/destination 用 0）：

```javascript
  var selected = null; // { segment, index }

  function nodeLabel(kind, type) {
    var def = PipelineSchema.findByType(kind, type);
    return def ? def.label : (type ? type + "（未知）" : "未设置");
  }

  function nodeCard(kind, type, segment, index, opts) {
    opts = opts || {};
    var card = DP.el("div", {
      class: "pe-node" + (selected && selected.segment === segment && selected.index === index ? " is-selected" : ""),
      onClick: function () { selected = { segment: segment, index: index }; renderFlow(); }
    },
      DP.el("div", { class: "pe-node-kind", text: opts.kindLabel || kind }),
      DP.el("div", { class: "pe-node-name", text: nodeLabel(kind, type) })
    );
    if (opts.onDelete) {
      var x = DP.el("button", { type: "button", class: "pe-node-del", text: "×", onClick: function (e) { e.stopPropagation(); opts.onDelete(); } });
      card.appendChild(x);
    }
    if (!type) card.classList.add("is-empty");
    return card;
  }

  function arrow() {
    return DP.el("div", { class: "pe-arrow", text: "→" });
  }

  function addStepButton(arr, segment, index) {
    return DP.el("button", { type: "button", class: "pe-add-step", text: "+", onClick: function () {
      // Task 6 弹菜单选类型；此处先占位插入 convert
      arr.splice(index, 0, { name: "convert", kwargs: {} });
      renderFlow();
    } });
  }

  function renderStepsSegment(segment, kindLabel) {
    var p = activePipeline(); if (!p) return null;
    var arr = p[segment];
    if (!arr.length && (segment === "post_steps" || segment === "finalize_steps")) {
      // 折叠段，空时不渲染（由 renderFlow 处理折叠）
      return null;
    }
    var wrap = DP.el("div", { class: "pe-segment" },
      DP.el("div", { class: "pe-segment-label", text: kindLabel })
    );
    var row = DP.el("div", { class: "pe-node-row" });
    row.appendChild(addStepButton(arr, segment, 0));
    arr.forEach(function (s, i) {
      row.appendChild(nodeCard("step", s.name, segment, i, {
        kindLabel: kindLabel,
        onDelete: function () { arr.splice(i, 1); selected = null; renderFlow(); }
      }));
      row.appendChild(arrow());
      row.appendChild(addStepButton(arr, segment, i + 1));
    });
    wrap.appendChild(row);
    return wrap;
  }

  function renderFlow() {
    DP.clear(flowEl);
    DP.clear(paramsEl);
    var p = activePipeline();
    if (!p) return;

    var mainRow = DP.el("div", { class: "pe-node-row pe-main-row" });
    mainRow.appendChild(nodeCard("source", p.source.type, "source", 0, { kindLabel: "来源" }));
    mainRow.appendChild(arrow());

    if (p.steps.length) {
      p.steps.forEach(function (s, i) {
        mainRow.appendChild(nodeCard("step", s.name, "steps", i, {
          kindLabel: "步骤", onDelete: function () { p.steps.splice(i, 1); selected = null; renderFlow(); }
        }));
        mainRow.appendChild(arrow());
      });
    }
    mainRow.appendChild(nodeCard("destination", p.destination.type, "destination", 0, { kindLabel: "目的地" }));

    var mainWrap = DP.el("div", { class: "pe-segment" },
      DP.el("div", { class: "pe-segment-label", text: "文档流向" }), mainRow,
      DP.el("div", { class: "pe-add-step-row" },
        DP.el("button", { type: "button", class: "btn btn-secondary btn-sm", text: "+ 添加步骤", onClick: function () {
          p.steps.push({ name: "convert", kwargs: {} }); renderFlow();
        } })
      )
    );
    flowEl.appendChild(mainWrap);

    // post_steps / finalize_steps 折叠段
    ["post_steps", "finalize_steps"].forEach(function (seg) {
      var label = seg === "post_steps" ? "post_steps（写入后）" : "finalize_steps（全部完成后）";
      if (p[seg].length) {
        flowEl.appendChild(renderStepsSegment(seg, label));
      } else {
        flowEl.appendChild(DP.el("div", { class: "pe-segment pe-collapsed" },
          DP.el("button", { type: "button", class: "btn btn-secondary btn-sm", text: "+ 添加 " + label, onClick: function () {
            p[seg].push({ name: "convert", kwargs: {} }); renderFlow();
          } })
        ));
      }
    });

    // pipeline 选项
    flowEl.appendChild(renderPipelineOptions(p));

    // 渲染选中节点参数（Task 6）
    renderParams();
  }

  function renderPipelineOptions(p) {
    var wrap = DP.el("div", { class: "pe-pipeline-opts" },
      DP.el("div", { class: "pe-segment-label", text: "pipeline 选项" })
    );
    var row = DP.el("div", { class: "pe-opts-row" });
    // mode
    var modeWrap = DP.el("div", { class: "pe-opt" }, DP.el("label", { text: "运行模式" }));
    var modeSel = DP.el("select", {});
    ["full", "incremental", "mirror"].forEach(function (m) {
      modeSel.appendChild(DP.el("option", { value: m, text: m, selected: p.mode === m }));
    });
    modeSel.addEventListener("change", function () { p.mode = modeSel.value; });
    modeWrap.appendChild(modeSel);
    row.appendChild(modeWrap);
    // change_detection
    var cdWrap = DP.el("div", { class: "pe-opt" }, DP.el("label", { text: "变更检测" }));
    var cdSel = DP.el("select", {});
    ["", "mtime", "hash"].forEach(function (c) {
      cdSel.appendChild(DP.el("option", { value: c, text: c || "（无）", selected: p.change_detection === c }));
    });
    cdSel.addEventListener("change", function () { p.change_detection = cdSel.value; });
    cdWrap.appendChild(cdSel);
    row.appendChild(cdWrap);
    // state_file
    var sfWrap = DP.el("div", { class: "pe-opt" }, DP.el("label", { text: "state_file" }));
    var sfIn = DP.el("input", { value: p.state_file || "", placeholder: "（可选）" });
    sfIn.addEventListener("input", function () { p.state_file = sfIn.value; });
    sfWrap.appendChild(sfIn);
    row.appendChild(sfWrap);
    wrap.appendChild(row);
    return wrap;
  }

  function renderParams() {
    // Task 6 实现
  }
```

- [ ] **Step 2: 用 chrome-devtools 验证节点链渲染**

重建临时测试页（同 Task 4 Step 1），启动服务，打开测试页，点"图形编辑"。截图。Expected: 节点链显示 `来源·钉钉知识库 → 步骤·格式转换 → 目的地·Hindsight`，有箭头，"+ 添加步骤"按钮，pipeline 选项区有运行模式/变更检测/state_file。

- [ ] **Step 3: 验证点节点高亮**

点"来源·钉钉知识库"卡片，截图。Expected: 该卡片高亮（`is-selected` 类），其余取消高亮。

- [ ] **Step 4: 删除临时测试页，Commit**

```bash
rm -f docupipe_manager/static/html/pipeline_editor_test.html
git add docupipe_manager/static/js/pipeline_editor.js
git commit -m "feat(editor): render node chain for source/steps/destination"
```

---

### Task 6: 参数表单与节点类型切换

**Files:**
- Modify: `docupipe_manager/static/js/pipeline_editor.js`（实现 `renderParams` + source/destination 类型切换 + step 插入菜单 + 拖拽排序）

**Interfaces:**
- 消费：`window.PipelineSchema.sources/destinations/steps/findByType`（Task 2）

- [ ] **Step 1: 实现 renderParams（按 schema 渲染参数表单）**

在 `pipeline_editor.js` 内替换 `renderParams` 占位为：

```javascript
  function currentSelectedNode() {
    if (!selected) return null;
    var p = activePipeline(); if (!p) return null;
    if (selected.segment === "source") return { kind: "source", holder: p.source };
    if (selected.segment === "destination") return { kind: "destination", holder: p.destination };
    var arr = p[selected.segment];
    var s = arr[selected.index];
    if (!s) return null;
    return { kind: "step", holder: { type: s.name, kwargs: s.kwargs }, stepArr: arr, stepIndex: selected.index };
  }

  function renderField(param, value, onChange) {
    var wrap = DP.el("div", { class: "pe-field" });
    var lab = DP.el("label", { text: param.label + (param.required ? " *" : "") });
    wrap.appendChild(lab);
    var ctrl;
    if (param.type === "enum") {
      ctrl = DP.el("select", {});
      (param.options || []).forEach(function (o) {
        ctrl.appendChild(DP.el("option", { value: o, text: o, selected: value === o }));
      });
      ctrl.addEventListener("change", function () { onChange(ctrl.value); });
    } else if (param.type === "bool") {
      ctrl = DP.el("input", { type: "checkbox" });
      if (value) ctrl.checked = true;
      ctrl.addEventListener("change", function () { onChange(ctrl.checked); });
    } else if (param.type === "list") {
      ctrl = DP.el("textarea", { rows: 2, placeholder: "每行一项" });
      ctrl.value = Array.isArray(value) ? value.join("\n") : (value || "");
      ctrl.addEventListener("input", function () {
        var lines = ctrl.value.split("\n").map(function (l) { return l.trim(); }).filter(function (l) { return l; });
        onChange(lines);
      });
    } else if (param.type === "int") {
      ctrl = DP.el("input", { type: "number" });
      ctrl.value = value != null ? value : (param.default != null ? param.default : "");
      ctrl.addEventListener("input", function () { onChange(parseInt(ctrl.value, 10) || 0); });
    } else {
      // str（含 _note 只读说明）
      if (param.name === "_note") {
        wrap.appendChild(DP.el("div", { class: "pe-note", text: param.help || "" }));
        return wrap;
      }
      ctrl = DP.el("input", { value: value || "" });
      ctrl.addEventListener("input", function () { onChange(ctrl.value); });
    }
    wrap.appendChild(ctrl);
    if (param.help) wrap.appendChild(DP.el("div", { class: "pe-help", text: param.help }));
    if (param.envHint) wrap.appendChild(DP.el("div", { class: "pe-envhint", text: "可留空，从环境变量 " + param.envHint + " 读取" }));
    return wrap;
  }

  function typeSelector(kind, currentType, onSelect) {
    var wrap = DP.el("div", { class: "pe-type-sel" }, DP.el("label", { text: "类型" }));
    var sel = DP.el("select", {});
    sel.appendChild(DP.el("option", { value: "", text: "（选择）", selected: !currentType }));
    var list = kind === "source" ? PipelineSchema.sources : kind === "destination" ? PipelineSchema.destinations : PipelineSchema.steps;
    list.forEach(function (d) {
      sel.appendChild(DP.el("option", { value: d.type, text: d.label, selected: currentType === d.type }));
    });
    sel.addEventListener("change", function () { onSelect(sel.value); });
    wrap.appendChild(sel);
    return wrap;
  }

  function renderParams() {
    DP.clear(paramsEl);
    var node = currentSelectedNode();
    if (!node) {
      paramsEl.appendChild(DP.el("div", { class: "pe-params-empty", text: "点击上方节点编辑参数" }));
      return;
    }
    var kind = node.kind;
    var holder = node.holder;
    var title = kind === "source" ? "来源" : kind === "destination" ? "目的地" : "步骤";
    paramsEl.appendChild(DP.el("div", { class: "pe-params-title", text: "节点参数：" + title + " · " + nodeLabel(kind, holder.type) }));

    // 类型切换（source/destination）或 step 类型菜单
    if (kind === "source" || kind === "destination") {
      paramsEl.appendChild(typeSelector(kind, holder.type, function (newType) {
        holder.type = newType; holder.kwargs = {}; selected = null; renderFlow();
      }));
    }

    var def = PipelineSchema.findByType(kind, holder.type);
    if (!def) {
      // 未知节点：只读 YAML 文本框
      paramsEl.appendChild(DP.el("div", { class: "pe-unknown", text: "未知节点类型：" + holder.type + "（编辑原始 YAML）" }));
      var ta = DP.el("textarea", { rows: 6, class: "input-mono" });
      ta.value = window.jsyaml.dump(holder.kwargs || {});
      ta.addEventListener("input", function () {
        try { holder.kwargs = window.jsyaml.load(ta.value) || {}; } catch (e) { /* 容忍编辑中语法错误 */ }
      });
      paramsEl.appendChild(ta);
      return;
    }

    def.params.forEach(function (param) {
      var val = holder.kwargs[param.name];
      if (val == null && param.default != null) val = param.default;
      paramsEl.appendChild(renderField(param, val, function (nv) { holder.kwargs[param.name] = nv; }));
    });
  }
```

- [ ] **Step 2: 实现 step 插入类型菜单（替换 Task 5 的占位插入 convert）**

修改 `addStepButton`，点 `+` 时弹一个小菜单选 step 类型。在 `pipeline_editor.js` 内新增：

```javascript
  function stepTypeMenu(onPick) {
    var menu = DP.el("div", { class: "pe-step-menu" });
    PipelineSchema.steps.forEach(function (d) {
      menu.appendChild(DP.el("button", { type: "button", class: "pe-step-menu-item", text: d.label, onClick: function () {
        onPick(d.type); document.body.removeChild(menu);
      } }));
    });
    menu.style.position = "absolute";
    // 简化：菜单定位由 CSS 处理，此处附 body
    return menu;
  }

  var openMenu = null;
  function showStepMenu(anchor, onPick) {
    if (openMenu) { document.body.removeChild(openMenu); openMenu = null; }
    var menu = stepTypeMenu(onPick);
    var r = anchor.getBoundingClientRect();
    menu.style.left = r.left + "px";
    menu.style.top = (r.bottom + 4) + "px";
    document.body.appendChild(menu);
    openMenu = menu;
    setTimeout(function () {
      document.addEventListener("click", function close() {
        if (openMenu) { document.body.removeChild(openMenu); openMenu = null; }
        document.removeEventListener("click", close);
      }, { once: true });
    }, 0);
  }
```

修改 `addStepButton` 的 onClick 为：
```javascript
    onClick: function (e) {
      var btn = e.currentTarget;
      showStepMenu(btn, function (type) {
        arr.splice(index, 0, { name: type, kwargs: {} });
        renderFlow();
      });
    }
```

同样修改 `renderFlow` 里 "+ 添加步骤" 按钮和 post/finalize 的 "+ 添加" 按钮，改为调 `showStepMenu`。

- [ ] **Step 3: 实现步骤拖拽排序（HTML5 drag）**

修改 `renderStepsSegment` 与 `renderFlow` 中 step 卡片，加 `draggable=true` 与 drag 事件。新增辅助函数：

```javascript
  function makeDraggable(card, arr, index) {
    card.draggable = true;
    card.addEventListener("dragstart", function (e) {
      e.dataTransfer.setData("text/pe-idx", String(index));
      card.classList.add("is-dragging");
    });
    card.addEventListener("dragover", function (e) { e.preventDefault(); card.classList.add("is-drop-target"); });
    card.addEventListener("dragleave", function () { card.classList.remove("is-drop-target"); });
    card.addEventListener("drop", function (e) {
      e.preventDefault();
      var from = parseInt(e.dataTransfer.getData("text/pe-idx"), 10);
      if (isNaN(from) || from === index) return;
      var moved = arr.splice(from, 1)[0];
      arr.splice(index, 0, moved);
      selected = null;
      renderFlow();
    });
    card.addEventListener("dragend", function () { card.classList.remove("is-dragging"); });
    return card;
  }
```

在 `renderStepsSegment` 的 `arr.forEach` 里，构造 step 卡片后调 `makeDraggable(card, arr, i)`（在 `nodeCard` 返回后包装）。由于 `nodeCard` 返回的是已绑定 click 的元素，需在 `renderStepsSegment` 内对其追加 draggable。修改 `renderStepsSegment` 的 forEach：

```javascript
    arr.forEach(function (s, i) {
      var card = nodeCard("step", s.name, segment, i, {
        kindLabel: kindLabel,
        onDelete: function () { arr.splice(i, 1); selected = null; renderFlow(); }
      });
      makeDraggable(card, arr, i);
      row.appendChild(card);
      row.appendChild(arrow());
      row.appendChild(addStepButton(arr, segment, i + 1));
    });
```

`renderFlow` 主行里 `p.steps.forEach` 同理加 `makeDraggable(card, p.steps, i)`。

- [ ] **Step 4: 用 chrome-devtools 验证参数表单**

重建临时测试页，打开 dialog，点"来源·钉钉知识库"。截图。Expected: 参数区显示"节点参数：来源 · 钉钉知识库"，类型下拉选"钉钉知识库"，字段：模式(radio wiki/doc)、知识库名、知识库 ID、文件夹 ID、文件夹路径(list textarea)、仅含类型。

- [ ] **Step 5: 验证 source 类型切换**

在参数区"类型"下拉切到"本地目录"，截图。Expected: 卡片变成"本地目录"，参数区字段变为 输入目录(必填*)/包含 glob/排除 glob。

- [ ] **Step 6: 验证 step 插入菜单**

点步骤间的 `+` 按钮，截图。Expected: 弹出菜单列出 6 个 step 类型（格式转换/图片描述/...）。点"图片描述"，截图。Expected: 节点链插入"图片描述"卡片。

- [ ] **Step 7: 验证拖拽排序**

拖拽一个 step 卡片到另一个位置，截图。Expected: 步骤顺序改变。

- [ ] **Step 8: 验证写回 YAML**

点"确定"，查看 textarea 值。用 chrome-devtools evaluate_script:
```js
JSON.stringify(document.getElementById('cfg').value)
```
Expected: YAML 含 `pipelines`、source/destination/steps 与编辑一致，空段不输出。

- [ ] **Step 9: 删除临时测试页，Commit**

```bash
rm -f docupipe_manager/static/html/pipeline_editor_test.html
git add docupipe_manager/static/js/pipeline_editor.js
git commit -m "feat(editor): params form, type switching, step menu, drag reorder"
```

---

### Task 7: 集成到 task_form

**Files:**
- Modify: `docupipe_manager/templates/docupipe/task_form.html`
- Modify: `docupipe_manager/static/js/task_form.js`

**Interfaces:**
- 消费：`window.PipelineEditor.open(textarea)`（Task 4-6）

- [ ] **Step 1: 改 task_form.html — config.yaml 行加按钮 + 底部加 dialog + 引入脚本**

修改 `templates/docupipe/task_form.html:19-23`（config.yaml 的 form-group）为：

```html
    <div class="form-group">
      <label>config.yaml</label>
      <div class="config-row">
        <textarea name="config_yaml" rows="12" class="form-control input-mono" required>pipelines:
  - name: default</textarea>
        <button type="button" id="pipeline-edit-btn" class="btn btn-secondary">图形编辑</button>
      </div>
    </div>
```

在文件底部 `</form>` 之后、现有 cron dialog 之后（`</div>` 之前或之后均可，放在 `<dialog id="cron-editor-dialog">` 之后），加：

```html
<dialog id="pipeline-editor-dialog" class="pipeline-dialog"></dialog>
```

在底部 `<script>` 引入区（cronstrue 之后、`task_form.js` 之前）加：

```html
<script src="/docupipe/static/vendor/js-yaml/js-yaml.min.js"></script>
<script src="/docupipe/static/js/pipeline_schema.js"></script>
<script src="/docupipe/static/js/pipeline_editor.js"></script>
```

- [ ] **Step 2: 改 task_form.js — 按钮联动**

在 `task_form.js` 的 init 函数内（cron 按钮联动附近，约 `:38`），加：

```javascript
  var pipelineEditBtn = document.getElementById("pipeline-edit-btn");
  var cfgTextarea = document.querySelector('[name="config_yaml"]');
  if (pipelineEditBtn) {
    pipelineEditBtn.addEventListener("click", function () {
      if (window.PipelineEditor) PipelineEditor.open(cfgTextarea);
    });
  }
```

- [ ] **Step 3: Commit**

```bash
git add docupipe_manager/templates/docupipe/task_form.html docupipe_manager/static/js/task_form.js
git commit -m "feat(editor): integrate pipeline editor button into task form"
```

（端到端验证因需登录 + Postgres 留到 Task 9 统一做；本任务仅确保引入路径与按钮绑定正确。）

---

### Task 8: 样式

**Files:**
- Modify: `docupipe_manager/static/css/docupipe.css`（追加 pipeline 编辑器样式）

- [ ] **Step 1: 追加样式到 docupipe.css 末尾**

```css
/* ── 流水线配置编辑器 ── */
.pipeline-dialog { max-width: 820px; width: 90vw; }
.pipeline-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.pipeline-bar select { min-width: 140px; }
.pipeline-err { margin-bottom: 8px; }
.pipeline-error { color: var(--error-text); background: var(--error-bg, #fee); padding: 6px 10px; border-radius: 4px; font-size: 13px; }

.pipeline-flow { display: flex; flex-direction: column; gap: 14px; }
.pe-segment { border: 1px solid var(--border); border-radius: 6px; padding: 10px; }
.pe-segment-label { font-size: 12px; color: var(--text-secondary, #666); margin-bottom: 8px; font-weight: 600; }
.pe-collapsed { border-style: dashed; text-align: left; padding: 6px; }
.pe-node-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.pe-main-row { flex-wrap: wrap; }

.pe-node {
  border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px;
  background: var(--bg, #fff); cursor: pointer; min-width: 92px; text-align: center;
  position: relative; transition: border-color .12s, box-shadow .12s;
}
.pe-node:hover { border-color: var(--accent, #4a90d9); }
.pe-node.is-selected { border-color: var(--accent, #4a90d9); box-shadow: 0 0 0 2px rgba(74,144,217,.25); }
.pe-node.is-empty { border-style: dashed; color: var(--text-secondary, #999); }
.pe-node.is-dragging { opacity: .5; }
.pe-node.is-drop-target { border-color: var(--accent, #4a90d9); border-style: solid; }
.pe-node-kind { font-size: 11px; color: var(--text-secondary, #888); }
.pe-node-name { font-size: 13px; font-weight: 600; }
.pe-node-del {
  position: absolute; top: -6px; right: -6px; width: 16px; height: 16px;
  border-radius: 50%; background: var(--error-text, #c33); color: #fff;
  border: none; font-size: 11px; line-height: 16px; padding: 0; cursor: pointer;
}
.pe-arrow { color: var(--text-secondary, #999); font-size: 16px; }
.pe-add-step {
  width: 20px; height: 20px; border-radius: 50%; border: 1px dashed var(--border);
  background: transparent; color: var(--text-secondary, #999); cursor: pointer;
  font-size: 13px; line-height: 18px; padding: 0;
}
.pe-add-step:hover { border-color: var(--accent, #4a90d9); color: var(--accent, #4a90d9); }
.pe-add-step-row { margin-top: 8px; }

.pe-pipeline-opts { border: 1px solid var(--border); border-radius: 6px; padding: 10px; }
.pe-opts-row { display: flex; gap: 14px; flex-wrap: wrap; }
.pe-opt { display: flex; flex-direction: column; gap: 4px; }
.pe-opt label { font-size: 12px; color: var(--text-secondary, #666); }
.pe-opt select, .pe-opt input { min-width: 120px; }

.pipeline-params { border-top: 1px solid var(--border); margin-top: 14px; padding-top: 12px; }
.pe-params-title { font-weight: 600; margin-bottom: 10px; }
.pe-params-empty { color: var(--text-secondary, #999); padding: 16px 0; }
.pe-type-sel { margin-bottom: 12px; }
.pe-type-sel label { display: block; font-size: 12px; margin-bottom: 4px; }
.pe-field { margin-bottom: 10px; }
.pe-field label { display: block; font-size: 13px; margin-bottom: 4px; }
.pe-field input[type="text"], .pe-field input:not([type]), .pe-field select, .pe-field textarea {
  width: 100%; padding: 6px 8px; box-sizing: border-box;
}
.pe-help { font-size: 12px; color: var(--text-secondary, #888); margin-top: 3px; }
.pe-envhint { font-size: 12px; color: var(--text-secondary, #888); margin-top: 2px; font-style: italic; }
.pe-note { font-size: 13px; color: var(--text-secondary, #666); padding: 6px 0; }
.pe-unknown { color: var(--error-text, #c33); margin-bottom: 8px; font-size: 13px; }

.pe-step-menu {
  position: absolute; z-index: 100; background: #fff; border: 1px solid var(--border);
  border-radius: 6px; box-shadow: 0 4px 12px rgba(0,0,0,.12); padding: 4px; min-width: 140px;
}
.pe-step-menu-item {
  display: block; width: 100%; text-align: left; background: none; border: none;
  padding: 6px 10px; cursor: pointer; font-size: 13px; border-radius: 4px;
}
.pe-step-menu-item:hover { background: var(--bg-hover, #f0f5ff); }

.config-row { display: flex; gap: 8px; align-items: flex-start; }
.config-row textarea { flex: 1; }
.btn-sm { font-size: 12px; padding: 3px 8px; }
```

- [ ] **Step 2: Commit**

```bash
git add docupipe_manager/static/css/docupipe.css
git commit -m "style(editor): pipeline config editor dialog styles"
```

---

### Task 9: 边界场景端到端验证

**Files:**
- 无新文件；验证清单覆盖 spec 第 4 节所有边界。

**前提:** 本地 Postgres 可用 + `.env` 配置正确（或开发环境已能跑 task_form）。若环境不具备，至少用临时测试页覆盖不依赖服务端的部分。

- [ ] **Step 1: 启动服务，登录，进入"新建任务"页**

Run:
```bash
uv run uvicorn docupipe_manager.main:app --port 8002
```
用 chrome-devtools 导航 `http://localhost:8002/docupipe/projects`，进入任一项目，点"新建任务"。

- [ ] **Step 2: 验证空 pipeline 起步**

config.yaml textarea 保持默认（`pipelines:\n  - name: default`），点"图形编辑"。截图。Expected: dialog 打开，节点链显示三个"未设置"占位卡片（来源/目的地），无步骤，无报错。

- [ ] **Step 3: 验证完整编辑流程**

选 source=钉钉知识库，填 space="测试"，加两个步骤（convert、image_description），选 destination=Hindsight，填 bank_id="b1"，点确定。查看 textarea。截图。Expected: YAML 含 source.dingtalk.space、steps 两项、destination.hindsight.bank_id，mode 默认 full 不输出。

- [ ] **Step 4: 验证未知节点不崩溃**

把 textarea 手改为含未知 step：
```yaml
pipelines:
  - name: default
    source:
      localdrive:
        input_dir: /tmp
    destination:
      localdrive:
        output_dir: /out
    steps:
      - my_custom_plugin:
          foo: bar
```
点"图形编辑"，点"my_custom_plugin"卡片。截图。Expected: 卡片显示"my_custom_plugin（未知）"，参数区为只读 YAML 文本框显示 `foo: bar`。确定写回，验证 YAML 不丢 `my_custom_plugin` 段。

- [ ] **Step 5: 验证 YAML 解析失败降级**

把 textarea 手改为非法 YAML（如 `pipelines: [\n  - {`），点"图形编辑"。截图。Expected: dialog 顶部红字"YAML 解析失败：..."，确定按钮禁用，取消可关闭。

- [ ] **Step 6: 验证 _preserved 字段往返保留**

把 textarea 手改为：
```yaml
variables:
  script: return {"K":"v"}
converters:
  extensions:
    .docx: markitdown
pipelines:
  - name: default
    source:
      localdrive:
        input_dir: /tmp
    destination:
      hindsight: {}
    steps: []
```
点"图形编辑"，不改任何东西，点确定。查看 textarea。Expected: `variables` 和 `converters` 段原样保留，pipelines 不变。

- [ ] **Step 7: 验证多 pipeline 切换**

textarea 手改为含 2 条 pipeline，点"图形编辑"，下拉切换，截图。Expected: 切换后节点链反映对应 pipeline。新建/删除/重命名各试一次。

- [ ] **Step 8: 验证 post_steps/finalize_steps 折叠与添加**

textarea 手改为含 post_steps 一项，点"图形编辑"。截图。Expected: post_steps 段展开显示卡片；finalize_steps 段折叠为"+ 添加"按钮。

- [ ] **Step 9: 最终提交（无代码改动则跳过 commit）**

若验证中发现 bug，修复后 commit；若全部通过，无 commit。

---

## Self-Review 结果

**1. Spec coverage:** 逐条核对 spec 各节——
- 纯前端实现、jsyaml 本地托管、手写 schema：Task 1/2 ✓
- dialog 骨架、节点链（方案 A）、参数表单、source/destination 切换、step 增删拖拽、post/finalize 折叠、pipeline 选项：Task 4/5/6 ✓
- YAML 双向转换、_preserved、steps 归一化、清理空段：Task 3 ✓
- 并存集成、按钮联动、后端兜底不动：Task 7 ✓
- 样式（max-width 820px、节点链、参数表单）：Task 8 ✓
- 边界（空配置/解析失败/未知节点/preserved 往返/多 pipeline/post_steps 折叠）：Task 9 ✓
- 文件清单 6 个文件全有对应任务 ✓

**2. Placeholder scan:** 无 TBD/TODO/"实现 later"。Task 5/6 的 step 插入菜单在 Task 5 用占位 convert、Task 6 替换为真菜单——这是有意的增量实现，已在步骤中标注。`renderParams`/`renderFlow` 的占位在对应任务里明确替换。

**3. Type consistency:** `PipelineSchema.findByType(kind, type)` 在 Task 2 定义，Task 5/6 消费，签名一致。`PipelineEditor.open(textarea)` 在 Task 4 定义，Task 7 消费，一致。state.pipelines[i].source/destination 用 `{type, kwargs}` 结构，steps 用 `{name, kwargs}` 结构——Task 3 normalize 定义，Task 5/6 消费，一致。`selected = {segment, index}` 在 Task 5 定义，Task 6 消费，一致。

无遗漏。
