var pid = document.querySelector("[data-project-id]").dataset.projectId;
var currentUserId = document.querySelector("[data-current-user-id]").dataset.currentUserId;

function statusTagClass(status) {
  if (status === "succeeded" || status === "active" || status === "success") return "is-success";
  if (status === "failed" || status === "error") return "is-failed";
  if (status === "running" || status === "pending") return "is-running";
  return "";
}

function statusTag(text, status) {
  return DP.el("span", {class: "status-tag " + statusTagClass(status), text: text});
}

async function loadProject() {
  var r = await fetch(API_PREFIX + "/api/projects/" + pid);
  if (!r.ok) { location.href = "/docupipe/projects"; return; }
  var p = await r.json();
  document.getElementById("proj-name").textContent = p.name;
  var tag = document.getElementById("proj-status");
  tag.textContent = p.status;
  tag.className = "status-tag " + statusTagClass(p.status);
}

// ============ 任务 ============

async function loadTasks() {
  var r = await fetch(API_PREFIX + "/api/projects/" + pid + "/tasks");
  var tasks = await r.json();
  var box = document.getElementById("tab-tasks");
  var kids = [
    DP.el("div", {class: "members-header"},
      DP.el("h3", {text: "任务"}),
      DP.el("a", {class: "btn btn-sm btn-primary", href: "/docupipe/projects/" + pid + "/tasks/new", text: "新建任务"})
    )
  ];
  if (!tasks.length) {
    kids.push(DP.el("div", {class: "empty-state", text: "暂无任务。"}));
    DP.fill(box, kids);
    return;
  }
  var rows = tasks.map(function(t) {
    return DP.el("tr", {},
      DP.el("td", {},
        DP.el("span", {text: t.name}),
        " ",
        DP.el("span", {class: "card-row-meta-inline", text: t.schedule_mode})
      ),
      DP.el("td", {}, DP.el("code", {text: t.slug})),
      DP.el("td", {text: t.schedule_cron || "手动"}),
      DP.el("td", {}, t.last_run_status ? statusTag(t.last_run_status, t.last_run_status) : "—"),
      DP.el("td", {class: "action-cell"},
        DP.el("a", {class: "btn btn-sm btn-secondary", href: "/docupipe/projects/" + pid + "/tasks/" + t.id + "/edit", text: "编辑"}),
        DP.el("button", {class: "btn btn-sm btn-secondary trigger", dataset: {id: t.id}, text: "触发"})
      )
    );
  });
  kids.push(DP.el("table", {class: "data-table"},
    DP.el("thead", {}, DP.el("tr", {},
      ["名称","Slug","调度","上次状态","操作"].map(function(h){ return DP.el("th",{text:h}); })
    )),
    DP.el("tbody", {}, rows)
  ));
  DP.fill(box, kids);

  box.querySelectorAll(".trigger").forEach(function(b) {
    b.addEventListener("click", function() {
      fetch(API_PREFIX + "/api/projects/" + pid + "/tasks/" + b.dataset.id + "/trigger",
        {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"})
        .then(function(r) { return r.json(); })
        .then(function(data) { location.href = "/docupipe/runs/" + data.run_id; })
        .catch(function() { alert("触发失败"); });
    });
  });
}

// ============ 凭证 ============

function fmtExpiresSpan(s) {
  if (!s) return document.createTextNode("—");
  var dt = new Date(s);
  if (isNaN(dt)) return document.createTextNode(s);
  var now = new Date();
  var diff = dt - now;
  var abs = Math.abs(diff);
  var days = Math.floor(abs / 86400000);
  var hours = Math.floor((abs % 86400000) / 3600000);
  var rel = diff >= 0 ? "还剩 " + days + "天" + hours + "h" : "已过期 " + days + "天" + hours + "h";
  var cls = diff < 0 ? "is-failed" : (abs < 86400000 ? "is-running" : "");
  return DP.el("span", {class: "status-tag " + cls, text: dt.toLocaleString() + " · " + rel});
}

async function loadCredentials() {
  var r = await fetch(API_PREFIX + "/api/projects/" + pid + "/credentials");
  var creds = await r.json();
  var box = document.getElementById("tab-credentials");

  var kids = [
    DP.el("div", {class: "members-header"},
      DP.el("h3", {text: "凭证"}),
      DP.el("button", {class: "btn btn-sm btn-primary", id: "cred-add", text: "添加凭证"})
    ),
    DP.el("div", {id: "cred-dialog-mount"})
  ];

  if (!creds.length) {
    kids.push(DP.el("div", {class: "empty-state", text: "暂无凭证。"}));
    DP.fill(box, kids);
    document.getElementById("cred-add").addEventListener("click", function() { showCredentialDialog(); });
    return;
  }

  var rows = creds.map(function(c) {
    return DP.el("tr", {},
      DP.el("td", {text: c.name}),
      DP.el("td", {}, statusTag((c.credential_type || "dws").toUpperCase(), "")),
      DP.el("td", {}, c.corp_id ? DP.el("code", {text: c.corp_id}) : "—"),
      DP.el("td", {}, statusTag(c.status, c.status)),
      DP.el("td", {}, fmtExpiresSpan(c.token_expires_at)),
      DP.el("td", {class: "text-muted"}, fmtExpiresSpan(c.refresh_token_expires_at)),
      DP.el("td", {class: "action-cell"},
        DP.el("button", {class: "btn btn-sm btn-secondary edit-cred", dataset: {id: c.id, name: c.name}, text: "编辑"}),
        DP.el("button", {class: "btn btn-sm btn-secondary test-cred", dataset: {id: c.id}, text: "测试"}),
        DP.el("button", {class: "btn btn-sm btn-danger revoke-cred", dataset: {id: c.id}, text: "吊销"})
      )
    );
  });

  kids.push(DP.el("table", {class: "data-table"},
    DP.el("thead", {}, DP.el("tr", {},
      ["名称","类型","CorpId","状态","Access 过期","Refresh 过期","操作"].map(function(h){ return DP.el("th",{text:h}); })
    )),
    DP.el("tbody", {}, rows)
  ));
  DP.fill(box, kids);

  document.getElementById("cred-add").addEventListener("click", function() { showCredentialDialog(); });
  box.querySelectorAll(".revoke-cred").forEach(function(b) {
    b.addEventListener("click", function() {
      if (!confirm("确认吊销此凭证？")) return;
      fetch(API_PREFIX + "/api/projects/" + pid + "/credentials/" + b.dataset.id, {method: "DELETE"})
        .then(function(rr) { if (rr.ok) loadCredentials(); else alert("吊销失败"); });
    });
  });
  box.querySelectorAll(".test-cred").forEach(function(b) {
    b.addEventListener("click", function(e) {
      var btn = e.currentTarget;
      var old = btn.textContent; btn.textContent = "测试中..."; btn.disabled = true;
      fetch(API_PREFIX + "/api/projects/" + pid + "/credentials/" + b.dataset.id + "/test", {method: "POST"})
        .then(function(tr) {
          if (!tr.ok) { alert("请求失败"); btn.textContent = old; btn.disabled = false; loadCredentials(); return; }
          return tr.json();
        })
        .then(function(data) {
          btn.textContent = old; btn.disabled = false;
          if (data) showTestResult(data);
          loadCredentials();
        });
    });
  });
  box.querySelectorAll(".edit-cred").forEach(function(b) {
    b.addEventListener("click", function() { showRenameDialog(b.dataset.id, b.dataset.name); });
  });
}

function showCredentialDialog() {
  var dialog = document.getElementById("cred-dialog");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "cred-dialog";
    document.body.appendChild(dialog);
  }
  DP.fill(dialog,
    DP.el("h3", {style: "margin:0 0 16px", text: "添加凭证"}),
    DP.el("div", {class: "form-group"},
      DP.el("label", {text: "凭证类型"}),
      DP.el("select", {id: "cred-type", class: "form-control"},
        DP.el("option", {value: "dws", text: "DWS（钉钉）"})
      )
    ),
    DP.el("div", {class: "form-group"},
      DP.el("label", {text: "创建方式"}),
      DP.el("div", {class: "check-row"},
        DP.el("label", {},
          DP.el("input", {type: "radio", name: "cred-mode", value: "import", checked: "checked"}),
          " 导入已有凭证"
        ),
        DP.el("label", {},
          DP.el("input", {type: "radio", name: "cred-mode", value: "device"}),
          " 设备码登录"
        )
      )
    ),
    DP.el("div", {class: "form-group"},
      DP.el("label", {text: "凭证名称"}),
      DP.el("input", {id: "cred-name", class: "form-control", placeholder: "凭证名称"})
    ),
    DP.el("div", {id: "cred-import-area"},
      DP.el("div", {class: "form-group"},
        DP.el("label", {text: "粘贴 base64（dws auth export --base64 输出）"}),
        DP.el("textarea", {id: "cred-blob", class: "form-control", rows: "5", placeholder: "粘贴 base64 文本"})
      ),
      DP.el("div", {class: "form-group"},
        DP.el("label", {text: "或上传文件"}),
        DP.el("input", {type: "file", id: "cred-file", class: "form-control"})
      )
    ),
    DP.el("div", {id: "cred-device-area", class: "hidden"}),
    DP.el("div", {class: "form-actions", style: "margin-top:16px"},
      DP.el("button", {class: "btn btn-sm btn-primary", id: "cred-save", text: "保存"}),
      DP.el("button", {class: "btn btn-sm btn-secondary", id: "cred-cancel", text: "取消"})
    )
  );
  dialog.showModal();

  var importArea = dialog.querySelector("#cred-import-area");
  var deviceArea = dialog.querySelector("#cred-device-area");
  var saveBtn = dialog.querySelector("#cred-save");

  dialog.querySelectorAll('input[name="cred-mode"]').forEach(function(r) {
    r.addEventListener("change", function() {
      var mode = dialog.querySelector('input[name="cred-mode"]:checked').value;
      importArea.classList.toggle("hidden", mode !== "import");
      deviceArea.classList.toggle("hidden", mode !== "device");
      saveBtn.style.display = mode === "import" ? "" : "none";
      if (mode === "device") startDeviceFlow(deviceArea, dialog);
    });
  });

  dialog.querySelector("#cred-file").addEventListener("change", function(e) {
    var f = e.target.files[0];
    if (!f) return;
    var reader = new FileReader();
    reader.onload = function() { dialog.querySelector("#cred-blob").value = reader.result; };
    reader.readAsText(f);
  });

  dialog.querySelector("#cred-cancel").addEventListener("click", function() { dialog.close(); });
  dialog.addEventListener("click", function(e) { if (e.target === dialog) dialog.close(); });

  saveBtn.addEventListener("click", function() {
    var name = dialog.querySelector("#cred-name").value.trim();
    var auth_blob = dialog.querySelector("#cred-blob").value.trim();
    if (!name) { alert("请输入凭证名称"); return; }
    if (!auth_blob) { alert("请粘贴或上传凭证内容"); return; }
    saveBtn.disabled = true;
    fetch(API_PREFIX + "/api/projects/" + pid + "/credentials/import", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name: name, auth_blob: auth_blob}),
    }).then(function(rr) {
      if (rr.ok) { dialog.close(); loadCredentials(); }
      else { rr.json().then(function(j) { alert(j.detail || "导入失败"); }); saveBtn.disabled = false; }
    });
  });
}

function startDeviceFlow(area, dialog) {
  var sessionKey = null;
  DP.fill(area, DP.el("p", {class: "card-row-meta", text: "启动设备登录..."}));
  fetch(API_PREFIX + "/api/projects/" + pid + "/credentials/device-login/start?name=" + encodeURIComponent(dialog.querySelector("#cred-name").value || "dws-cred"), {method: "POST"})
    .then(function(r) { return r.ok ? r.json() : Promise.reject(); })
    .then(function(data) {
      sessionKey = data.session_key;
      var urlLink = DP.el("a", {href: data.verification_url, target: "_blank", text: data.verification_url});
      DP.fill(area,
        DP.el("p", {}, "请在浏览器打开：", urlLink),
        DP.el("p", {}, "验证码：", DP.el("span", {class: "device-code", text: data.user_code})),
        DP.el("p", {class: "device-hint", text: "有效期 " + (data.expires_in || 300) + " 秒"}),
        DP.el("div", {class: "form-actions"},
          DP.el("button", {class: "btn btn-sm btn-primary", id: "df-poll", text: "已完成，验证"})
        )
      );
      area.querySelector("#df-poll").addEventListener("click", function() {
        DP.fill(area, DP.el("p", {class: "card-row-meta", text: "验证中..."}));
        fetch(API_PREFIX + "/api/projects/" + pid + "/credentials/device-login/poll?session_key=" + sessionKey)
          .then(function(pr) {
            if (!pr.ok) { DP.fill(area, DP.el("p", {class: "status-tag is-failed", text: "验证失败或已过期"})); return; }
            return pr.json();
          })
          .then(function(pd) {
            if (!pd) return;
            if (pd.status === "success" || pd.status === "authorized") {
              fetch(API_PREFIX + "/api/projects/" + pid + "/credentials/device-login/finalize", {
                method: "POST", headers: {"Content-Type": "application/json"},
                body: JSON.stringify({session_key: sessionKey, name: dialog.querySelector("#cred-name").value || "dws-cred"}),
              }).then(function(fr) {
                if (fr.ok) { dialog.close(); loadCredentials(); }
                else { DP.fill(area, DP.el("p", {class: "status-tag is-failed", text: "最终验证失败"})); }
              });
            } else {
              DP.fill(area, DP.el("p", {class: "status-tag is-running", text: "尚未授权，请在钉钉扫码确认"}));
            }
          });
      });
    })
    .catch(function() { DP.fill(area, DP.el("p", {class: "status-tag is-failed", text: "启动设备登录失败"})); });
}

// ============ 成员 ============

function displayName(user) {
  return user.display_name || user.username || user.user_id;
}

function userSubtitle(user) {
  var parts = [];
  if (user.username) parts.push("@" + user.username);
  if (user.email) parts.push(user.email);
  return parts.join(" · ");
}

var _membersCache = [];

async function loadMembers() {
  var r = await fetch(API_PREFIX + "/api/projects/" + pid);
  var project = await r.json();
  var isOwner = project.is_owner;

  var mr = await fetch(API_PREFIX + "/api/projects/" + pid + "/members");
  var data = await mr.json();
  var box = document.getElementById("tab-members");
  _membersCache = data.members || [];

  var kids = [
    DP.el("div", {class: "members-header"},
      DP.el("h3", {text: "成员"}),
      isOwner ? DP.el("button", {class: "btn btn-sm btn-primary", id: "member-add-btn", text: "+ 添加成员"}) : null
    )
  ];

  if (!_membersCache.length) {
    kids.push(DP.el("div", {class: "empty-state", text: "暂无成员。"}));
    DP.fill(box, kids);
    if (isOwner) document.getElementById("member-add-btn").addEventListener("click", showMemberAddDialog);
    return;
  }

  var ownerCount = _membersCache.filter(function(m) { return m.role === "owner"; }).length;
  var rows = _membersCache.map(function(m) {
    var isMemberOwner = m.role === "owner";
    var isSelf = m.user_id === currentUserId;
    var selfLastOwner = isSelf && isMemberOwner && ownerCount <= 1;
    var badge = isMemberOwner
      ? DP.el("span", {class: "role-badge role-owner", text: "owner"})
      : DP.el("span", {class: "role-badge", text: "member"});

    var roleCell = badge;
    var actionCell = null;
    if (isOwner) {
      var selectDisabled = selfLastOwner;
      roleCell = DP.el("select", {class: "role-select", dataset: {userId: m.user_id}});
      roleCell.appendChild(DP.el("option", {value: "member", text: "member"}));
      roleCell.appendChild(DP.el("option", {value: "owner", text: "owner"}));
      roleCell.value = m.role;
      if (selectDisabled) roleCell.disabled = true;
      if (selectDisabled) roleCell = [roleCell, DP.el("br"), DP.el("span", {class: "member-hint", text: "至少保留一位 owner"})];
      if (!isSelf) {
        actionCell = DP.el("button", {class: "btn btn-sm btn-danger remove-member", dataset: {id: m.user_id}, text: "移除"});
      }
    }

    return DP.el("tr", {},
      DP.el("td", {},
        DP.el("span", {class: "card-row-title", text: displayName(m)}),
        DP.el("br"),
        DP.el("span", {class: "card-row-meta", text: userSubtitle(m)})
      ),
      DP.el("td", {}, roleCell),
      DP.el("td", {class: "card-row-meta", text: m.created_at}),
      DP.el("td", {class: "action-cell"}, actionCell)
    );
  });

  kids.push(DP.el("table", {class: "data-table"},
    DP.el("thead", {}, DP.el("tr", {},
      ["名称","角色","加入时间","操作"].map(function(h){ return DP.el("th",{text:h}); })
    )),
    DP.el("tbody", {}, rows)
  ));
  DP.fill(box, kids);

  if (isOwner) document.getElementById("member-add-btn").addEventListener("click", showMemberAddDialog);

  box.querySelectorAll(".remove-member").forEach(function(b) {
    b.addEventListener("click", function() {
      if (!confirm("确认移除该成员？")) return;
      fetch(API_PREFIX + "/api/projects/" + pid + "/members/" + b.dataset.id, {method: "DELETE"})
        .then(function(r) { if (r.ok) loadMembers(); else alert("移除失败"); });
    });
  });
  box.querySelectorAll(".role-select").forEach(function(sel) {
    sel.addEventListener("change", function() {
      changeMemberRole(sel.dataset.userId, sel.value);
    });
  });
}

async function changeMemberRole(userId, newRole) {
  var isSelf = userId === currentUserId;
  var isSelfDowngrade = isSelf && newRole === "member";
  if (isSelfDowngrade) {
    if (!confirm("你将失去管理权限，确定？")) { loadMembers(); return; }
  }
  try {
    var resp = await fetch(API_PREFIX + "/api/projects/" + pid + "/members/" + userId, {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({role: newRole}),
    });
    if (!resp.ok) {
      var data = await resp.json().catch(function() { return {}; });
      alert(data.detail || "修改失败");
      loadMembers();
      return;
    }
    if (isSelfDowngrade) { window.location.reload(); return; }
    loadMembers();
  } catch (e) { alert("网络错误"); }
}

// ============ 添加成员弹窗 ============

function showMemberAddDialog() {
  var dialog = document.getElementById("member-add-dialog");
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
  var preview = document.getElementById("member-preview");
  preview.style.display = "none";
  DP.clear(preview);
  preview.className = "member-preview";
  document.getElementById("member-add-submit").disabled = true;
}

function onMemberLookupInput() {
  _resetMemberPreview();
}

async function lookupMember() {
  var username = document.getElementById("member-lookup-input").value.trim();
  if (!username) return;

  var preview = document.getElementById("member-preview");
  preview.style.display = "block";
  preview.className = "member-preview";
  DP.fill(preview, DP.el("span", {class: "status-tag", text: "查找中..."}));
  document.getElementById("member-add-submit").disabled = true;

  var resp = await fetch(API_PREFIX + "/api/users/search?q=" + encodeURIComponent(username));
  if (!resp.ok) {
    preview.className = "member-preview is-error";
    DP.fill(preview, DP.el("span", {class: "status-tag is-failed", text: "查找失败"}));
    return;
  }

  var users = await resp.json();
  var user = users.find(function(u) { return u.username === username; });
  if (!user) {
    preview.className = "member-preview is-error";
    DP.fill(preview, DP.el("span", {class: "status-tag is-failed", text: "用户不存在"}));
    return;
  }

  var isAlreadyMember = _membersCache.some(function(m) { return m.user_id === user.id; });
  if (isAlreadyMember) {
    preview.className = "member-preview is-error";
    DP.fill(preview,
      DP.el("div", {class: "member-preview-name"},
        DP.el("span", {text: displayName(user)}),
        " ",
        DP.el("span", {class: "member-preview-username", text: "@" + user.username})
      ),
      DP.el("span", {class: "status-tag is-failed", text: "已是成员"})
    );
    return;
  }

  preview.className = "member-preview";
  var previewKids = [
    DP.el("div", {class: "member-preview-name"},
      DP.el("span", {text: displayName(user)}),
      " ",
      DP.el("span", {class: "member-preview-username", text: "@" + user.username})
    )
  ];
  if (user.email) previewKids.push(DP.el("div", {class: "member-preview-email", text: user.email}));
  previewKids.push(DP.el("span", {class: "status-tag is-success", text: "可添加"}));
  DP.fill(preview, previewKids);
  document.getElementById("member-lookup-id").value = user.id;
  document.getElementById("member-add-submit").disabled = false;
}

async function confirmAddMember(event) {
  event.preventDefault();
  var userId = document.getElementById("member-lookup-id").value;
  if (!userId) return;

  var submitBtn = document.getElementById("member-add-submit");
  submitBtn.disabled = true;

  var preview = document.getElementById("member-preview");
  var resp = await fetch(API_PREFIX + "/api/projects/" + pid + "/members", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({user_id: userId}),
  });

  if (resp.ok) {
    hideMemberAddDialog();
  } else {
    var data = await resp.json().catch(function() { return {}; });
    preview.className = "member-preview is-error";
    DP.fill(preview, DP.el("span", {class: "status-tag is-failed", text: data.detail || "添加失败"}));
    submitBtn.disabled = false;
  }
}

// ============ 运行历史 ============

var _runsPage = {page: 1};

async function loadRuns() {
  var box = document.getElementById("tab-runs");
  var pp = _runsPage.page;
  var r = await fetch(API_PREFIX + "/api/runs?project_id=" + pid + "&page=" + pp + "&page_size=20");
  var header = DP.el("div", {class: "members-header"}, DP.el("h3", {text: "运行历史"}));
  if (!r.ok) { DP.fill(box, header, DP.el("div", {class: "empty-state", text: "加载失败"})); return; }
  var data = await r.json();

  if (!data.total) {
    DP.fill(box, header, DP.el("div", {class: "empty-state", text: "暂无运行记录。"}));
    _runsPage = {page: 1};
    return;
  }

  var rows = data.runs.map(function(run) {
    return DP.el("tr", {},
      DP.el("td", {text: run.task_name || run.task_id.slice(0,8)}),
      DP.el("td", {text: run.pipeline_name || "default"}),
      DP.el("td", {text: run.mode}),
      DP.el("td", {}, statusTag(run.status, run.status)),
      DP.el("td", {text: run.started_at ? new Date(run.started_at).toLocaleString() : "—"}),
      DP.el("td", {class: "action-cell"},
        DP.el("a", {class: "btn btn-sm btn-secondary", href: "/docupipe/runs/" + run.id, text: "详情"})
      )
    );
  });

  var totalPages = Math.ceil(data.total / data.page_size);
  var pager = [
    DP.el("span", {class: "card-row-meta-inline", text: "共 " + data.total + " 条记录"})
  ];
  if (pp > 1) pager.push(DP.el("button", {class: "btn btn-sm btn-secondary", id: "runs-prev", text: "上一页"}));
  pager.push(DP.el("span", {class: "card-row-meta-inline", text: "第 " + pp + "/" + totalPages + " 页"}));
  if (pp < totalPages) pager.push(DP.el("button", {class: "btn btn-sm btn-secondary", id: "runs-next", text: "下一页"}));

  DP.fill(box,
    DP.el("div", {class: "members-header"},
      DP.el("h3", {text: "运行历史"}),
      DP.el("span", {class: "card-row-meta", text: "共 " + data.total + " 条记录"})
    ),
    DP.el("table", {class: "data-table"},
      DP.el("thead", {}, DP.el("tr", {},
        ["任务","流水线","模式","状态","开始时间","操作"].map(function(h){ return DP.el("th",{text:h}); })
      )),
      DP.el("tbody", {}, rows)
    ),
    DP.el("div", {class: "form-actions", style: "margin-top:8px;align-items:center"}, pager)
  );

  box.querySelector("#runs-prev")?.addEventListener("click", function() { _runsPage.page--; loadRuns(); });
  box.querySelector("#runs-next")?.addEventListener("click", function() { _runsPage.page++; loadRuns(); });
}

// ============ 环境变量 ============

async function loadEnvVars() {
  var box = document.getElementById("tab-env-vars");
  DP.fill(box,
    DP.el("div", {class: "members-header"},
      DP.el("h3", {text: "环境变量"}),
      DP.el("button", {class: "btn btn-sm btn-primary", id: "env-add", text: "新增变量"})
    ),
    DP.el("div", {id: "env-list"})
  );
  document.getElementById("env-add").addEventListener("click", function() { showEnvEditor(null); });
  await refreshEnvList();
}

async function refreshEnvList() {
  var r = await fetch(API_PREFIX + "/api/projects/" + pid + "/env-vars");
  var vars = await r.json();
  var list = document.getElementById("env-list");
  if (!vars.length) {
    DP.fill(list, DP.el("div", {class: "empty-state", text: "暂无环境变量。"}));
    return;
  }
  var rows = vars.map(function(v) {
    var valCell = v.is_secret
      ? DP.el("span", {class: "card-row-meta-inline", text: "•••••• 🔒"})
      : DP.el("code", {text: v.value || ""});
    var typeTag = v.is_secret
      ? DP.el("span", {class: "status-tag", text: "密钥"})
      : DP.el("span", {class: "card-row-meta-inline", text: "普通"});
    return DP.el("tr", {},
      DP.el("td", {}, DP.el("code", {text: v.key})),
      DP.el("td", {}, valCell),
      DP.el("td", {}, typeTag),
      DP.el("td", {text: v.description || "—"}),
      DP.el("td", {class: "action-cell"},
        DP.el("button", {class: "btn btn-sm btn-secondary env-edit", dataset: {id: v.id}, text: "编辑"}),
        DP.el("button", {class: "btn btn-sm btn-danger env-del", dataset: {id: v.id}, text: "删除"})
      )
    );
  });

  DP.fill(list, DP.el("table", {class: "data-table"},
    DP.el("thead", {}, DP.el("tr", {},
      ["变量名","值","类型","说明","操作"].map(function(h){ return DP.el("th",{text:h}); })
    )),
    DP.el("tbody", {}, rows)
  ));

  list.querySelectorAll(".env-edit").forEach(function(b) {
    b.addEventListener("click", function() { showEnvEditor(b.dataset.id); });
  });
  list.querySelectorAll(".env-del").forEach(function(b) {
    b.addEventListener("click", function() {
      if (!confirm("确认删除该环境变量？")) return;
      fetch(API_PREFIX + "/api/projects/" + pid + "/env-vars/" + b.dataset.id, {method: "DELETE"})
        .then(function(dr) { if (dr.ok) refreshEnvList(); else alert("删除失败"); });
    });
  });
}

async function showEnvEditor(varId) {
  var dialog = document.getElementById("env-dialog");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "env-dialog";
    document.body.appendChild(dialog);
  }
  var v = null;
  if (varId) {
    var r = await fetch(API_PREFIX + "/api/projects/" + pid + "/env-vars");
    var all = await r.json();
    v = all.find(function(x) { return x.id === varId; });
  }
  var isEdit = !!v;

  var kids = [
    DP.el("h3", {style: "margin:0 0 16px", text: isEdit ? "编辑环境变量" : "新增环境变量"}),
    DP.el("div", {class: "form-group"},
      DP.el("label", {text: "变量名"}),
      DP.el("input", {id: "env-key", class: "form-control", placeholder: "如 MY_VAR", pattern: "^[A-Za-z_][A-Za-z0-9_]*$"})
    ),
    DP.el("div", {class: "form-group"},
      DP.el("label", {text: "值"}),
      DP.el("input", {id: "env-value", class: "form-control", placeholder: "值"})
    ),
    DP.el("div", {class: "check-row"},
      DP.el("input", {type: "checkbox", id: "env-secret"}),
      DP.el("label", {for: "env-secret", text: "密钥（加密存储）"})
    ),
    DP.el("div", {class: "form-group"},
      DP.el("label", {text: "说明（可选）"}),
      DP.el("input", {id: "env-desc", class: "form-control"})
    ),
    DP.el("div", {class: "form-actions", style: "margin-top:16px"},
      DP.el("button", {class: "btn btn-sm btn-primary", id: "env-save", text: "保存"}),
      DP.el("button", {class: "btn btn-sm btn-secondary", id: "env-cancel", text: "取消"})
    )
  ];
  DP.fill(dialog, kids);

  var keyInput = document.getElementById("env-key");
  var valueInput = document.getElementById("env-value");
  var secretCheck = document.getElementById("env-secret");
  var descInput = document.getElementById("env-desc");

  if (isEdit) {
    keyInput.value = v.key;
    if (!v.is_secret) valueInput.value = v.value || "";
    else valueInput.placeholder = "留空表示不修改";
    secretCheck.checked = !!v.is_secret;
    if (isEdit) secretCheck.disabled = true;
    if (v.description) descInput.value = v.description;
  }

  dialog.showModal();
  document.getElementById("env-cancel").addEventListener("click", function() { dialog.close(); });
  dialog.addEventListener("click", function(e) { if (e.target === dialog) dialog.close(); });

  document.getElementById("env-save").addEventListener("click", function() {
    var body = {
      key: document.getElementById("env-key").value.trim(),
      value: document.getElementById("env-value").value,
      is_secret: document.getElementById("env-secret").checked,
      description: document.getElementById("env-desc").value.trim() || null,
    };
    if (!body.key) { alert("变量名不能为空"); return; }
    if (!isEdit && !body.value && !body.is_secret) { alert("值不能为空"); return; }
    var r;
    if (isEdit) {
      var upd = {description: body.description};
      if (keyInput.value.trim() !== v.key) upd.key = body.key;
      var valField = valueInput.value;
      if (!v.is_secret) { upd.value = valField; }
      else if (valField) { upd.value = valField; }
      r = fetch(API_PREFIX + "/api/projects/" + pid + "/env-vars/" + varId, {
        method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(upd),
      });
    } else {
      r = fetch(API_PREFIX + "/api/projects/" + pid + "/env-vars", {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
      });
    }
    r.then(function(resp) {
      if (resp.ok) { dialog.close(); refreshEnvList(); }
      else { resp.json().then(function(j) { alert(j.detail || "保存失败"); }); }
    });
  });
}

// ============ 凭证测试结果 ============

function showTestResult(data) {
  var d = document.getElementById("test-result-dialog");
  if (!d) { d = document.createElement("dialog"); d.id = "test-result-dialog"; document.body.appendChild(d); }
  var statusCls = data.status === "active" ? "is-success" : "is-failed";
  var kids = [
    DP.el("h3", {style: "margin:0 0 12px", text: "凭证测试结果"}),
    DP.el("table", {class: "data-table"},
      DP.el("tr", {}, DP.el("td", {text: "状态"}), DP.el("td", {}, statusTag(data.status || "?", data.status))),
      DP.el("tr", {}, DP.el("td", {text: "CorpId"}), DP.el("td", {}, DP.el("code", {text: data.corp_id || "—"}))),
      DP.el("tr", {}, DP.el("td", {text: "Access 过期"}), DP.el("td", {}, fmtExpiresSpan(data.token_expires_at))),
      DP.el("tr", {}, DP.el("td", {text: "Refresh 过期"}), DP.el("td", {class: "text-muted"}, fmtExpiresSpan(data.refresh_token_expires_at)))
    )
  ];
  if (data.error) {
    kids.push(DP.el("p", {class: "is-failed status-tag", style: "margin-top:8px", text: data.error}));
  }
  kids.push(DP.el("div", {class: "form-actions", style: "margin-top:12px"},
    DP.el("button", {class: "btn btn-sm btn-primary", id: "test-result-close", text: "关闭"})
  ));
  DP.fill(d, kids);
  d.showModal();
  d.querySelector("#test-result-close").onclick = function() { d.close(); };
  d.onclick = function(e) { if (e.target === d) d.close(); };
}

// ============ 凭证改名 ============

function showRenameDialog(id, oldName) {
  var d = document.getElementById("rename-dialog");
  if (!d) { d = document.createElement("dialog"); d.id = "rename-dialog"; document.body.appendChild(d); }
  DP.fill(d,
    DP.el("h3", {style: "margin:0 0 16px", text: "凭证改名"}),
    DP.el("div", {class: "form-group"},
      DP.el("label", {text: "新名称"}),
      DP.el("input", {id: "rename-input", class: "form-control"})
    ),
    DP.el("div", {class: "form-actions", style: "margin-top:16px"},
      DP.el("button", {class: "btn btn-sm btn-primary", id: "rename-save", text: "保存"}),
      DP.el("button", {class: "btn btn-sm btn-secondary", id: "rename-cancel", text: "取消"})
    )
  );
  document.getElementById("rename-input").value = oldName;
  d.showModal();
  document.getElementById("rename-input").focus();
  document.getElementById("rename-cancel").onclick = function() { d.close(); };
  d.onclick = function(e) { if (e.target === d) d.close(); };
  document.getElementById("rename-save").onclick = function() {
    var name = document.getElementById("rename-input").value.trim();
    if (!name) { alert("名称不能为空"); return; }
    fetch(API_PREFIX + "/api/projects/" + pid + "/credentials/" + id, {
      method: "PUT", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name: name}),
    }).then(function(r) {
      if (r.ok) { d.close(); loadCredentials(); }
      else { r.json().then(function(j) { alert(j.detail || "改名失败"); }); }
    });
  };
}

loadProject(); loadTasks(); loadEnvVars(); loadCredentials(); loadMembers(); loadRuns();
