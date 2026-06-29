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
