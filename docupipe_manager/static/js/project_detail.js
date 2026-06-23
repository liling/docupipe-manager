const pid = document.querySelector("[data-project-id]").dataset.projectId;

async function loadProject() {
  const r = await fetch(`/api/projects/${pid}`);
  if (!r.ok) { location.href = "/docupipe/projects"; return; }
  const p = await r.json();
  document.getElementById("proj-name").textContent = p.name;
  document.getElementById("proj-status").textContent = p.status;
}

async function loadTasks() {
  const r = await fetch(`/api/projects/${pid}/tasks`);
  const tasks = await r.json();
  const box = document.getElementById("tab-tasks");
  if (!tasks.length) { box.innerHTML = '<p class="text-gray-500">无任务。<a class="link" href="/docupipe/projects/'+pid+'/tasks/new">新建任务</a></p>'; return; }
  box.innerHTML = `<div class="mb-2"><a class="btn btn-sm btn-primary" href="/docupipe/projects/${pid}/tasks/new">新建任务</a></div>` +
    tasks.map(t => `
    <div class="card p-3 flex justify-between items-center">
      <div>
        <a class="font-semibold" href="/docupipe/projects/${pid}/tasks/${t.id}/edit">${t.name}</a>
        <span class="text-xs text-gray-500 ml-2">${t.schedule_cron || "手动"} · ${t.schedule_mode}</span>
      </div>
      <div class="flex gap-2 items-center">
        ${t.last_run_status ? `<span class="text-xs">${t.last_run_status}</span>` : ""}
        <button class="btn btn-sm trigger" data-id="${t.id}">触发</button>
      </div>
    </div>`).join("");
  box.querySelectorAll(".trigger").forEach(b => b.addEventListener("click", async () => {
    const r = await fetch(`/api/projects/${pid}/tasks/${b.dataset.id}/trigger`, {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
    alert(r.ok ? "已触发" : "触发失败");
  }));
}

async function loadCredentials() {
  const r = await fetch(`/api/projects/${pid}/credentials`);
  const creds = await r.json();
  const box = document.getElementById("tab-credentials");

  let html = '<div class="mb-2"><button class="btn btn-sm btn-primary" id="device-start">添加凭证（设备码）</button></div>';
  html += '<div id="device-flow" class="hidden card p-4 mb-4 space-y-2"></div>';

  if (!creds.length) {
    html += '<p class="text-gray-500">暂无凭证。</p>';
  } else {
    html += '<table class="data-table"><thead><tr><th>名称</th><th>CorpId</th><th>状态</th><th>过期时间</th><th>操作</th></tr></thead><tbody>';
    for (const c of creds) {
      html += `<tr>
        <td>${c.name}</td>
        <td><code>${c.corp_id || "—"}</code></td>
        <td>${c.status}</td>
        <td>${c.token_expires_at || "—"}</td>
        <td><button class="btn btn-sm btn-danger revoke-cred" data-id="${c.id}">吊销</button></td>
      </tr>`;
    }
    html += '</tbody></table>';
  }
  box.innerHTML = html;

  let sessionKey = null;

  document.getElementById("device-start").addEventListener("click", async () => {
    const name = prompt("请输入凭证名称：");
    if (!name) return;
    const flowBox = document.getElementById("device-flow");
    flowBox.classList.remove("hidden");
    flowBox.innerHTML = '<p class="text-gray-500">启动设备登录...</p>';
    try {
      const r = await fetch(`/api/projects/${pid}/credentials/device-login/start?name=${encodeURIComponent(name)}`);
      if (!r.ok) { flowBox.innerHTML = '<p class="text-red-500">启动失败</p>'; return; }
      const data = await r.json();
      sessionKey = data.session_key;
      flowBox.innerHTML = `
        <p>请在浏览器中打开以下链接并输入验证码：</p>
        <p><a href="${data.verification_url}" target="_blank" class="link">${data.verification_url}</a></p>
        <p class="font-bold text-lg">验证码：<code>${data.user_code}</code></p>
        <p class="text-xs text-gray-500">有效期 ${data.expires_in || 300} 秒</p>
        <div class="flex gap-2 mt-2">
          <button class="btn btn-sm btn-primary" id="device-poll">已完成，验证</button>
          <button class="btn btn-sm btn-secondary" id="device-cancel">取消</button>
        </div>
      `;
      document.getElementById("device-poll").addEventListener("click", async () => {
        flowBox.innerHTML = '<p class="text-gray-500">验证中...</p>';
        const pollR = await fetch(`/api/projects/${pid}/credentials/device-login/poll?session_key=${sessionKey}`);
        if (!pollR.ok) { flowBox.innerHTML = '<p class="text-red-500">验证失败或已过期，请重试</p>'; return; }
        const pollData = await pollR.json();
        if (pollData.status === "authorized") {
          const finalR = await fetch(`/api/projects/${pid}/credentials/device-login/finalize`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({session_key: sessionKey, name}),
          });
          if (finalR.ok) {
            flowBox.innerHTML = '<p class="text-green-600">✅ 凭证添加成功！</p>';
            loadCredentials();
          } else {
            flowBox.innerHTML = '<p class="text-red-500">最终验证失败</p>';
          }
        } else {
          flowBox.innerHTML = '<p class="text-orange-500">尚未授权，请在钉钉中扫码确认</p>';
        }
      });
      document.getElementById("device-cancel").addEventListener("click", () => {
        flowBox.classList.add("hidden");
      });
    } catch (e) {
      flowBox.innerHTML = '<p class="text-red-500">请求失败</p>';
    }
  });

  box.querySelectorAll(".revoke-cred").forEach(b => b.addEventListener("click", async () => {
    if (!confirm("确认吊销此凭证？")) return;
    const r = await fetch(`/api/projects/${pid}/credentials/${b.dataset.id}`, {method: "DELETE"});
    if (r.ok) { loadCredentials(); } else { alert("吊销失败"); }
  }));
}

async function loadMembers() {
  const r = await fetch(`/api/projects/${pid}`);
  const project = await r.json();
  const isOwner = project.is_owner;

  const mr = await fetch(`/api/projects/${pid}/members`);
  const data = await mr.json();
  const box = document.getElementById("tab-members");

  let html = `<div class="card p-3 mb-2">
    <span class="font-semibold">${data.owner.username || data.owner.user_id}</span>
    <span class="text-xs text-gray-500 ml-2">所有者</span>
  </div>`;

  if (data.members.length) {
    for (const m of data.members) {
      html += `<div class="card p-3 mb-2 flex justify-between items-center">
        <span>${m.username || m.user_id}</span>
        <div class="flex gap-2 items-center">
          <span class="text-xs text-gray-500">${m.created_at}</span>
          ${isOwner ? `<button class="btn btn-sm btn-danger remove-member" data-id="${m.user_id}">移除</button>` : ""}
        </div>
      </div>`;
    }
  } else {
    html += '<p class="text-gray-500">暂无成员</p>';
  }

  if (isOwner) {
    html += `<div class="mt-4 flex gap-2">
      <input id="member-user-id" placeholder="用户 ID" class="input">
      <button class="btn btn-sm btn-primary" id="add-member-btn">添加成员</button>
    </div>`;
  }

  box.innerHTML = html;

  if (isOwner) {
    document.getElementById("add-member-btn")?.addEventListener("click", async () => {
      const userId = document.getElementById("member-user-id").value.trim();
      if (!userId) return;
      const r = await fetch(`/api/projects/${pid}/members`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({user_id: userId}),
      });
      if (r.ok) { loadMembers(); } else { alert((await r.json()).detail || "添加失败"); }
    });

    box.querySelectorAll(".remove-member").forEach(b => b.addEventListener("click", async () => {
      if (!confirm("确认移除该成员？")) return;
      const r = await fetch(`/api/projects/${pid}/members/${b.dataset.id}`, {method: "DELETE"});
      if (r.ok) { loadMembers(); } else { alert("移除失败"); }
    }));
  }
}

async function loadRuns() {
  const box = document.getElementById("tab-runs");

  const tasksRes = await fetch(`/api/projects/${pid}/tasks`);
  const tasks = await tasksRes.json();
  if (!tasks.length) {
    box.innerHTML = '<p class="text-gray-500">暂无运行记录。</p>';
    return;
  }

  const allRuns = [];
  for (const t of tasks.slice(0, 10)) {
    const r = await fetch(`/api/runs?task_id=${t.id}&page_size=5`);
    if (!r.ok) continue;
    const data = await r.json();
    for (const run of data.runs) {
      allRuns.push({...run, task_name: t.name});
    }
  }

  if (!allRuns.length) {
    box.innerHTML = '<p class="text-gray-500">暂无运行记录。</p>';
    return;
  }

  allRuns.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));

  box.innerHTML = '<div class="space-y-2">' +
    allRuns.slice(0, 50).map(run => `
      <div class="card p-3 flex justify-between items-center">
        <div>
          <span class="font-semibold">${run.task_name}</span>
          <span class="text-xs text-gray-500 ml-2">${run.pipeline_name || "default"} · ${run.mode}</span>
        </div>
        <div class="flex gap-2 items-center">
          <span class="text-xs ${run.status === 'succeeded' ? 'text-green-600' : run.status === 'failed' ? 'text-red-600' : 'text-gray-400'}">${run.status}</span>
          <span class="text-xs text-gray-400">${run.started_at ? new Date(run.started_at).toLocaleString() : ""}</span>
        </div>
      </div>`).join("") +
    '</div>';
}

loadProject(); loadTasks(); loadCredentials(); loadMembers(); loadRuns();
