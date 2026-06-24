const runId = document.querySelector("[data-run-id]").dataset.runId;

function statusTagClass(status) {
  if (status === "succeeded" || status === "active" || status === "success") return "is-success";
  if (status === "failed" || status === "error" || status === "cancelled") return "is-failed";
  if (status === "running" || status === "pending") return "is-running";
  return "";
}

const consoleEl = document.getElementById("console");
const autoscrollEl = document.getElementById("autoscroll");

function appendLine(text) {
  const div = document.createElement("span");
  div.className = "log-line";
  div.textContent = text;  // textContent 自动 HTML 转义
  consoleEl.appendChild(div);
  if (autoscrollEl.checked) {
    consoleEl.scrollTop = consoleEl.scrollHeight;
  }
}

function renderMeta(m) {
  document.getElementById("run-task-name").textContent = m.task_name || "运行";
  const tag = document.getElementById("run-status");
  tag.textContent = m.status || "";
  tag.className = "status-tag " + statusTagClass(m.status);
  document.getElementById("run-command").textContent = m.command_text || "—";
  document.getElementById("run-exit-code").textContent =
    m.exit_code === null || m.exit_code === undefined ? "—" : m.exit_code;
  document.getElementById("run-started-at").textContent = m.started_at || "—";
  document.getElementById("run-completed-at").textContent = m.completed_at || "—";
  const dl = document.getElementById("run-download");
  dl.href = `/api/runs/${runId}/download-log`;
  const cancelBtn = document.getElementById("run-cancel");
  if (m.project_id) {
    document.getElementById("run-back").href = `/docupipe/projects/${m.project_id}#runs`;
  }
  if (m.status === "running" || m.status === "pending") {
    cancelBtn.classList.remove("hidden");
    cancelBtn.onclick = async () => {
      if (!confirm("确认取消此运行？")) return;
      const r = await fetch(`/api/runs/${runId}/cancel`, {method: "POST"});
      if (!r.ok) alert("取消失败");
    };
  }
}

function finalize(end) {
  const tag = document.getElementById("run-status");
  tag.textContent = end.status || "";
  tag.className = "status-tag " + statusTagClass(end.status);
  document.getElementById("run-exit-code").textContent =
    end.exit_code === null || end.exit_code === undefined ? "—" : end.exit_code;
  document.getElementById("run-command").textContent = end.command_text || "—";
  document.getElementById("run-started-at").textContent = end.started_at || "—";
  document.getElementById("run-completed-at").textContent = end.completed_at || "—";
  document.getElementById("run-cancel").classList.add("hidden");
}

const es = new EventSource(`/api/runs/${runId}/stream`);
es.addEventListener("meta", e => renderMeta(JSON.parse(e.data)));
es.addEventListener("log", e => appendLine(JSON.parse(e.data)));
es.addEventListener("end", e => { finalize(JSON.parse(e.data)); es.close(); });
es.onerror = () => {
  const tag = document.getElementById("run-status");
  if (!tag.textContent) tag.textContent = "重连中…";
};
