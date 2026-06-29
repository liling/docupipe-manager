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

  function renderFlow() {
    DP.clear(flowEl);
    DP.clear(paramsEl);
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
