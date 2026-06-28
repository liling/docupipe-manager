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

  var state = {
    template: "daily",
    hour: 3, minute: 0,
    weekdays: [1],
    day: 1,
    n: 5,
    fMin: "*", fHour: "*", fDay: "*", fMonth: "*", fDow: "*",
  };
  var targetInput = null;
  var previewTimer = null;
  var dialog, templatesEl, fieldsEl, exprEl, descEl, nextEl, okBtn;

  function $(sel) { return dialog.querySelector(sel); }

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

    if (hmInt && isStar(d) && isStar(mo) && isStar(dow)) {
      state.template = "daily"; state.minute = +m; state.hour = +h; return;
    }
    if (hmInt && isStar(d) && isStar(mo) && dow === "1-5") {
      state.template = "weekday"; state.minute = +m; state.hour = +h; return;
    }
    if (hmInt && isStar(d) && isStar(mo) && /^[\d,]+$/.test(dow) && dow !== "1-5") {
      state.template = "weekly"; state.minute = +m; state.hour = +h;
      state.weekdays = dow.split(",").map(Number); return;
    }
    if (hmInt && isInt(d) && isStar(mo) && isStar(dow)) {
      state.template = "monthly"; state.minute = +m; state.hour = +h; state.day = +d; return;
    }
    if (isInt(m) && isStar(h) && isStar(d) && isStar(mo) && isStar(dow)) {
      state.template = "hourly"; state.minute = +m; return;
    }
    if (/^\*\/\d+$/.test(m) && isStar(h) && isStar(d) && isStar(mo) && isStar(dow)) {
      state.template = "every_n"; state.n = parseInt(m.slice(2), 10); return;
    }
    state.template = "custom";
  }

  function buildTemplates() {
    DP.clear(templatesEl);
    TEMPLATES.forEach(function (t) {
      var b = document.createElement("button");
      b.type = "button";
      b.dataset.tpl = t.id;
      b.className = "cron-tpl-btn" + (state.template === t.id ? " is-active" : "");
      b.textContent = t.label;
      b.addEventListener("click", function () {
        if (state.template === t.id) return;
        state.template = t.id;
        updateTemplateActive();
        renderFields();
        schedulePreview();
      });
      templatesEl.appendChild(b);
    });
  }

  function updateTemplateActive() {
    var btns = templatesEl.querySelectorAll(".cron-tpl-btn");
    for (var i = 0; i < btns.length; i++) {
      var on = btns[i].dataset.tpl === state.template;
      btns[i].classList.toggle("is-active", on);
    }
  }

  function renderFields() {
    DP.clear(fieldsEl);
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
          b.classList.toggle("is-active", state.weekdays.indexOf(idx) >= 0);
          schedulePreview();
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

  function schedulePreview() {
    var expr = buildCron();
    exprEl.textContent = expr;
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
    if (previewTimer) clearTimeout(previewTimer);
    previewTimer = setTimeout(function () { fetchNextRuns(expr); }, 300);
  }

  function fetchNextRuns(expr) {
    fetch(API_PREFIX + "/api/cron/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cron: expr }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        DP.clear(nextEl);
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
        DP.fill(nextEl, DP.el("li", {style: "color:var(--error-text)", text: "预览失败"}));
      });
  }

  function formatTime(iso) {
    try {
      var d = new Date(iso);
      return d.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai",
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", hour12: false });
    } catch (e) { return iso; }
  }

  function confirmDialog() {
    if (okBtn.disabled) return;
    if (targetInput) targetInput.value = buildCron();
    dialog.close();
  }

  function open(input) {
    targetInput = input;
    parseIntoState(input.value);
    updateTemplateActive();
    renderFields();
    schedulePreview();
    dialog.showModal();
  }

  function init() {
    dialog = document.getElementById("cron-editor-dialog");
    if (!dialog) return;
    templatesEl = $("#cron-templates");
    fieldsEl = $("#cron-fields");
    exprEl = $("#cron-expr");
    descEl = $("#cron-desc");
    nextEl = $("#cron-next");
    okBtn = $("#cron-ok");
    buildTemplates();

    $("#cron-cancel").addEventListener("click", function () { dialog.close(); });
    okBtn.addEventListener("click", confirmDialog);
    dialog.addEventListener("click", function (e) { if (e.target === dialog) dialog.close(); });

    window.CronEditor = { open: open };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
