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
    dialog.addEventListener("click", function (e) { if (e.target === dialog) dialog.close(); });
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
    return DP.el("button", { type: "button", class: "pe-add-step", text: "+", onClick: function () {
      arr.splice(index, 0, { name: "convert", kwargs: {} });
      renderFlow();
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

  function renderPipelineOptions(p) {
    var wrap = DP.el("div", { class: "pe-pipeline-opts" },
      DP.el("div", { class: "pe-segment-label", text: "pipeline 选项" })
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

  function renderParams() {
    // Task 6 实现
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
