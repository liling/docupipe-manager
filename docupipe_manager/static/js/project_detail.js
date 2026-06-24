const pid = document.querySelector("[data-project-id]").dataset.projectId;
const currentUserId = document.querySelector("[data-current-user-id]").dataset.currentUserId;

function statusTagClass(status) {
  if (status === "succeeded" || status === "active" || status === "success") return "is-success";
  if (status === "failed" || status === "error") return "is-failed";
  if (status === "running" || status === "pending") return "is-running";
  return "";
}

async function loadProject() {
  const r = await fetch(`${API_PREFIX}/api/projects/${pid}`);
  if (!r.ok) { location.href = "/docupipe/projects"; return; }
  const p = await r.json();
  document.getElementById("proj-name").textContent = p.name;
  const tag = document.getElementById("proj-status");
  tag.textContent = p.status;
  tag.className = "status-tag " + statusTagClass(p.status);
}

async function loadTasks() {
  const r = await fetch(`${API_PREFIX}/api/projects/${pid}/tasks`);
  const tasks = await r.json();
  const box = document.getElementById("tab-tasks");
  let html = `<div class="members-header"><h3>任务</h3><a class="btn btn-sm btn-primary" href="/docupipe/projects/${pid}/tasks/new">新建任务</a></div>`;
  if (!tasks.length) {
    box.innerHTML = html + '<div class="empty-state">暂无任务。</div>';
    return;
  }
  html += `<table class="data-table"><thead><tr><th>名称</th><th>Slug</th><th>调度</th><th>上次状态</th><th>操作</th></tr></thead><tbody>` +
    tasks.map(t => `
    <tr>
      <td>${t.name} <span class="card-row-meta-inline">${t.schedule_mode}</span></td>
      <td><code>${t.slug}</code></td>
      <td>${t.schedule_cron || "手动"}</td>
      <td>${t.last_run_status ? `<span class="status-tag ${statusTagClass(t.last_run_status)}">${t.last_run_status}</span>` : "—"}</td>
      <td class="action-cell">
        <a class="btn btn-sm btn-secondary" href="/docupipe/projects/${pid}/tasks/${t.id}/edit">编辑</a>
        <button class="btn btn-sm btn-secondary trigger" data-id="${t.id}">触发</button>
      </td>
    </tr>`).join("") + `</tbody></table>`;
  box.innerHTML = html;
  box.querySelectorAll(".trigger").forEach(b => b.addEventListener("click", async () => {
    const r = await fetch(`${API_PREFIX}/api/projects/${pid}/tasks/${b.dataset.id}/trigger`, {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
    if (r.ok) {
      const data = await r.json();
      location.href = `/docupipe/runs/${data.run_id}`;
    } else {
      alert("触发失败");
    }
  }));
}

async function loadCredentials() {
  const r = await fetch(`${API_PREFIX}/api/projects/${pid}/credentials`);
  const creds = await r.json();
  const box = document.getElementById("tab-credentials");

  let html = '<div class="members-header"><h3>凭证</h3><button class="btn btn-sm btn-primary" id="cred-add">添加凭证</button></div>';
  html += '<div id="cred-dialog-mount"></div>';

  if (!creds.length) {
    html += '<div class="empty-state">暂无凭证。</div>';
  } else {
    html += '<table class="data-table"><thead><tr><th>名称</th><th>类型</th><th>CorpId</th><th>状态</th><th>Access 过期</th><th>Refresh 过期</th><th>操作</th></tr></thead><tbody>';
    for (const c of creds) {
      html += `<tr>
        <td>${c.name}</td>
        <td><span class="status-tag">${(c.credential_type || "dws").toUpperCase()}</span></td>
        <td>${c.corp_id ? `<code>${c.corp_id}</code>` : "—"}</td>
        <td><span class="status-tag ${statusTagClass(c.status)}">${c.status}</span></td>
        <td>${fmtExpires(c.token_expires_at)}</td>
        <td class="text-muted">${fmtExpires(c.refresh_token_expires_at)}</td>
        <td class="action-cell">
          <button class="btn btn-sm btn-secondary edit-cred" data-id="${c.id}" data-name="${c.name}">编辑</button>
          <button class="btn btn-sm btn-secondary test-cred" data-id="${c.id}">测试</button>
          <button class="btn btn-sm btn-danger revoke-cred" data-id="${c.id}">吊销</button>
        </td>
      </tr>`;
    }
    html += '</tbody></table>';
  }
  box.innerHTML = html;

  document.getElementById("cred-add").addEventListener("click", () => showCredentialDialog());
  box.querySelectorAll(".revoke-cred").forEach(b => b.addEventListener("click", async () => {
    if (!confirm("确认吊销此凭证？")) return;
    const rr = await fetch(`${API_PREFIX}/api/projects/${pid}/credentials/${b.dataset.id}`, {method: "DELETE"});
    if (rr.ok) { loadCredentials(); } else { alert("吊销失败"); }
  }));
  box.querySelectorAll(".test-cred").forEach(b => b.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const old = btn.textContent; btn.textContent = "测试中..."; btn.disabled = true;
    const tr = await fetch(`${API_PREFIX}/api/projects/${pid}/credentials/${b.dataset.id}/test`, {method: "POST"});
    if (!tr.ok) { alert("请求失败"); btn.textContent = old; btn.disabled = false; loadCredentials(); return; }
    const data = await tr.json();
    btn.textContent = old; btn.disabled = false;
    showTestResult(data);
    loadCredentials();
  }));
  box.querySelectorAll(".edit-cred").forEach(b => b.addEventListener("click", () => showRenameDialog(b.dataset.id, b.dataset.name)));
}

function fmtExpires(s) {
  if (!s) return "—";
  const dt = new Date(s);
  if (isNaN(dt)) return s;
  const now = new Date();
  const diff = dt - now;
  const abs = Math.abs(diff);
  const days = Math.floor(abs / 86400000);
  const hours = Math.floor((abs % 86400000) / 3600000);
  const rel = diff >= 0 ? `还剩 ${days}天${hours}h` : `已过期 ${days}天${hours}h`;
  const cls = diff < 0 ? "is-failed" : (abs < 86400000 ? "is-running" : "");
  return `<span class="status-tag ${cls}">${dt.toLocaleString()} · ${rel}</span>`;
}

function showCredentialDialog() {
  let dialog = document.getElementById("cred-dialog");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "cred-dialog";
    document.body.appendChild(dialog);
  }
  dialog.innerHTML = `
    <h3 style="margin:0 0 16px">添加凭证</h3>
    <div class="form-group"><label>凭证类型</label>
      <select id="cred-type" class="form-control"><option value="dws">DWS（钉钉）</option></select></div>
    <div class="form-group"><label>创建方式</label>
      <div class="check-row">
        <label><input type="radio" name="cred-mode" value="import" checked> 导入已有凭证</label>
        <label><input type="radio" name="cred-mode" value="device"> 设备码登录</label>
      </div></div>
    <div class="form-group"><label>凭证名称</label>
      <input id="cred-name" class="form-control" placeholder="凭证名称"></div>
    <div id="cred-import-area">
      <div class="form-group"><label>粘贴 base64（dws auth export --base64 输出）</label>
        <textarea id="cred-blob" class="form-control" rows="5" placeholder="粘贴 base64 文本"></textarea></div>
      <div class="form-group"><label>或上传文件</label>
        <input type="file" id="cred-file" class="form-control"></div>
    </div>
    <div id="cred-device-area" class="hidden"></div>
    <div class="form-actions" style="margin-top:16px">
      <button class="btn btn-sm btn-primary" id="cred-save">保存</button>
      <button class="btn btn-sm btn-secondary" id="cred-cancel">取消</button>
    </div>`;
  dialog.showModal();

  const importArea = dialog.querySelector("#cred-import-area");
  const deviceArea = dialog.querySelector("#cred-device-area");
  const saveBtn = dialog.querySelector("#cred-save");

  dialog.querySelectorAll('input[name="cred-mode"]').forEach(r => r.addEventListener("change", () => {
    const mode = dialog.querySelector('input[name="cred-mode"]:checked').value;
    importArea.classList.toggle("hidden", mode !== "import");
    deviceArea.classList.toggle("hidden", mode !== "device");
    saveBtn.style.display = mode === "import" ? "" : "none";
    if (mode === "device") startDeviceFlow(deviceArea, dialog);
  }));

  dialog.querySelector("#cred-file").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => { dialog.querySelector("#cred-blob").value = reader.result; };
    reader.readAsText(f);
  });

  dialog.querySelector("#cred-cancel").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });

  saveBtn.addEventListener("click", async () => {
    const name = dialog.querySelector("#cred-name").value.trim();
    const auth_blob = dialog.querySelector("#cred-blob").value.trim();
    if (!name) { alert("请输入凭证名称"); return; }
    if (!auth_blob) { alert("请粘贴或上传凭证内容"); return; }
    saveBtn.disabled = true;
    const rr = await fetch(`${API_PREFIX}/api/projects/${pid}/credentials/import`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name, auth_blob}),
    });
    if (rr.ok) { dialog.close(); loadCredentials(); }
    else { const j = await rr.json(); alert(j.detail || "导入失败"); saveBtn.disabled = false; }
  });
}

function startDeviceFlow(area, dialog) {
  let sessionKey = null;
  area.innerHTML = '<p class="card-row-meta">启动设备登录...</p>';
  fetch(`${API_PREFIX}/api/projects/${pid}/credentials/device-login/start?name=${encodeURIComponent(dialog.querySelector("#cred-name").value || "dws-cred")}`, {method: "POST"})
    .then(r => r.ok ? r.json() : Promise.reject())
    .then(data => {
      sessionKey = data.session_key;
      area.innerHTML = `
        <p>请在浏览器打开：<a href="${data.verification_url}" target="_blank">${data.verification_url}</a></p>
        <p>验证码：<span class="device-code">${data.user_code}</span></p>
        <p class="device-hint">有效期 ${data.expires_in || 300} 秒</p>
        <div class="form-actions">
          <button class="btn btn-sm btn-primary" id="df-poll">已完成，验证</button>
        </div>`;
      area.querySelector("#df-poll").addEventListener("click", async () => {
        area.innerHTML = '<p class="card-row-meta">验证中...</p>';
        const pr = await fetch(`${API_PREFIX}/api/projects/${pid}/credentials/device-login/poll?session_key=${sessionKey}`);
        if (!pr.ok) { area.innerHTML = '<p class="status-tag is-failed">验证失败或已过期</p>'; return; }
        const pd = await pr.json();
        if (pd.status === "success" || pd.status === "authorized") {
          const fr = await fetch(`${API_PREFIX}/api/projects/${pid}/credentials/device-login/finalize`, {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({session_key: sessionKey, name: dialog.querySelector("#cred-name").value || "dws-cred"}),
          });
          if (fr.ok) { dialog.close(); loadCredentials(); }
          else { area.innerHTML = '<p class="status-tag is-failed">最终验证失败</p>'; }
        } else {
          area.innerHTML = '<p class="status-tag is-running">尚未授权，请在钉钉扫码确认</p>';
        }
      });
    })
    .catch(() => { area.innerHTML = '<p class="status-tag is-failed">启动设备登录失败</p>'; });
}

function displayName(user) {
  return user.display_name || user.username || user.user_id;
}

function userSubtitle(user) {
  const parts = [];
  if (user.username) parts.push(`@${user.username}`);
  if (user.email) parts.push(user.email);
  return parts.join(" · ");
}

let _membersCache = [];

async function loadMembers() {
  const r = await fetch(`${API_PREFIX}/api/projects/${pid}`);
  const project = await r.json();
  const isOwner = project.is_owner;

  const mr = await fetch(`${API_PREFIX}/api/projects/${pid}/members`);
  const data = await mr.json();
  const box = document.getElementById("tab-members");
  _membersCache = data.members || [];

  let html = '<div class="members-header">'
    + '<h3>成员</h3>'
    + (isOwner ? '<button class="btn btn-sm btn-primary" onclick="showMemberAddDialog()">+ 添加成员</button>' : '')
    + '</div>';

  if (!_membersCache.length) {
    html += '<div class="empty-state">暂无成员。</div>';
  } else {
    const ownerCount = _membersCache.filter(m => m.role === "owner").length;
    html += '<table class="data-table"><thead><tr><th>名称</th><th>角色</th><th>加入时间</th><th>操作</th></tr></thead><tbody>';
    for (const m of _membersCache) {
      const isMemberOwner = m.role === "owner";
      const isSelf = m.user_id === currentUserId;
      const selfLastOwner = isSelf && isMemberOwner && ownerCount <= 1;
      const badge = isMemberOwner
        ? '<span class="role-badge role-owner">owner</span>'
        : '<span class="role-badge">member</span>';

      let roleCell = badge;
      let actionCell = '';
      if (isOwner) {
        const selectDisabled = selfLastOwner ? 'disabled' : '';
        roleCell = `<select class="role-select" onchange="changeMemberRole('${m.user_id}',this.value)" ${selectDisabled}>
          <option value="member" ${m.role === 'member' ? 'selected' : ''}>member</option>
          <option value="owner" ${m.role === 'owner' ? 'selected' : ''}>owner</option>
        </select>`;
        if (selfLastOwner) roleCell += '<br><span class="member-hint">至少保留一位 owner</span>';
        if (!isSelf) {
          actionCell += `<button class="btn btn-sm btn-danger remove-member" data-id="${m.user_id}">移除</button>`;
        }
      }

      html += '<tr>'
        + `<td><span class="card-row-title">${displayName(m)}</span><br><span class="card-row-meta">${userSubtitle(m)}</span></td>`
        + `<td>${roleCell}</td>`
        + `<td class="card-row-meta">${m.created_at}</td>`
        + `<td class="action-cell">${actionCell}</td>`
        + '</tr>';
    }
    html += '</tbody></table>';
  }

  box.innerHTML = html;

  box.querySelectorAll(".remove-member").forEach(b => b.addEventListener("click", async () => {
    if (!confirm("确认移除该成员？")) return;
    const r = await fetch(`${API_PREFIX}/api/projects/${pid}/members/${b.dataset.id}`, {method: "DELETE"});
    if (r.ok) { loadMembers(); } else { alert("移除失败"); }
  }));
}

async function changeMemberRole(userId, newRole) {
  const isSelf = userId === currentUserId;
  const isSelfDowngrade = isSelf && newRole === 'member';

  if (isSelfDowngrade) {
    if (!confirm('你将失去管理权限，确定？')) {
      loadMembers();
      return;
    }
  }

  try {
    const resp = await fetch(`${API_PREFIX}/api/projects/${pid}/members/${userId}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({role: newRole}),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      alert(data.detail || '修改失败');
      loadMembers();
      return;
    }
    if (isSelfDowngrade) {
      window.location.reload();
      return;
    }
    loadMembers();
  } catch (e) {
    alert('网络错误');
  }
}

// ============ 添加成员弹窗 ============

function showMemberAddDialog() {
  const dialog = document.getElementById("member-add-dialog");
  document.getElementById("member-lookup-input").value = "";
  document.getElementById("member-role-select").value = "member";
  document.getElementById("member-lookup-id").value = "";
  _resetMemberPreview();
  dialog.showModal();
}

function hideMemberAddDialog() {
  document.getElementById("member-add-dialog").close();
  loadMembers();
}

function _resetMemberPreview() {
  const preview = document.getElementById("member-preview");
  preview.style.display = "none";
  preview.innerHTML = "";
  preview.className = "member-preview";
  document.getElementById("member-add-submit").disabled = true;
}

function onMemberLookupInput() {
  _resetMemberPreview();
}

async function lookupMember() {
  const username = document.getElementById("member-lookup-input").value.trim();
  if (!username) return;

  const preview = document.getElementById("member-preview");
  preview.style.display = "block";
  preview.className = "member-preview";
  preview.innerHTML = '<span class="status-tag">查找中...</span>';
  document.getElementById("member-add-submit").disabled = true;

  const resp = await fetch(`${API_PREFIX}/api/users/search?q=${encodeURIComponent(username)}`);
  if (!resp.ok) {
    preview.className = "member-preview is-error";
    preview.innerHTML = '<span class="status-tag is-failed">查找失败</span>';
    return;
  }

  const users = await resp.json();
  const user = users.find(u => u.username === username);
  if (!user) {
    preview.className = "member-preview is-error";
    preview.innerHTML = '<span class="status-tag is-failed">用户不存在</span>';
    return;
  }

  const isAlreadyMember = _membersCache.some(m => m.user_id === user.id);
  if (isAlreadyMember) {
    preview.className = "member-preview is-error";
    preview.innerHTML = `<div class="member-preview-name">${displayName(user)} <span class="member-preview-username">@${user.username}</span></div>`
      + '<span class="status-tag is-failed">已是成员</span>';
    return;
  }

  preview.className = "member-preview";
  preview.innerHTML = `<div class="member-preview-name">${displayName(user)} <span class="member-preview-username">@${user.username}</span></div>`
    + (user.email ? `<div class="member-preview-email">${user.email}</div>` : '')
    + '<span class="status-tag is-success">可添加</span>';
  document.getElementById("member-lookup-id").value = user.id;
  document.getElementById("member-add-submit").disabled = false;
}

async function confirmAddMember(event) {
  event.preventDefault();
  const userId = document.getElementById("member-lookup-id").value;
  if (!userId) return;

  const submitBtn = document.getElementById("member-add-submit");
  submitBtn.disabled = true;

  const preview = document.getElementById("member-preview");
  const resp = await fetch(`${API_PREFIX}/api/projects/${pid}/members`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({user_id: userId}),
  });

  if (resp.ok) {
    hideMemberAddDialog();
  } else {
    const data = await resp.json().catch(() => ({}));
    preview.className = "member-preview is-error";
    preview.innerHTML = `<span class="status-tag is-failed">${data.detail || "添加失败"}</span>`;
    submitBtn.disabled = false;
  }
}

let _runsPage = {page: 1};

async function loadRuns() {
  const box = document.getElementById("tab-runs");
  const pp = _runsPage.page;
  const r = await fetch(`${API_PREFIX}/api/runs?project_id=${pid}&page=${pp}&page_size=20`);
  const headerHtml = '<div class="members-header"><h3>运行历史</h3></div>';
  if (!r.ok) { box.innerHTML = headerHtml + '<div class="empty-state">加载失败</div>'; return; }
  const data = await r.json();

  if (!data.total) {
    box.innerHTML = headerHtml + '<div class="empty-state">暂无运行记录。</div>';
    _runsPage = {page: 1};
    return;
  }

  let html = '<div class="members-header"><h3>运行历史</h3><span class="card-row-meta">共 ' + data.total + ' 条记录</span></div>';
  html += '<table class="data-table"><thead><tr><th>任务</th><th>流水线</th><th>模式</th><th>状态</th><th>开始时间</th><th>操作</th></tr></thead><tbody>';
  for (const run of data.runs) {
    html += `<tr>
      <td>${run.task_name || run.task_id.slice(0,8)}</td>
      <td>${run.pipeline_name || "default"}</td>
      <td>${run.mode}</td>
      <td><span class="status-tag ${statusTagClass(run.status)}">${run.status}</span></td>
      <td>${run.started_at ? new Date(run.started_at).toLocaleString() : "—"}</td>
      <td class="action-cell"><a class="btn btn-sm btn-secondary" href="/docupipe/runs/${run.id}">详情</a></td>
    </tr>`;
  }
  html += '</tbody></table>';

  const totalPages = Math.ceil(data.total / data.page_size);
  html += '<div class="form-actions" style="margin-top:8px;align-items:center">';
  if (pp > 1) html += `<button class="btn btn-sm btn-secondary" id="runs-prev">上一页</button> `;
  html += `<span class="card-row-meta-inline">第 ${pp}/${totalPages} 页</span>`;
  if (pp < totalPages) html += ` <button class="btn btn-sm btn-secondary" id="runs-next">下一页</button>`;
  html += '</div>';

  box.innerHTML = html;

  box.querySelector("#runs-prev")?.addEventListener("click", () => { _runsPage.page--; loadRuns(); });
  box.querySelector("#runs-next")?.addEventListener("click", () => { _runsPage.page++; loadRuns(); });
}

async function loadEnvVars() {
  const box = document.getElementById("tab-env-vars");
  let html = '<div class="members-header"><h3>环境变量</h3><button class="btn btn-sm btn-primary" id="env-add">新增变量</button></div>';
  html += '<div id="env-list"></div>';
  box.innerHTML = html;
  document.getElementById("env-add").addEventListener("click", () => showEnvEditor(null));
  await refreshEnvList();
}

async function refreshEnvList() {
  const r = await fetch(`${API_PREFIX}/api/projects/${pid}/env-vars`);
  const vars = await r.json();
  const list = document.getElementById("env-list");
  if (!vars.length) {
    list.innerHTML = '<div class="empty-state">暂无环境变量。</div>';
    return;
  }
  let html = '<table class="data-table"><thead><tr><th>变量名</th><th>值</th><th>类型</th><th>说明</th><th>操作</th></tr></thead><tbody>';
  for (const v of vars) {
    const valCell = v.is_secret ? '<span class="card-row-meta-inline">•••••• 🔒</span>' : `<code>${v.value || ""}</code>`;
    const typeTag = v.is_secret ? '<span class="status-tag">密钥</span>' : '<span class="card-row-meta-inline">普通</span>';
    html += `<tr>
      <td><code>${v.key}</code></td>
      <td>${valCell}</td>
      <td>${typeTag}</td>
      <td>${v.description || "—"}</td>
      <td class="action-cell">
        <button class="btn btn-sm btn-secondary env-edit" data-id="${v.id}">编辑</button>
        <button class="btn btn-sm btn-danger env-del" data-id="${v.id}">删除</button>
      </td>
    </tr>`;
  }
  html += '</tbody></table>';
  list.innerHTML = html;
  list.querySelectorAll(".env-edit").forEach(b => b.addEventListener("click", () => showEnvEditor(b.dataset.id)));
  list.querySelectorAll(".env-del").forEach(b => b.addEventListener("click", async () => {
    if (!confirm("确认删除该环境变量？")) return;
    const dr = await fetch(`${API_PREFIX}/api/projects/${pid}/env-vars/${b.dataset.id}`, {method: "DELETE"});
    if (dr.ok) { refreshEnvList(); } else { alert("删除失败"); }
  }));
}

async function showEnvEditor(varId) {
  let dialog = document.getElementById("env-dialog");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "env-dialog";
    document.body.appendChild(dialog);
  }
  let v = null;
  if (varId) {
    const r = await fetch(`${API_PREFIX}/api/projects/${pid}/env-vars`);
    const all = await r.json();
    v = all.find(x => x.id === varId);
  }
  const isEdit = !!v;
  const valPlaceholder = (isEdit && v.is_secret) ? 'placeholder="留空表示不修改"' : 'placeholder="值"';
  const secretDisabled = isEdit ? 'disabled' : '';
  const secretChecked = (isEdit && v.is_secret) ? 'checked' : '';
  dialog.innerHTML = `
    <h3 style="margin:0 0 16px">${isEdit ? "编辑环境变量" : "新增环境变量"}</h3>
    <div class="form-group"><label>变量名</label>
      <input id="env-key" class="form-control" value="${isEdit ? v.key : ""}" placeholder="如 MY_VAR" pattern="^[A-Za-z_][A-Za-z0-9_]*$"></div>
    <div class="form-group"><label>值</label>
      <input id="env-value" class="form-control" value="${isEdit && !v.is_secret ? (v.value || "") : ""}" ${valPlaceholder}></div>
    <div class="check-row">
      <input type="checkbox" id="env-secret" ${secretChecked} ${secretDisabled}>
      <label for="env-secret">密钥（加密存储）</label>
    </div>
    <div class="form-group"><label>说明（可选）</label>
      <input id="env-desc" class="form-control" value="${isEdit && v.description ? v.description : ""}"></div>
    <div class="form-actions" style="margin-top:16px">
      <button class="btn btn-sm btn-primary" id="env-save">保存</button>
      <button class="btn btn-sm btn-secondary" id="env-cancel">取消</button>
    </div>`;
  dialog.showModal();
  document.getElementById("env-cancel").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
  document.getElementById("env-save").addEventListener("click", async () => {
    const body = {
      key: document.getElementById("env-key").value.trim(),
      value: document.getElementById("env-value").value,
      is_secret: document.getElementById("env-secret").checked,
      description: document.getElementById("env-desc").value.trim() || null,
    };
    if (!body.key) { alert("变量名不能为空"); return; }
    if (!isEdit && !body.value && !body.is_secret) { alert("值不能为空"); return; }
    let r;
    if (isEdit) {
      const upd = {description: body.description};
      if (document.getElementById("env-key").value.trim() !== v.key) upd.key = body.key;
      const valField = document.getElementById("env-value").value;
      if (!v.is_secret) {
        upd.value = valField;
      } else if (valField) {
        upd.value = valField;
      }
      r = await fetch(`${API_PREFIX}/api/projects/${pid}/env-vars/${varId}`, {
        method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(upd),
      });
    } else {
      r = await fetch(`${API_PREFIX}/api/projects/${pid}/env-vars`, {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
      });
    }
    if (r.ok) { dialog.close(); refreshEnvList(); }
    else { const j = await r.json(); alert(j.detail || "保存失败"); }
  });
}

function showTestResult(data) {
  let d = document.getElementById("test-result-dialog");
  if (!d) { d = document.createElement("dialog"); d.id = "test-result-dialog"; document.body.appendChild(d); }
  const statusCls = data.status === "active" ? "is-success" : "is-failed";
  const errorHtml = data.error ? `<p class="is-failed status-tag" style="margin-top:8px">${data.error}</p>` : "";
  d.innerHTML = `
    <h3 style="margin:0 0 12px">凭证测试结果</h3>
    <table class="data-table">
      <tr><td>状态</td><td><span class="status-tag ${statusCls}">${data.status || "?"}</span></td></tr>
      <tr><td>CorpId</td><td><code>${data.corp_id || "—"}</code></td></tr>
      <tr><td>Access 过期</td><td>${fmtExpires(data.token_expires_at)}</td></tr>
      <tr><td>Refresh 过期</td><td class="text-muted">${fmtExpires(data.refresh_token_expires_at)}</td></tr>
    </table>
    ${errorHtml}
    <div class="form-actions" style="margin-top:12px"><button class="btn btn-sm btn-primary" id="test-result-close">关闭</button></div>`;
  d.showModal();
  d.querySelector("#test-result-close").onclick = () => d.close();
  d.onclick = (e) => { if (e.target === d) d.close(); };
}

function showRenameDialog(id, oldName) {
  let d = document.getElementById("rename-dialog");
  if (!d) { d = document.createElement("dialog"); d.id = "rename-dialog"; document.body.appendChild(d); }
  d.innerHTML = `
    <h3 style="margin:0 0 16px">凭证改名</h3>
    <div class="form-group"><label>新名称</label>
      <input id="rename-input" class="form-control" value="${oldName}"></div>
    <div class="form-actions" style="margin-top:16px">
      <button class="btn btn-sm btn-primary" id="rename-save">保存</button>
      <button class="btn btn-sm btn-secondary" id="rename-cancel">取消</button>
    </div>`;
  d.showModal();
  d.querySelector("#rename-input").focus();
  d.querySelector("#rename-cancel").onclick = () => d.close();
  d.onclick = (e) => { if (e.target === d) d.close(); };
  d.querySelector("#rename-save").onclick = async () => {
    const name = d.querySelector("#rename-input").value.trim();
    if (!name) { alert("名称不能为空"); return; }
    const r = await fetch(`${API_PREFIX}/api/projects/${pid}/credentials/${id}`, {
      method: "PUT", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name}),
    });
    if (r.ok) { d.close(); loadCredentials(); }
    else { const j = await r.json(); alert(j.detail || "改名失败"); }
  };
}

loadProject(); loadTasks(); loadEnvVars(); loadCredentials(); loadMembers(); loadRuns();
