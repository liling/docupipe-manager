# 流水线配置编辑器设计

- **日期**: 2026-06-29
- **状态**: 已批准（待 spec 审查）
- **范围**: task 表单的 `config.yaml` 编辑体验改造

## 背景与动机

当前 task 表单（`templates/docupipe/task_form.html:20-23`）的 `config.yaml` 是一个纯 `<textarea>`，默认值仅 `pipelines:\n  - name: default`，零引导。用户必须手写 YAML，理解 docupipe 的 source/destination/step 概念、各节点类型与参数。难度高，一般人不知如何下手。

docupipe 的配置本质是"source → steps → destination"的线性文档流向，节点类型可枚举（sources: dingtalk/localdrive/tencent；destinations: hindsight/localdrive；steps: convert/image_description/resolve_attachments/tencent_delete/excel_structured/s3_upload）。适合用流程图 + 节点参数表单可视化呈现。

目标：增加一个图形编辑器，把文档流向与处理节点表示清楚，用户无需理解 YAML 语法和节点参数细节即可配置流水线，同时保留 textarea 兼容高级用户手写。

## 核心决策（不调用大模型）

- **纯前端实现**：节点 schema 是静态数据，放在前端 JS 自包含，不引入后端 API。manager 本来只通过 subprocess 调 docupipe（`runner_service.py:201`），不 import 它；编辑器所需中文 label/说明/枚举值无法靠 introspect 得到。后端校验已有独立的 `_validate_yaml`（`api/tasks.py:16`），复用此兜底即可。
- **流程图 + 节点表单（方案 A）**：HTML/CSS 节点卡片 + 箭头表达线性流向，点节点在下方内嵌展开参数表单。纯静态前端，无 SVG/Canvas，无 npm 构建。
- **单 pipeline 聚焦 + 切换**：v1 以一条 pipeline 的流向图为核心，多 pipeline 用顶部下拉切换；全局高级配置（`variables`/`converters`/`plugin_dirs`/组件默认值）不图形化，原样保留。
- **并存集成**：保留现有 textarea + 旁加"图形编辑"按钮，打开 dialog 编辑，确认后写回 textarea。与已落地的 cron editor 模式一致。

## 目标 / 非目标

**目标**
- 提供 pipeline 配置图形编辑 dialog，可视化呈现 source → steps → destination 的文档流向。
- 节点类型可选、参数可填，覆盖内置 sources/destinations/steps。
- 支持单 config 多 pipeline（下拉切换 + 新建/删除/重命名）。
- 保留 config 中 `pipelines` 以外的全局字段（variables/converters 等）原样不动。
- 保留现有 textarea 手写入口，兼容高级用户。
- 保留现有提交契约与后端校验链路，后端不动。

**非目标**
- 不图形化全局高级配置（variables 脚本、converters 扩展名映射、plugin_dirs、组件默认值）。
- 不引入前端构建步骤或 npm 依赖。
- 不改后端 API、数据模型、字段长度。
- 不从 docupipe 库动态 introspect 节点 schema（手写静态 schema）。

## 现状参考

- 数据模型：`models/task.py:34` `config_yaml: str`（Text，非空）。
- 后端校验：`api/tasks.py:16-28` `_validate_yaml` 要求 YAML 为 dict 且含 `pipelines` list。
- 提交契约：`static/js/task_form.js:46` `body = Object.fromEntries(new FormData(f))` → `config_yaml` 取自 textarea。
- 表单入口：`templates/docupipe/task_form.html:20-23` `<textarea name="config_yaml" rows="12">`。
- 前端安全约定：`static/js/dom.js` 提供 `DP.el`/`DP.fill`/`DP.clear`，无 innerHTML，防 XSS（历史 commit `31fbe2e` DOM API 重写）。
- dialog 惯用模式：cron editor（`static/js/cron_editor.js`）IIFE 暴露 `window.CronEditor.open(input)`，`task_form.js:38` 按钮联动。
- 全局变量：`API_PREFIX`（模板注入）。
- docupipe 配置结构（`runner.py`）：顶层可含组件默认值 + `variables` + `converters` + `plugin_dirs` + `pipelines`（必填 list）。每条 pipeline：`name`/`source`(单 key dict)/`destination`(单 key dict)/`steps`/`post_steps`/`finalize_steps`/`mode`/`change_detection`/`state_file`/`options.mirror_delete`。step 项可为字符串或单 key dict（`runner.py:48-53`）。

## 设计

### 1. 架构与依赖落点

**新增文件（全部前端）**
- `static/vendor/js-yaml/jsyaml.min.js` — YAML 解析/序列化（本地托管，约 40KB，UMD）。
- `static/js/pipeline_schema.js` — 节点 schema 静态定义（IIFE 暴露 `window.PipelineSchema`）。
- `static/js/pipeline_editor.js` — 编辑器逻辑（IIFE 暴露 `window.PipelineEditor.open(textarea)`）。

**改动文件**
- `templates/docupipe/task_form.html` — `config.yaml` 行加"图形编辑"按钮 + 底部内嵌 `<dialog id="pipeline-editor-dialog">` + 引入三个脚本（jsyaml → schema → editor 顺序）。
- `static/js/task_form.js` — 按钮联动 `PipelineEditor.open(textarea)`。
- `static/css/docupipe.css` — `.pipeline-dialog`、节点链、参数表单样式（复用现有 CSS 变量）。

**jsyaml 引入：本地托管**
- 下载到 `static/vendor/js-yaml/`（不依赖 CDN，符合内网/离线部署，与 cronstrue 同模式）。
- 加载顺序：jsyaml → pipeline_schema → pipeline_editor。

**节点 schema 来源：前端手写静态数据**
`pipeline_schema.js` 暴露 `window.PipelineSchema = { sources: [...], destinations: [...], steps: [...] }`，每条含 `type`/`label`/`params`（每个 param：`name`/`label`/`type`/`default`/`required`/`help`/`options`）。

schema 示例条目：
```js
{ kind: "source", type: "dingtalk", label: "钉钉知识库", params: [
  { name: "mode", label: "模式", type: "enum", options: ["wiki","doc"], default: "wiki", required: true },
  { name: "space", label: "知识库名", type: "str", help: "与 space_id 二选一" },
  { name: "space_id", label: "知识库 ID", type: "str", help: "与 space 二选一" },
  { name: "folders", label: "文件夹路径", type: "list", help: "可多行，如 产品规划/解决方案" },
  { name: "include_types", label: "仅含类型", type: "list" },
]}
```

覆盖清单（基于 docupipe 0.1.4 各节点 `__init__` 签名）：
- sources: dingtalk（mode/space/space_id/folder_id/folders/include_types）、localdrive（input_dir/include/exclude）、tencent（token/space_id/...）
- destinations: hindsight（bank_id/api_url/api_key/context_prefix/document_id_template/context_template/extra_tags/extra_metadata）、localdrive（output_dir/replace_extension/save_sidecar/path_template）
- steps: convert（无用户参数，由全局 converters 驱动）、image_description（api_key/base_url/model/concurrency）、resolve_attachments（无参数）、tencent_delete（remove_type）、excel_structured（fill_merged/skip_hidden/skip_empty）、s3_upload（endpoint_url/region/bucket/access_key/secret_key/prefix/url_prefix/roles）

docupipe 升级新增节点时手动同步 `pipeline_schema.js`——节点清单低频变化，可接受。JS 静态资源有缓存，更新 schema 时加版本查询参数或让用户硬刷新。

**职责划分**
- 节点 schema：前端手写静态数据。
- YAML ↔ 结构化双向转换：前端 jsyaml。
- 配置校验：前端结构校验（必填项、节点类型合法性）+ 后端现有 `_validate_yaml` 兜底不动。
- 全局高级配置：v1 不图形化，编辑器只操作 `pipelines` 数组，保留 config 其余字段原样。

### 2. 前端编辑器 UI

**触发落点：config.yaml 行改造**（`task_form.html:20-23`）

当前：`<label>config.yaml</label> <textarea ...>`
改为：
```
<label>config.yaml</label>
<div class="config-row">
  <textarea name="config_yaml" rows="12" ...>...</textarea>
  <button type="button" id="pipeline-edit-btn" class="btn btn-secondary">图形编辑</button>
</div>
```
- textarea 仍可手写（兼容高级用户 + 不破坏现有行为）。
- "图形编辑"按钮打开 dialog；dialog 确认后写回 textarea 值。

**dialog 落点：模板内嵌静态 HTML**（`task_form.html` 底部加 `<dialog id="pipeline-editor-dialog" class="pipeline-dialog">`）
- 宽度：`.pipeline-dialog { max-width: 820px }`（比 cron 的 560px 更大，因要横向放节点链）。

**dialog 内部布局**
```
┌─ 流水线配置编辑器 ────────────────────────────────────┐
│ pipeline: [default ▾]   [+新建]   [删除]   [重命名]     │
│                                                            │
│ ── 文档流向 ──────────────────────────────────────────── │
│  来源            步骤                          目的地      │
│ ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────────┐    │
│ │ 钉钉 │→│转换  │→│图片  │→│S3上传│→│ Hindsight │    │
│ │ 知识库│ │convert│ │描述  │ │      │ │          │    │
│ └──────┘  └──────┘  └──────┘  └──────┘  └──────────┘    │
│           [+ 在此处添加步骤] ×3（步骤间各一个）          │
│                                                            │
│ ── post_steps（写入后）── ── finalize_steps（全部完成后）│
│ [步骤链...]           [步骤链...]   [+ 添加]              │
│                                                            │
│ ── 节点参数：来源 · 钉钉知识库 ──────────────────────── │
│  模式: (•)wiki ( )doc                                      │
│  知识库名: [__________]  与 ID 二选一                      │
│  ...                                                       │
│                                                            │
│  pipeline 选项:                                            │
│   运行模式: [incremental ▾]   变更检测: [mtime ▾]         │
│   state_file: [____]                                        │
│                                                            │
│             [取消]  [确定]                                  │
└────────────────────────────────────────────────────────────┘
```

**节点链交互**
- 节点卡片 = 类型名 + 中文小标题。点击高亮选中 → 下方参数区渲染该节点表单。
- source/destination 各一个卡片，点卡片可切换类型（下拉选）。
- steps 段：卡片可拖拽排序（HTML5 drag），卡片右上角 ✕ 删除。每个步骤间隙有 `+` 按钮，点开 step 类型菜单插入。
- post_steps / finalize_steps 段同理，可折叠（默认收起，多数 pipeline 无此段）。
- 箭头用 CSS（`::after` 三角或简单 `→` 字符），纯视觉，不可点。

**参数表单**
- 按 schema 的 `type` 渲染控件：`str`→input、`enum`→radio/select、`bool`→checkbox、`list`→textarea（每行一项）、`int`→number。
- `help` 文本显示在控件下方。`required` 标星号。
- 环境变量回退的字段（如 hindsight 的 `bank_id`/`api_url`/`api_key`）标注"可留空，从环境变量读取"。

### 3. 数据模型与 YAML 双向转换

**编辑器内部状态**
```js
{
  _preserved: { /* config 里 pipelines 以外的所有顶层 key-value，编辑期间不动 */ },
  pipelines: [
    {
      name: "default",
      source: { dingtalk: { mode:"wiki", space:"...", folders:[...], ... } },
      destination: { hindsight: { bank_id:"...", ... } },
      steps: [ {convert:{}}, {image_description:{api_key:"..."}}, ... ],
      post_steps: [...],
      finalize_steps: [...],
      mode: "incremental",
      change_detection: "mtime",
      state_file: "",
      options: { mirror_delete: true }
    }
  ]
}
```

**打开 dialog 时：`parseFromYaml(yamlText) → state`**
1. `jsyaml.load(yamlText)` 解析整个 config。
2. 顶层非 `pipelines` 的 key 全部塞进 `_preserved`（`variables`/`converters`/`plugin_dirs`/组件默认值等），编辑期间不动。
3. `pipelines` 数组逐条规范化：缺失的段补默认空值（`steps:[]`、`post_steps:[]`、`mode:"full"`、`options:{}`）。
4. 解析失败 → dialog 顶部红字显示错误信息 + "确定"按钮禁用。用户可"取消"回 textarea 手改（降级路径，不阻塞）。

**确定时：`buildToYaml(state) → yamlText`**
1. 重建顶层 dict：`{..._preserved, pipelines: state.pipelines}`。
2. 清理空段：`post_steps:[]` → 不输出该 key；`options:{}` → 不输出；空字符串 `state_file` → 不输出。
3. `jsyaml.dump(obj, {lineWidth:-1, noRefs:true })` 序列化。
4. 写回 textarea，dialog 关闭。
5. 表单提交走既有 `task_form.js:46` 逻辑（读 textarea → `body.config_yaml`），后端 `_validate_yaml` 兜底校验，链路不变。

**steps 的两种形态归一化**
docupipe runner 支持 step 项为字符串或单 key dict（`runner.py:48-53`）。编辑器内部统一存 `{name: kwargs}`，序列化时：`kwargs` 为空对象 → 输出字符串形式 `"convert"`；非空 → 输出 `{convert: {...}}`。读入时两种都能解析。

### 4. 边界、降级与错误处理

**空配置 / 新建任务**
- 新建任务时 textarea 默认值是 `pipelines:\n  - name: default`（`task_form.html:21`）。打开编辑器 → 解析出一条空 pipeline（无 source/destination/steps）。节点链显示"未设置"占位卡片，引导用户先选 source 和 destination。
- config_yaml 为空或仅空白 → 同上，给一条空 pipeline 起步。

**YAML 解析失败**
- `jsyaml.load` 抛异常 → dialog 顶部红字显示错误信息 + "确定"按钮禁用。用户可"取消"回 textarea 手改。不阻塞主表单。

**未知节点类型（schema 未覆盖）**
- config 里出现 schema 没有的 source/destination/step 类型（自定义插件、docupipe 新增节点）→ 节点链仍显示该卡片（标注"未知类型"），点开参数区为只读 YAML 文本框（显示原始 kwargs），用户可编辑原始 YAML。
- 确定时原样写回，不丢数据。编辑器不因未知节点崩溃。

**全局组件默认值的处理**
- docupipe runner 会把顶层组件默认值与 pipeline 级配置 deep_merge（`runner.py:54-56`）。编辑器只编辑 pipeline 级显式配置，不展示合并后的最终值。
- 全局默认值通过 `_preserved` 原样保留，不受编辑影响。
- 参数区不标注"此处含全局默认"——避免增加复杂度。用户若用了全局默认，pipeline 级留空的字段就留空。

**post_steps / finalize_steps 缺失**
- 多数 pipeline 无此段。读入时缺失 → `[]`；序列化时空数组 → 不输出该 key。节点链区段可折叠，默认收起。

**写回机制**
- "确定"：`buildToYaml(state)` → 写入 `[name="config_yaml"]` textarea → `dialog.close()`。
- "取消"/点 backdrop/Esc：不写回，关闭。
- 打开时：读当前 textarea 值 → `parseFromYaml`。

## 数据流

```
打开 dialog
  → 读 textarea 现值 → jsyaml.load → 拆分 _preserved / pipelines → 规范化
  → 渲染 pipeline 下拉 + 节点链 + 选中节点参数表单
用户切 pipeline / 拖拽步骤 / 改节点类型 / 填参数
  → 实时更新 state
点确定
  → buildToYaml(state) → 写回 textarea → close dialog
表单提交（既有逻辑不变）
  → body.config_yaml = textarea.value
  → 后端 _validate_yaml 兜底
```

## 测试策略

纯前端实现，无后端改动。用 chrome-devtools 工具启动 dev server、操作编辑器、验证写回 YAML、截图为证。回归靠手动重跑关键路径。

**验证清单**
- YAML ↔ state 双向转换：多 pipeline、含 `_preserved` 字段、steps 字符串/dict 混用、空段。
- 节点链交互：拖拽排序、间隙加号插入、删除步骤、切换 source/destination 类型。
- 参数表单：各节点类型字段渲染正确、enum/bool/list/int 控件行为、required 校验。
- 未知节点：不崩溃、原始 YAML 可编辑、写回不丢。
- 解析失败：红字提示、确定禁用、取消可退。
- 新建任务空 pipeline 起步 → 选 source/destination → 加 steps → 确定写回 → 表单提交成功。
- 编辑现有任务 → 正确还原节点链与参数 → 改动 → 确定 → 保存成功。
- `_preserved` 字段（variables/converters 等）经编辑器往返后原样保留。

## 文件级落点清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `static/vendor/js-yaml/jsyaml.min.js` | 新增 | YAML 解析/序列化 UMD（约 40KB） |
| `static/js/pipeline_schema.js` | 新增 | 节点 schema 静态定义，暴露 `window.PipelineSchema` |
| `static/js/pipeline_editor.js` | 新增 | dialog 渲染、节点链、参数表单、YAML 双向转换、写回 |
| `templates/docupipe/task_form.html` | 改 | config.yaml 行加编辑按钮 + 底部内嵌 dialog + 引入脚本 |
| `static/js/task_form.js` | 改 | 编辑按钮打开 dialog、写回联动 |
| `static/css/docupipe.css` | 改 | `.pipeline-dialog`、节点链、参数表单样式 |

## 风险与未决

- **schema 与 docupipe 版本漂移**：手写 schema 需在 docupipe 升级新增节点时手动同步。节点清单低频变化，可接受；更新时加版本查询参数避免缓存。
- **全局组件默认值不可见**：编辑器只编辑 pipeline 级显式配置，用户若依赖全局默认值，参数区看不到合并后的最终值。v1 接受此限制，高级用户可切 textarea 查看。
- **jsyaml 序列化格式差异**：jsyaml.dump 的缩进/引号风格可能与用户手写不一致。功能等价，但 diff 噪声可能让用户困惑；可在 spec 审查时确认是否需保留原格式（v1 不做，序列化即可读即可）。
- **拖拽排序的可访问性**：HTML5 drag 无键盘替代。v1 接受鼠标依赖，后续可加上下移动按钮。
