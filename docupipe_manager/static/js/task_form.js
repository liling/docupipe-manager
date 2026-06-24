const root = document.querySelector("[data-project-id]");
const pid = root.dataset.projectId;
const tid = root.dataset.taskId;

(async function init() {
  const cr = await fetch(`${API_PREFIX}/api/projects/${pid}/credentials`);
  const creds = await cr.json();
  const sel = document.querySelector('[name="credential_id"]');
  creds.forEach(c => {
    const o = document.createElement("option");
    o.value = c.id; o.textContent = `${c.name} (${c.corp_id})`;
    sel.appendChild(o);
  });
  const cronInput = document.querySelector('[name="schedule_cron"]');
  const enabledCheck = document.querySelector('[name="schedule_enabled"]');

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

  cronInput.disabled = !enabledCheck.checked;
  enabledCheck.addEventListener("change", () => {
    cronInput.disabled = !enabledCheck.checked;
  });
})();

document.getElementById("task-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = Object.fromEntries(new FormData(f).entries());
  body.schedule_enabled = f.elements.schedule_enabled.checked;
  body.schedule_cron = body.schedule_enabled ? f.elements.schedule_cron.value : null;
  if (!body.credential_id) { delete body.credential_id; delete body.credential_type; }
  const url = tid ? `${API_PREFIX}/api/projects/${pid}/tasks/${tid}` : `${API_PREFIX}/api/projects/${pid}/tasks`;
  const method = tid ? "PUT" : "POST";
  const r = await fetch(url, {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  if (r.ok) location.href = `/docupipe/projects/${pid}`;
  else { const j = await r.json(); alert(typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail)); }
});
