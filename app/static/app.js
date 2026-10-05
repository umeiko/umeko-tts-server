/* Umeko TTS 控制台前端逻辑 */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const state = {
  voices: [],
  status: null,
  token: localStorage.getItem("umeko_admin_token") || "",
  editing: null, // null=新增，否则为音色名
};

// ---------- 工具 ----------

function toast(msg, type = "info") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  $("#toastWrap").appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtUptime(sec) {
  if (sec < 60) return `${sec} 秒`;
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟`;
  return `${Math.floor(sec / 3600)} 小时 ${Math.floor((sec % 3600) / 60)} 分`;
}

async function api(path, { method = "GET", json, raw = false } = {}) {
  const headers = {};
  if (state.token) headers["X-Admin-Token"] = state.token;
  let body;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  }
  const res = await fetch(path, { method, headers, body });
  if (res.status === 401) {
    showTokenModal();
    throw new Error("未授权，请先填写管理令牌");
  }
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { msg = (await res.json()).message || msg; } catch { /* 非 JSON 响应 */ }
    throw new Error(msg);
  }
  return raw ? res : res.json();
}

// 大文件上传用 XHR 以显示进度
function uploadWithProgress(url, method, form, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, url);
    if (state.token) xhr.setRequestHeader("X-Admin-Token", state.token);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let j = null;
      try { j = JSON.parse(xhr.responseText); } catch { /* 忽略 */ }
      if (xhr.status === 401) showTokenModal();
      if (xhr.status >= 200 && xhr.status < 300) resolve(j);
      else reject(new Error((j && j.message) || `HTTP ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error("网络错误"));
    xhr.send(form);
  });
}

// ---------- 状态栏 ----------

async function loadStatus() {
  try {
    const s = await api("/api/status");
    state.status = s;
    const b = s.engine.backend;
    $("#stBackend").innerHTML = `后端: <strong>${esc(b.name)}</strong>`;
    $("#stBackend").classList.toggle("warn", !!b.demo);
    $("#stDevice").innerHTML = `设备: <strong>${esc(b.device || "-")}</strong>`;
    $("#stWorkers").innerHTML = `线程: <strong>${s.engine.workers}</strong>`;
    const q = s.engine.queue;
    $("#stQueue").innerHTML =
      `队列: <strong>${q.pending}</strong> 待处理 / <strong>${q.running}</strong> 执行中 / <strong>${q.completed}</strong> 完成` +
      (q.failed ? ` / <strong>${q.failed}</strong> 失败` : "");
    $("#stDefault").innerHTML = `默认音色: <strong>${esc(s.default_voice || "未设置")}</strong>`;
    $("#stUptime").innerHTML = `运行: <strong>${fmtUptime(s.engine.uptime_sec)}</strong>`;
    $("#mockBanner").classList.toggle("hidden", !b.demo);
  } catch (e) {
    if (e.message.includes("未授权")) return;
    $("#stBackend").textContent = `状态获取失败: ${e.message}`;
  }
}

// ---------- 音色管理 ----------

async function loadVoices() {
  try {
    state.voices = await api("/api/voices");
  } catch (e) {
    if (!e.message.includes("未授权")) toast(e.message, "error");
    return;
  }
  renderVoices();
  renderVoiceSelect();
}

function renderVoices() {
  const grid = $("#voiceGrid");
  $("#voiceEmpty")?.remove();
  grid.innerHTML = "";
  if (!state.voices.length) {
    grid.innerHTML = '<div class="empty" id="voiceEmpty">暂无音色，点击「新增音色」创建</div>';
    return;
  }
  for (const v of state.voices) {
    const card = document.createElement("div");
    card.className = "voice-card" + (v.is_default ? " default" : "");
    const chip = (ok, label) =>
      `<span class="chip ${ok ? "ok" : "miss"}">${label}${ok ? " ✓" : " ✗"}</span>`;
    card.innerHTML = `
      <div class="vc-head">
        <span class="vc-name">${esc(v.name)}</span>
        ${v.is_default ? '<span class="badge">默认</span>' : ""}
        ${v.is_complete ? "" : '<span class="badge gray">待完善</span>'}
      </div>
      <div class="vc-desc">${esc(v.description || "（无描述）")}</div>
      <div class="vc-files">
        ${chip(v.has_gpt, "GPT 权重")}
        ${chip(v.has_sovits, "SoVITS 权重")}
        ${chip(v.has_ref_audio, "参考音频")}
      </div>
      <div class="vc-ref">参考文本 (${esc(v.ref_lang)}): ${esc(v.ref_text || "未填写")}</div>
      <div class="vc-meta">更新于 ${esc(v.updated_at)}</div>
      <div class="vc-actions">
        <button class="btn" data-act="ref" ${v.has_ref_audio ? "" : "disabled"}>试听参考</button>
        <button class="btn" data-act="default" ${v.is_default ? "disabled" : ""}>设为默认</button>
        <button class="btn" data-act="edit">编辑</button>
        <button class="btn danger" data-act="del">删除</button>
      </div>`;
    card.querySelector('[data-act="ref"]').addEventListener("click", () => playRefAudio(v.name));
    card.querySelector('[data-act="default"]').addEventListener("click", () => setDefault(v.name));
    card.querySelector('[data-act="edit"]').addEventListener("click", () => openVoiceModal(v));
    card.querySelector('[data-act="del"]').addEventListener("click", () => deleteVoice(v.name));
    grid.appendChild(card);
  }
}

async function playRefAudio(name) {
  try {
    const res = await api(`/api/voices/${encodeURIComponent(name)}/ref-audio`, { raw: true });
    const url = URL.createObjectURL(await res.blob());
    const audio = new Audio(url);
    audio.onended = () => URL.revokeObjectURL(url);
    audio.play();
  } catch (e) {
    toast(e.message, "error");
  }
}

async function setDefault(name) {
  try {
    await api(`/api/voices/${encodeURIComponent(name)}/default`, { method: "POST" });
    toast(`已将「${name}」设为默认音色`, "ok");
    loadVoices();
    loadStatus();
  } catch (e) {
    toast(e.message, "error");
  }
}

async function deleteVoice(name) {
  if (!confirm(`确定删除音色「${name}」？其权重与参考音频文件将一并删除。`)) return;
  try {
    await api(`/api/voices/${encodeURIComponent(name)}`, { method: "DELETE" });
    toast(`音色「${name}」已删除`, "ok");
    loadVoices();
    loadStatus();
  } catch (e) {
    toast(e.message, "error");
  }
}

// ---------- 音色弹窗（新增 / 编辑） ----------

function openVoiceModal(voice) {
  state.editing = voice ? voice.name : null;
  $("#voiceModalTitle").textContent = voice ? `编辑音色: ${voice.name}` : "新增音色";
  $("#vName").value = voice ? voice.name : "";
  $("#vName").disabled = !!voice;
  $("#vDesc").value = voice ? voice.description : "";
  $("#vRefText").value = voice ? voice.ref_text : "";
  $("#vRefLang").value = voice ? voice.ref_lang : "zh";
  $("#vGpt").value = "";
  $("#vSovits").value = "";
  $("#vRefAudio").value = "";
  $("#uploadProgress").classList.add("hidden");
  $("#voiceModal").classList.remove("hidden");
}

function closeVoiceModal() {
  $("#voiceModal").classList.add("hidden");
}

async function saveVoice() {
  const form = new FormData();
  const name = $("#vName").value.trim();
  if (!state.editing) {
    if (!name) { toast("请填写音色名称", "error"); return; }
    form.append("name", name);
  }
  form.append("description", $("#vDesc").value);
  form.append("ref_text", $("#vRefText").value);
  form.append("ref_lang", $("#vRefLang").value);
  const gpt = $("#vGpt").files[0];
  const sovits = $("#vSovits").files[0];
  const refAudio = $("#vRefAudio").files[0];
  if (gpt) form.append("gpt_file", gpt);
  if (sovits) form.append("sovits_file", sovits);
  if (refAudio) form.append("ref_audio", refAudio);

  const url = state.editing
    ? `/api/voices/${encodeURIComponent(state.editing)}`
    : "/api/voices";
  const method = state.editing ? "PUT" : "POST";

  const bar = $("#uploadProgressBar");
  $("#uploadProgress").classList.remove("hidden");
  $("#btnVoiceSave").disabled = true;
  try {
    await uploadWithProgress(url, method, form, (p) => {
      bar.style.width = `${Math.round(p * 100)}%`;
    });
    toast(state.editing ? "音色已更新" : `音色「${name}」已创建`, "ok");
    closeVoiceModal();
    loadVoices();
    loadStatus();
  } catch (e) {
    toast(e.message, "error");
  } finally {
    $("#btnVoiceSave").disabled = false;
    $("#uploadProgress").classList.add("hidden");
    bar.style.width = "0%";
  }
}

// ---------- 在线合成 ----------

function renderVoiceSelect() {
  const sel = $("#fVoice");
  const cur = sel.value;
  sel.innerHTML = "";
  if (!state.voices.length) {
    sel.innerHTML = '<option value="">（暂无音色）</option>';
    return;
  }
  for (const v of state.voices) {
    const opt = document.createElement("option");
    opt.value = v.name;
    opt.textContent = v.name + (v.is_default ? "（默认）" : "") + (v.is_complete ? "" : "（待完善）");
    sel.appendChild(opt);
  }
  const def = state.voices.find((v) => v.is_default);
  sel.value = cur || (def ? def.name : state.voices[0].name);
}

async function synthesize() {
  const text = $("#fText").value.trim();
  if (!text) { toast("请输入要合成的文本", "error"); return; }
  const payload = {
    text,
    voice: $("#fVoice").value || undefined,
    text_lang: $("#fLang").value,
    speed_factor: parseFloat($("#fSpeed").value) || 1.0,
    top_k: parseInt($("#fTopK").value) || 15,
    top_p: parseFloat($("#fTopP").value) || 1.0,
    temperature: parseFloat($("#fTemp").value) || 1.0,
    cut_punc: $("#fCutPunc").value,
    max_sec: parseFloat($("#fMaxSec").value) || 10,
  };
  const btn = $("#btnSynth");
  btn.disabled = true;
  btn.textContent = "合成中…";
  const t0 = performance.now();
  try {
    const res = await fetch("/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try { msg = (await res.json()).message || msg; } catch { /* 忽略 */ }
      throw new Error(msg);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const audio = $("#synthAudio");
    audio.src = url;
    $("#synthDownload").href = url;
    const clientMs = Math.round(performance.now() - t0);
    const serverMs = res.headers.get("X-Inference-Ms");
    const voiceUsed = decodeURIComponent(res.headers.get("X-Voice") || "");
    $("#synthMeta").textContent =
      `音色: ${voiceUsed} · 大小: ${(blob.size / 1024).toFixed(1)} KB · 耗时: ${serverMs || clientMs} ms`;
    $("#synthResult").classList.remove("hidden");
    audio.play().catch(() => { /* 浏览器自动播放限制，忽略 */ });
    loadStatus();
  } catch (e) {
    toast(e.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "开始合成";
  }
}

// ---------- 令牌弹窗 ----------

function showTokenModal() {
  $("#tokenInput").value = state.token;
  $("#tokenModal").classList.remove("hidden");
}

// ---------- 文档 ----------

function fillDocs() {
  const base = location.origin;
  $("#docBase").textContent = base;
  $("#docCurlMin").textContent =
    `curl -X POST ${base}/tts \\\n  -H "Content-Type: application/json" \\\n  -d '{"text": "你好世界"}' \\\n  -o output.wav`;
  $("#docCurlFull").textContent =
    `curl -X POST ${base}/tts \\\n  -H "Content-Type: application/json" \\\n  -d '{\n    "text": "你好世界，这是语音合成测试",\n    "voice": "我的音色",\n    "text_lang": "zh",\n    "speed_factor": 1.0,\n    "top_k": 15,\n    "top_p": 1.0,\n    "temperature": 1.0,\n    "cut_punc": "",\n    "max_sec": 10\n  }' \\\n  -o output.wav`;
}

// ---------- 初始化 ----------

function init() {
  $$(".tab").forEach((tab) =>
    tab.addEventListener("click", () => {
      $$(".tab").forEach((t) => t.classList.remove("active"));
      $$(".tab-page").forEach((p) => p.classList.remove("active"));
      tab.classList.add("active");
      $(`#tab-${tab.dataset.tab}`).classList.add("active");
    })
  );
  $("#btnNewVoice").addEventListener("click", () => openVoiceModal(null));
  $("#btnRefresh").addEventListener("click", () => { loadVoices(); loadStatus(); });
  $("#btnVoiceCancel").addEventListener("click", closeVoiceModal);
  $("#btnVoiceSave").addEventListener("click", saveVoice);
  $("#voiceModal").addEventListener("click", (e) => {
    if (e.target.id === "voiceModal") closeVoiceModal();
  });
  $("#btnSynth").addEventListener("click", synthesize);
  $("#btnTokenSave").addEventListener("click", () => {
    state.token = $("#tokenInput").value.trim();
    localStorage.setItem("umeko_admin_token", state.token);
    $("#tokenModal").classList.add("hidden");
    toast("令牌已保存", "ok");
    loadStatus();
    loadVoices();
  });

  fillDocs();
  loadStatus();
  loadVoices();
  setInterval(loadStatus, 3000);
}

document.addEventListener("DOMContentLoaded", init);
