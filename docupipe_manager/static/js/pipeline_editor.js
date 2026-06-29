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

  var state = null;
  var targetInput = null;
  var dialog, pipelineSelect, flowEl, paramsEl, okBtn, errEl;
  var activeIdx = 0;

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
    dialog.addEventListener("cancel", function (e) { e.preventDefault(); });
  }

  var selected = null; // { segment, index }

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
        var card = nodeCard("step", s.name, "steps", i, {
          kindLabel: "步骤", onDelete: function () { p.steps.splice(i, 1); selected = null; renderFlow(); }
        });
        makeDraggable(card, p.steps, i);
        mainRow.appendChild(card);
        mainRow.appendChild(arrow());
      });
    }
    mainRow.appendChild(nodeCard("destination", p.destination.type, "destination", 0, { kindLabel: "目的地" }));

    if (p.post_steps.length) {
      mainRow.appendChild(arrow());
      mainRow.appendChild(addStepButton(p.post_steps, "post_steps", 0));
      p.post_steps.forEach(function (s, i) {
        var card = nodeCard("step", s.name, "post_steps", i, {
          kindLabel: "写入后", onDelete: function () { p.post_steps.splice(i, 1); selected = null; renderFlow(); }
        });
        makeDraggable(card, p.post_steps, i);
        mainRow.appendChild(card);
        mainRow.appendChild(arrow());
        mainRow.appendChild(addStepButton(p.post_steps, "post_steps", i + 1));
      });
    }

    var mainWrap = DP.el("div", { class: "pe-segment" },
      DP.el("div", { class: "pe-segment-label" }, "文档流向", helpIcon("文档从来源读取，依次经过处理步骤转换（如格式转换、图片描述），最终写入目的地。写入后步骤在文档写入目的地之后执行。")), mainRow,
      DP.el("div", { class: "pe-add-step-row" },
        DP.el("button", { type: "button", class: "btn btn-secondary btn-sm", text: "+ 添加处理步骤", onClick: function (e) {
          var btn = e.currentTarget;
          showStepMenu(btn, function (type) {
            p.steps.push({ name: type, kwargs: {} }); renderFlow();
          }, "steps");
        } }),
        DP.el("button", { type: "button", class: "btn btn-secondary btn-sm", text: "+ 添加写入后步骤", onClick: function (e) {
          var btn = e.currentTarget;
          showStepMenu(btn, function (type) {
            p.post_steps.push({ name: type, kwargs: {} }); renderFlow();
          }, "post_steps");
        } })
      )
    );
    flowEl.appendChild(mainWrap);

    ["finalize_steps"].forEach(function (seg) {
      var label = "finalize_steps（全部完成后）";
      if (p[seg].length) {
        flowEl.appendChild(renderStepsSegment(seg, label));
      } else {
        flowEl.appendChild(DP.el("div", { class: "pe-segment pe-collapsed" },
          DP.el("button", { type: "button", class: "btn btn-secondary btn-sm", text: "+ 添加 " + label, onClick: function (e) {
            var btn = e.currentTarget;
            showStepMenu(btn, function (type) {
              p[seg].push({ name: type, kwargs: {} }); renderFlow();
            }, seg);
          } })
        ));
      }
    });

    flowEl.appendChild(renderPipelineOptions(p));

    renderParams();
  }

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
    return DP.el("button", { type: "button", class: "pe-add-step", text: "+", onClick: function (e) {
      var btn = e.currentTarget;
      showStepMenu(btn, function (type) {
        arr.splice(index, 0, { name: type, kwargs: {} });
        renderFlow();
      }, segment);
    } });
  }

  function renderStepsSegment(segment, kindLabel) {
    var p = activePipeline(); if (!p) return null;
    var arr = p[segment];
    if (!arr.length && (segment === "post_steps" || segment === "finalize_steps")) {
      return null;
    }
    var wrap = DP.el("div", { class: "pe-segment" },
      DP.el("div", { class: "pe-segment-label", text: kindLabel })
    );
    var row = DP.el("div", { class: "pe-node-row" });
    row.appendChild(addStepButton(arr, segment, 0));
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
    wrap.appendChild(row);
    return wrap;
  }

  function renderPipelineOptions(p) {
    var wrap = DP.el("div", { class: "pe-pipeline-opts" },
      DP.el("div", { class: "pe-segment-label" }, "pipeline 选项", helpIcon("运行模式：full=处理所有文档，incremental=仅处理新增文档，mirror=镜像同步含删除。变更检测：mtime=按修改时间判断，hash=按内容哈希判断。仅 mirror 模式需指定变更检测。"))
    );
    var row = DP.el("div", { class: "pe-opts-row" });
    var modeWrap = DP.el("div", { class: "pe-opt" }, DP.el("label", { text: "运行模式" }));
    var modeSel = DP.el("select", {});
    ["full", "incremental", "mirror"].forEach(function (m) {
      modeSel.appendChild(DP.el("option", { value: m, text: m, selected: p.mode === m }));
    });
    modeSel.addEventListener("change", function () { p.mode = modeSel.value; });
    modeWrap.appendChild(modeSel);
    row.appendChild(modeWrap);
    var cdWrap = DP.el("div", { class: "pe-opt" }, DP.el("label", { text: "变更检测" }));
    var cdSel = DP.el("select", {});
    ["", "mtime", "hash"].forEach(function (c) {
      cdSel.appendChild(DP.el("option", { value: c, text: c || "（无）", selected: p.change_detection === c }));
    });
    cdSel.addEventListener("change", function () { p.change_detection = cdSel.value; });
    cdWrap.appendChild(cdSel);
    row.appendChild(cdWrap);
    var sfWrap = DP.el("div", { class: "pe-opt" }, DP.el("label", { text: "state_file" }));
    var sfIn = DP.el("input", { value: p.state_file || "", placeholder: "（可选）" });
    sfIn.addEventListener("input", function () { p.state_file = sfIn.value; });
    sfWrap.appendChild(sfIn);
    row.appendChild(sfWrap);
    wrap.appendChild(row);
    return wrap;
  }

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

    if (kind === "source" || kind === "destination") {
      paramsEl.appendChild(typeSelector(kind, holder.type, function (newType) {
        holder.type = newType; holder.kwargs = {}; renderFlow();
      }));
    }

    var def = PipelineSchema.findByType(kind, holder.type);
    if (!def) {
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

  function stepTypeMenu(onPick, segment) {
    var menu = DP.el("div", { class: "pe-step-menu" });
    var suitable = PipelineSchema.steps.filter(function (s) {
      var isFinalize = s.stage === "finalize";
      return segment === "finalize_steps" ? isFinalize : !isFinalize;
    });
    suitable.forEach(function (d) {
      menu.appendChild(DP.el("button", { type: "button", class: "pe-step-menu-item", text: d.label, onClick: function () {
        onPick(d.type); openMenu = null; dialog.removeChild(menu);
      } }));
    });
    menu.style.position = "absolute";
    return menu;
  }

  var openMenu = null;
  function showStepMenu(anchor, onPick, segment) {
    if (openMenu) { dialog.removeChild(openMenu); openMenu = null; }
    var menu = stepTypeMenu(onPick, segment);
    var r = anchor.getBoundingClientRect();
    var dr = dialog.getBoundingClientRect();
    menu.style.position = "absolute";
    menu.style.left = (r.left - dr.left) + "px";
    menu.style.top = (r.bottom - dr.top + 4) + "px";
    dialog.appendChild(menu);
    openMenu = menu;
    setTimeout(function () {
      document.addEventListener("click", function close() {
        if (openMenu) { dialog.removeChild(openMenu); openMenu = null; }
        document.removeEventListener("click", close);
      }, { once: true });
    }, 0);
  }

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

  var openHelp = null;
  function showHelp(anchor, text) {
    if (openHelp) { dialog.removeChild(openHelp); openHelp = null; }
    var bubble = DP.el("div", { class: "pe-help-bubble", text: text });
    var r = anchor.getBoundingClientRect();
    var dr = dialog.getBoundingClientRect();
    bubble.style.position = "absolute";
    bubble.style.left = (r.left - dr.left) + "px";
    bubble.style.top = (r.bottom - dr.top + 4) + "px";
    dialog.appendChild(bubble);
    openHelp = bubble;
    setTimeout(function () {
      document.addEventListener("click", function close() {
        if (openHelp) { dialog.removeChild(openHelp); openHelp = null; }
        document.removeEventListener("click", close);
      }, { once: true });
    }, 0);
  }

  function helpIcon(text) {
    return DP.el("span", { class: "pe-help-icon", text: "?", onClick: function (e) {
      e.stopPropagation();
      showHelp(e.currentTarget, text);
    } });
  }

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
})();
