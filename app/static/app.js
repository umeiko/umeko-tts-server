"use strict";
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const prefix = $('meta[name="umeko-base-path"]').content;
const configuredBase = $('meta[name="umeko-base-url"]').content;
const appUrl = (path) => prefix + path;
const state = {
  csrf: "",
  users: [],
  keys: [],
  voices: [],
  status: null,
  queue: { jobs: [], history: [] },
  settingsLoaded: false,
  loggedIn: false,
  refreshing: false,
  synthJob: null,
  audioUrl: null,
};
const states = {
  queued: "排队中",
  running: "合成中",
  cancelling: "正在取消",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  expired: "排队超时",
  interrupted: "重启中断",
  pending: "处理中",
};
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const fmt = (value) => Number(value || 0).toLocaleString("zh-CN");
const date = (seconds) =>
  seconds
    ? new Date(seconds * 1000).toLocaleString("zh-CN", { hour12: false })
    : "尚未使用";
const empty = (cols, text) =>
  `<tr><td colspan="${cols}" class="empty">${esc(text)}</td></tr>`;
const status = (value) =>
  `<span class="status ${value === "completed" ? "ok" : value === "running" ? "running" : value === "queued" ? "queued" : "bad"}">${esc(states[value] || value)}</span>`;
const action = (name, id, text, danger = false) =>
  `<button data-action="${name}" data-id="${esc(id)}"${danger ? ' class="danger"' : ""}>${esc(text)}</button>`;

function toast(message, error = false) {
  const el = document.createElement("div");
  el.className = "toast" + (error ? " error" : "");
  el.textContent = message;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), 4500);
}
function errorMessage(error) {
  toast(error.message || "操作失败，请稍后重试", true);
}
function closeDialog(id) {
  const dialog = document.getElementById(id);
  dialog.close();
  if (id === "secretDialog") $("#newSecret").value = "";
}
function signedOut() {
  state.loggedIn = false;
  state.csrf = "";
  state.settingsLoaded = false;
  state.users = [];
  state.keys = [];
  state.voices = [];
  state.synthJob = null;
  $("#appView").classList.add("hidden");
  $("#loginView").classList.remove("hidden");
  $$("dialog[open]").forEach((d) => d.close());
  $("#newSecret").value = "";
  if (state.audioUrl) URL.revokeObjectURL(state.audioUrl);
  state.audioUrl = null;
  $("#synthAudio").removeAttribute("src");
  $("#audioResult").classList.add("hidden");
}
async function api(path, { method = "GET", json, raw = false, body } = {}) {
  const headers = {};
  if (!["GET", "HEAD"].includes(method)) headers["X-CSRF-Token"] = state.csrf;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  }
  const res = await fetch(appUrl(path), {
    method,
    headers,
    body,
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) {
    let message = `请求失败 (${res.status})`;
    try {
      message = (await res.json()).message || message;
    } catch {
      /* Non-JSON proxy errors. */
    }
    if (res.status === 401 && state.loggedIn) signedOut();
    throw new Error(message);
  }
  return raw ? res : res.json();
}
function page(name) {
  $$(".page").forEach((p) =>
    p.classList.toggle("active", p.id === "page-" + name),
  );
  $$(".nav-item").forEach((b) =>
    b.classList.toggle("active", b.dataset.page === name),
  );
  $("#pageTitle").textContent = {
    overview: "服务总览",
    users: "用户与密钥",
    queue: "合成队列",
    voices: "音色与试听",
    docs: "接入文档",
  }[name];
}

function renderStatus() {
  const s = state.status,
    e = s.engine,
    q = e.queue,
    config = e.config;
  $("#metricRunning").textContent = q.running;
  $("#metricQueued").textContent = q.queued;
  $("#metricCapacity").textContent =
    `${config.concurrency} 个并行名额 / 后端上限 ${e.capacity}`;
  $("#metricUsers").textContent = state.users.filter((u) => u.enabled).length;
  $("#metricKeys").textContent =
    `${state.keys.filter((k) => !k.revoked && (!k.expires || k.expires > Date.now() / 1000) && state.users.find((u) => u.id === k.user_id)?.enabled).length} 个可用密钥`;
  $("#metricChars").textContent = fmt(
    state.users.reduce((sum, u) => sum + u.used_chars, 0),
  );
  $("#connectionBadge").textContent = e.backend.demo
    ? "测试模式"
    : "服务运行中";
  $("#sidebarBackend").textContent = e.backend.name;
  $("#infoBackend").textContent = e.backend.name;
  $("#infoDevice").textContent = e.backend.device || "测试后端";
  $("#infoVoice").textContent = s.default_voice || "尚未设置";
  $("#infoUptime").textContent =
    `${Math.floor(e.uptime_sec / 3600)} 小时 ${Math.floor((e.uptime_sec % 3600) / 60)} 分钟`;
  $("#infoPrefix").textContent = prefix || "/";
  $("#infoRetention").textContent = `${s.limits.result_ttl / 60} 分钟`;
  $("#demoBanner").classList.toggle("hidden", !e.backend.demo);
  $("#capacityHint").textContent =
    e.capacity === 1
      ? "当前后端共用一份模型管线，安全推理并行上限为 1。更多请求会进入队列。"
      : `当前后端支持最多 ${e.capacity} 个并行推理。`;
  $("#globalConcurrency").max = e.capacity;
  $("#synthText").maxLength = Math.min(4000, s.limits.max_text_chars);
  if (!state.settingsLoaded) {
    $("#globalConcurrency").value = config.concurrency;
    $("#globalQueue").value = config.max_queue;
    $("#queueTimeout").value = config.queue_timeout;
    $("#agingSeconds").value = config.aging_seconds;
    state.settingsLoaded = true;
  }
}
function historyRows(rows) {
  return (
    rows
      .map(
        (j) =>
          `<tr><td>${esc(j.user_name)}</td><td><code title="${esc(j.id)}">${j.id.slice(0, 10)}</code></td><td>${fmt(j.chars)}</td><td>${status(j.state)}</td><td>${esc(date(j.created))}</td></tr>`,
      )
      .join("") || empty(5, "还没有调用记录")
  );
}
function renderQueue() {
  $("#activeBody").innerHTML =
    state.queue.jobs
      .map(
        (j) =>
          `<tr><td>${esc(j.user_name)}</td><td>${esc(j.voice)}<small>${j.id.slice(0, 10)}</small></td><td>${j.priority}</td><td>${j.position || "—"}</td><td>${status(j.state)}</td><td>${action("cancel-job", j.id, "取消任务", true)}</td></tr>`,
      )
      .join("") || empty(6, "队列空闲，等待新的声音");
  $("#historyBody").innerHTML = historyRows(state.queue.history);
  $("#recentBody").innerHTML = historyRows(state.queue.history.slice(0, 6));
}
function renderUsers() {
  const query = $("#userSearch").value.trim().toLowerCase();
  const matchingKeys = state.keys.filter((k) =>
    [k.user_name, k.label, k.prefix].some((v) =>
      v.toLowerCase().includes(query),
    ),
  );
  const users = state.users.filter(
    (u) =>
      u.name.toLowerCase().includes(query) ||
      matchingKeys.some((k) => k.user_id === u.id),
  );
  $("#userCount").textContent = `${state.users.length} 位`;
  $("#userBody").innerHTML =
    users
      .map(
        (u) =>
          `<tr><td><strong>${esc(u.name)}</strong><small>${u.enabled ? "已启用" : "已停用"}</small></td><td><span class="tag">P${u.priority}</span></td><td>${u.max_concurrent} / ${u.max_queue}</td><td>${u.rpm} 次</td><td>${fmt(u.used_chars)} / ${u.daily_chars ? fmt(u.daily_chars) : "不限"}<small>预留 ${fmt(u.reserved_chars)} 字符</small></td><td><div class="table-actions">${action("edit-user", u.id, "编辑")}${action("issue-key", u.id, "发放密钥")}${action("toggle-user", u.id, u.enabled ? "停用" : "启用", !!u.enabled)}</div></td></tr>`,
      )
      .join("") || empty(6, "新增一位可信用户，再为其发放 API Key");
  $("#keyBody").innerHTML =
    matchingKeys
      .map((k) => {
        const active =
          !k.revoked && (!k.expires || k.expires > Date.now() / 1000);
        return `<tr><td>${esc(k.user_name)}<small>${esc(k.label)}</small></td><td><code>${esc(k.prefix)}…</code></td><td>${k.expires ? esc(date(k.expires)) : "不设到期日"}</td><td>${esc(date(k.last_used))}</td><td><span class="status ${active ? "ok" : "bad"}">${k.revoked ? "已撤销" : active ? "可用" : "已过期"}</span></td><td><div class="table-actions">${active ? action("rotate-key", k.id, "轮换") + action("revoke-key", k.id, "撤销", true) : "—"}</div></td></tr>`;
      })
      .join("") || empty(6, "还没有密钥，完整值仅在发放时展示一次");
}
function renderVoices() {
  const selected = $("#synthVoice").value;
  $("#synthVoice").innerHTML = state.voices
    .map(
      (v) =>
        `<option value="${esc(v.name)}">${esc(v.name)}${v.is_default ? "（默认）" : ""}</option>`,
    )
    .join("");
  if (state.voices.some((v) => v.name === selected))
    $("#synthVoice").value = selected;
  else
    $("#synthVoice").value =
      state.voices.find((v) => v.is_default)?.name ||
      state.voices[0]?.name ||
      "";
  $("#voiceList").innerHTML =
    state.voices
      .map(
        (v) =>
          `<div class="voice-card"><div class="section-head"><h3>${esc(v.name)}</h3><span class="tag">${v.is_default ? "默认音色" : v.is_complete ? "已就绪" : "待补齐文件"}</span></div><p>${esc(v.description || "暂无简介")}</p><div class="actions">${action("edit-voice", v.name, "编辑")}${v.has_ref_audio ? action("play-ref", v.name, "参考试听") : ""}${!v.is_default ? action("default-voice", v.name, "设为默认") : ""}${action("delete-voice", v.name, "删除", true)}</div></div>`,
      )
      .join("") || '<div class="empty">添加音色后即可在线合成</div>';
}
async function refresh(voices = false) {
  if (!state.loggedIn || state.refreshing) return;
  state.refreshing = true;
  try {
    const paths = ["/api/status", "/api/users", "/api/keys", "/api/queue"];
    if (voices || !state.status) paths.push("/api/voices");
    const results = await Promise.all(paths.map((p) => api(p)));
    if (!state.loggedIn) return;
    [state.status, state.users, state.keys, state.queue] = results;
    if (results[4]) {
      state.voices = results[4];
      renderVoices();
    }
    renderStatus();
    renderUsers();
    renderQueue();
  } catch (e) {
    $("#connectionBadge").textContent = "连接暂不可用";
    errorMessage(e);
  } finally {
    state.refreshing = false;
  }
}
async function enter(csrf) {
  state.loggedIn = true;
  state.csrf = csrf;
  state.status = null;
  $("#loginView").classList.add("hidden");
  $("#appView").classList.remove("hidden");
  await refresh(true);
}
function userPolicy(u) {
  return Object.fromEntries(
    [
      "name",
      "enabled",
      "priority",
      "max_concurrent",
      "max_queue",
      "rpm",
      "daily_chars",
      "max_text_chars",
    ].map((k) => [k, u[k]]),
  );
}
function editUser(id) {
  const u = state.users.find((u) => u.id === id) || {
    name: "",
    priority: 0,
    max_concurrent: 1,
    max_queue: 8,
    rpm: 30,
    daily_chars: 100000,
    max_text_chars: 1000,
    enabled: true,
  };
  $("#editUserId").value = id || "";
  $("#userDialogTitle").textContent = id ? "编辑可信用户" : "新增可信用户";
  const fields = {
    userName: "name",
    userPriority: "priority",
    userConcurrency: "max_concurrent",
    userQueue: "max_queue",
    userRpm: "rpm",
    userDaily: "daily_chars",
    userTextLimit: "max_text_chars",
  };
  Object.entries(fields).forEach(
    ([el, key]) => (document.getElementById(el).value = u[key]),
  );
  $("#userEnabled").checked = !!u.enabled;
  $("#userDialog").showModal();
}
function issueKey(id) {
  const users = state.users.filter((u) => u.enabled);
  if (!users.length) {
    toast("请先添加并启用一位用户", true);
    return;
  }
  $("#keyUser").innerHTML = users
    .map((u) => `<option value="${u.id}">${esc(u.name)}</option>`)
    .join("");
  if (id) $("#keyUser").value = id;
  $("#keyDialog").showModal();
}
function showSecret(result) {
  $("#newSecret").value = result.key;
  $("#secretDialog").showModal();
}
function editVoice(name) {
  const v = state.voices.find((v) => v.name === name);
  $("#voiceForm").reset();
  $("#editVoiceName").value = name || "";
  $("#voiceName").value = name || "";
  $("#voiceName").disabled = !!name;
  $("#voiceDialogTitle").textContent = name ? "编辑音色" : "添加音色";
  $("#voiceDescription").value = v?.description || "";
  $("#voiceRefText").value = v?.ref_text || "";
  $("#voiceRefLang").value = v?.ref_lang || "zh";
  $("#voiceProgress").classList.add("hidden");
  $("#voiceDialog").showModal();
}
async function playAudio(path) {
  const response = await api(path, { raw: true });
  const blob = await response.blob();
  if (state.audioUrl) URL.revokeObjectURL(state.audioUrl);
  state.audioUrl = URL.createObjectURL(blob);
  $("#synthAudio").src = state.audioUrl;
  $("#audioDownload").href = state.audioUrl;
  $("#audioResult").classList.remove("hidden");
  $("#synthAudio")
    .play()
    .catch(() => {});
}
async function synthesize(e) {
  e.preventDefault();
  $("#synthButton").disabled = true;
  try {
    const job = await api("/v1/jobs", {
      method: "POST",
      json: {
        text: $("#synthText").value,
        voice: $("#synthVoice").value,
        text_lang: $("#synthLang").value,
        speed_factor: Number($("#synthSpeed").value),
        max_sec: Number($("#synthSeconds").value),
      },
    });
    state.synthJob = job.id;
    $("#cancelSynth").classList.remove("hidden");
    while (state.synthJob === job.id && state.loggedIn) {
      const current = await api(`/v1/jobs/${job.id}`);
      $("#synthStatus").textContent =
        (states[current.state] || current.state) +
        (current.position ? ` · 队列位置 ${current.position}` : "");
      if (current.state === "completed") {
        await playAudio(`/v1/jobs/${job.id}/audio`);
        break;
      }
      if (["failed", "cancelled", "expired"].includes(current.state))
        throw new Error(current.error || states[current.state]);
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  } catch (error) {
    $("#synthStatus").textContent = error.message;
    errorMessage(error);
  } finally {
    state.synthJob = null;
    $("#synthButton").disabled = false;
    $("#cancelSynth").classList.add("hidden");
    refresh();
  }
}
function uploadVoice(path, method, form) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, appUrl(path));
    xhr.setRequestHeader("X-CSRF-Token", state.csrf);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable)
        $("#voiceProgress").value = (e.loaded / e.total) * 100;
    };
    xhr.onload = () => {
      let body;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = { message: `上传失败 (${xhr.status})` };
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new Error(body.message || "上传失败"));
    };
    xhr.onerror = () => reject(new Error("网络中断，上传未完成"));
    xhr.send(form);
  });
}
function fillDocs() {
  const base = configuredBase.startsWith("http")
    ? configuredBase
    : location.origin + prefix;
  $("#docBase").textContent = base;
  $("#docSync").textContent =
    `curl -X POST '${base}/tts' \\\n  -H "Authorization: Bearer $UMEKO_TTS_API_KEY" \\\n  -H 'Content-Type: application/json' \\\n  -d '{"text":"你好世界","max_sec":10}' -o output.wav`;
  $("#docAsync").textContent =
    `# 1. 提交任务，保存返回的 id\ncurl -X POST '${base}/v1/jobs' \\\n  -H "Authorization: Bearer $UMEKO_TTS_API_KEY" \\\n  -H 'Content-Type: application/json' \\\n  -d '{"text":"这是一段需要排队合成的文本。"}'\n\n# 2. 查询状态\ncurl '${base}/v1/jobs/<id>' \\\n  -H "Authorization: Bearer $UMEKO_TTS_API_KEY"\n\n# 3. completed 后下载\ncurl '${base}/v1/jobs/<id>/audio' \\\n  -H "Authorization: Bearer $UMEKO_TTS_API_KEY" -o output.wav`;
}
async function performAction(name, id) {
  if (name === "edit-user") return editUser(id);
  if (name === "issue-key") return issueKey(id);
  if (name === "edit-voice") return editVoice(id);
  if (name === "toggle-user") {
    const u = state.users.find((u) => u.id === id);
    if (u.enabled && !confirm(`停用“${u.name}”并取消其未完成任务？`)) return;
    await api(`/api/users/${id}`, {
      method: "PUT",
      json: { ...userPolicy(u), enabled: !u.enabled },
    });
  } else if (name === "revoke-key") {
    if (!confirm("撤销后该密钥将立即失效，并取消其未完成任务。继续？")) return;
    await api(`/api/keys/${id}`, { method: "DELETE" });
  } else if (name === "rotate-key") {
    if (!confirm("轮换会立即撤销旧密钥。请保存并重新发放新密钥。继续？"))
      return;
    showSecret(await api(`/api/keys/${id}/rotate`, { method: "POST" }));
  } else if (name === "cancel-job")
    await api(`/api/queue/${id}`, { method: "DELETE" });
  else if (name === "default-voice")
    await api(`/api/voices/${encodeURIComponent(id)}/default`, {
      method: "POST",
    });
  else if (name === "delete-voice") {
    if (!confirm(`删除音色“${id}”及其模型文件？`)) return;
    await api(`/api/voices/${encodeURIComponent(id)}`, { method: "DELETE" });
  } else if (name === "play-ref") {
    page("voices");
    return playAudio(`/api/voices/${encodeURIComponent(id)}/ref-audio`);
  }
  await refresh(name.includes("voice"));
}
document.addEventListener("click", (event) => {
  const target = event.target.closest("button");
  if (!target) return;
  if (target.dataset.page) page(target.dataset.page);
  if (target.dataset.jump) page(target.dataset.jump);
  if (target.dataset.close) closeDialog(target.dataset.close);
  if (target.dataset.action) {
    target.disabled = true;
    performAction(target.dataset.action, target.dataset.id)
      .catch(errorMessage)
      .finally(() => (target.disabled = false));
  }
});
$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#loginButton").disabled = true;
  $("#loginError").textContent = "";
  try {
    const result = await api("/api/auth/login", {
      method: "POST",
      json: { token: $("#adminToken").value },
    });
    $("#adminToken").value = "";
    await enter(result.csrf_token);
  } catch (error) {
    $("#loginError").textContent = error.message;
  } finally {
    $("#loginButton").disabled = false;
  }
});
["#logoutButton", "#mobileLogoutButton"].forEach((selector) =>
  $(selector).addEventListener("click", () =>
    api("/api/auth/logout", { method: "POST" })
      .then(signedOut)
      .catch(errorMessage),
  ),
);
$("#refreshButton").addEventListener("click", () => refresh(true));
$("#userSearch").addEventListener("input", renderUsers);
$("#newUserButton").addEventListener("click", () => editUser());
$("#issueKeyButton").addEventListener("click", () => issueKey());
$("#newVoiceButton").addEventListener("click", () => editVoice());
$("#synthForm").addEventListener("submit", synthesize);
$("#cancelSynth").addEventListener("click", () => {
  if (state.synthJob)
    api(`/v1/jobs/${state.synthJob}`, { method: "DELETE" }).catch(errorMessage);
});
$("#settingsForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/settings", {
      method: "PUT",
      json: {
        concurrency: Number($("#globalConcurrency").value),
        max_queue: Number($("#globalQueue").value),
        queue_timeout: Number($("#queueTimeout").value),
        aging_seconds: Number($("#agingSeconds").value),
      },
    });
    state.settingsLoaded = false;
    toast("调度设置已生效");
    await refresh();
  } catch (error) {
    errorMessage(error);
  }
});
$("#userForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = $("#editUserId").value;
  try {
    await api(id ? `/api/users/${id}` : "/api/users", {
      method: id ? "PUT" : "POST",
      json: {
        name: $("#userName").value,
        enabled: $("#userEnabled").checked,
        priority: Number($("#userPriority").value),
        max_concurrent: Number($("#userConcurrency").value),
        max_queue: Number($("#userQueue").value),
        rpm: Number($("#userRpm").value),
        daily_chars: Number($("#userDaily").value),
        max_text_chars: Number($("#userTextLimit").value),
      },
    });
    closeDialog("userDialog");
    toast("用户已保存");
    await refresh();
  } catch (error) {
    errorMessage(error);
  }
});
$("#keyForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const key = await api("/api/keys", {
      method: "POST",
      json: {
        user_id: $("#keyUser").value,
        label: $("#keyLabel").value,
        expires_days: Number($("#keyDays").value),
      },
    });
    closeDialog("keyDialog");
    showSecret(key);
    await refresh();
  } catch (error) {
    errorMessage(error);
  }
});
$("#copySecret").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("#newSecret").value);
    toast("密钥已复制");
  } catch {
    $("#newSecret").focus();
    $("#newSecret").select();
    toast("请复制已选中的密钥");
  }
});
$("#secretDialog").addEventListener(
  "close",
  () => ($("#newSecret").value = ""),
);
$("#voiceForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const edit = $("#editVoiceName").value;
  const form = new FormData();
  if (!edit) form.append("name", $("#voiceName").value);
  form.append("description", $("#voiceDescription").value);
  form.append("ref_text", $("#voiceRefText").value);
  form.append("ref_lang", $("#voiceRefLang").value);
  [
    ["voiceGpt", "gpt_file"],
    ["voiceSovits", "sovits_file"],
    ["voiceAudio", "ref_audio"],
  ].forEach(([id, key]) => {
    const file = document.getElementById(id).files[0];
    if (file) form.append(key, file);
  });
  $("#voiceSave").disabled = true;
  $("#voiceProgress").classList.remove("hidden");
  $("#voiceProgress").value = 0;
  try {
    await uploadVoice(
      edit ? `/api/voices/${encodeURIComponent(edit)}` : "/api/voices",
      edit ? "PUT" : "POST",
      form,
    );
    closeDialog("voiceDialog");
    toast("音色已保存");
    await refresh(true);
  } catch (error) {
    errorMessage(error);
  } finally {
    $("#voiceSave").disabled = false;
  }
});
async function init() {
  try {
    localStorage.removeItem("umeko_admin_token");
  } catch {
    /* Storage may be disabled. */
  }
  fillDocs();
  try {
    const info = await api("/api/auth/session");
    if (info.authenticated) await enter(info.csrf_token);
    else if (!info.configured)
      $("#loginError").textContent =
        "请在服务端设置至少 24 字符的 TTS_ADMIN_TOKEN 后重启。";
  } catch (error) {
    $("#loginError").textContent = error.message;
  }
  setInterval(() => {
    if (state.loggedIn && !document.hidden) refresh();
  }, 3000);
}
init();
