"use strict";

const el = (s) => document.querySelector(s);

let currentId = null;
let pollTimer = null;
let templatesCache = null;
let hotwords = [];

const STATUS_LABEL = {
  uploaded: "已上传，排队中",
  processing_audio: "音频处理中",
  transcribing: "转写 + 说话人分离中",
  cleaning_text: "文本清洗中",
  summarizing: "生成 AI 纪要中",
  completed: "已完成",
  failed: "处理失败",
};
const RUNNING = new Set(["uploaded", "processing_audio", "transcribing", "cleaning_text", "summarizing"]);

// ---------- utils ----------
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtDur(sec) {
  sec = Math.round(sec || 0);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const p = (n) => String(n).padStart(2, "0");
  return `${p(h)}:${p(m)}:${p(s)}`;
}
function tsToSeconds(ts) {
  if (!ts || ts === "未明确") return null;
  const parts = String(ts).split(":").map(Number);
  if (parts.some(isNaN)) return null;
  while (parts.length < 3) parts.unshift(0);
  return parts[0] * 3600 + parts[1] * 60 + parts[2];
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  return r.json();
}

// ---------- 时间戳回听（转写 .ts 与 纪要 .src 共用） ----------
document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-sec]");
  if (t && t.dataset.sec) {
    el("#audio-card").hidden = false;
    const audio = el("#audio");
    audio.currentTime = parseFloat(t.dataset.sec);
    audio.play().catch(() => {});
  }
});

// ---------- 模板 ----------
function fillTemplates(templates) {
  templatesCache = templates;
  const opts = Object.entries(templates)
    .map(([k, v]) => `<option value="${k}">${esc(v.name)}</option>`).join("");
  el("#template").innerHTML = opts;
  el("#m-template").innerHTML = opts;
}

// ---------- 热词 ----------
let hwDraft = [];

async function loadHotwords() {
  try {
    const d = await api("/api/hotwords");
    hotwords = d.hotwords || [];
  } catch (e) { hotwords = []; }
  renderHotwordSummary();
}
function renderHotwordSummary() {
  el("#hotword-count").textContent = `${hotwords.length} 个热词`;
  const box = el("#hotword-preview");
  if (!hotwords.length) {
    box.innerHTML = `<span class="tags-empty">暂无热词，点"管理"添加专有名词/人名/项目词</span>`;
    return;
  }
  const PREV = 8;
  const shown = hotwords.slice(0, PREV).map((w) => `<span class="tag">${esc(w)}</span>`).join("");
  const more = hotwords.length > PREV ? `<span class="tags-empty">+${hotwords.length - PREV} 更多</span>` : "";
  box.innerHTML = shown + more;
}

function openHotwordModal() {
  hwDraft = [...hotwords];
  el("#hw-search").value = "";
  el("#hw-add-input").value = "";
  el("#hw-bulk").hidden = true;
  el("#hw-chips").hidden = false;
  el("#hw-bulk-toggle").textContent = "批量编辑";
  el("#hotword-modal").hidden = false;
  renderHwChips();
  el("#hw-add-input").focus();
}
function closeHotwordModal() { el("#hotword-modal").hidden = true; }

function renderHwChips() {
  const q = el("#hw-search").value.trim().toLowerCase();
  const list = q ? hwDraft.filter((w) => w.toLowerCase().includes(q)) : hwDraft;
  el("#hw-count").textContent = `${hwDraft.length} 个` + (q ? `（匹配 ${list.length}）` : "");
  const box = el("#hw-chips");
  if (!list.length) {
    box.innerHTML = `<span class="hw-empty">${q ? "无匹配热词" : "暂无热词，回车添加，或点「批量编辑」粘贴"}</span>`;
    return;
  }
  box.innerHTML = list.map((w) =>
    `<span class="tag">${esc(w)}<span class="x" data-word="${esc(w)}">×</span></span>`).join("");
  box.querySelectorAll(".x").forEach((x) => x.addEventListener("click", () => {
    hwDraft = hwDraft.filter((v) => v !== x.dataset.word);
    renderHwChips();
  }));
}
function hwAdd() {
  const inp = el("#hw-add-input");
  const w = inp.value.trim();
  if (w && !hwDraft.includes(w)) hwDraft.unshift(w);
  inp.value = "";
  el("#hw-search").value = "";
  renderHwChips();
  inp.focus();
}
function hwApplyBulk() {
  const lines = el("#hw-bulk-text").value.split(/[\n,，]/).map((s) => s.trim()).filter(Boolean);
  const seen = new Set(); const merged = [];
  for (const w of lines) { if (!seen.has(w)) { seen.add(w); merged.push(w); } }
  hwDraft = merged;
  el("#hw-bulk").hidden = true;
  el("#hw-chips").hidden = false;
  el("#hw-bulk-toggle").textContent = "批量编辑";
  renderHwChips();
}
async function hwSave() {
  if (!el("#hw-bulk").hidden) hwApplyBulk();
  const btn = el("#hw-save");
  btn.disabled = true;
  try {
    const d = await api("/api/hotwords", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hotwords: hwDraft }),
    });
    hotwords = d.hotwords || [];
    renderHotwordSummary();
    closeHotwordModal();
  } catch (e) { alert("保存失败：" + e.message); }
  finally { btn.disabled = false; }
}

el("#hotword-manage").addEventListener("click", openHotwordModal);
el("#hw-close").addEventListener("click", closeHotwordModal);
el("#hw-cancel").addEventListener("click", closeHotwordModal);
el("#hotword-modal").addEventListener("click", (e) => { if (e.target.id === "hotword-modal") closeHotwordModal(); });
el("#hw-add-btn").addEventListener("click", hwAdd);
el("#hw-add-input").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); hwAdd(); } });
el("#hw-search").addEventListener("input", renderHwChips);
el("#hw-clear").addEventListener("click", () => { if (confirm("清空所有热词？")) { hwDraft = []; renderHwChips(); } });
el("#hw-save").addEventListener("click", hwSave);
el("#hw-bulk-toggle").addEventListener("click", () => {
  const bulk = el("#hw-bulk");
  if (bulk.hidden) {
    // 打开批量编辑：隐藏 chips，避免两块同时撑高弹窗
    el("#hw-bulk-text").value = hwDraft.join("\n");
    bulk.hidden = false;
    el("#hw-chips").hidden = true;
    el("#hw-bulk-toggle").textContent = "完成编辑";
  } else {
    // 再次点击 = 应用文本并返回 chips 视图
    hwApplyBulk();
  }
});
el("#hw-bulk-apply").addEventListener("click", hwApplyBulk);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !el("#hotword-modal").hidden) closeHotwordModal();
});

// ---------- 文件选择 ----------
el("#file").addEventListener("change", () => {
  const f = el("#file").files[0];
  el("#file-name").textContent = f ? f.name : "点击选择音频 / 视频文件";
  el("#file-label").classList.toggle("has-file", !!f);
});

// ---------- 列表 ----------
async function loadList() {
  const data = await api("/api/meetings");
  if (data.templates && !templatesCache) fillTemplates(data.templates);
  const ul = el("#meeting-list");
  if (!data.meetings.length) {
    ul.innerHTML = `<li class="tags-empty" style="padding:8px">还没有会议</li>`;
    return;
  }
  ul.innerHTML = data.meetings.map((m) => `
    <li class="meeting-item ${m.meeting_id === currentId ? "active" : ""}" data-id="${m.meeting_id}">
      <div class="meeting-item-title">${esc(m.title || "未命名会议")}</div>
      <div class="meeting-item-meta">
        <span class="badge ${m.status === "completed" ? "completed" : m.status === "failed" ? "failed" : "running"}">${esc(STATUS_LABEL[m.status] || m.status)}</span>
        <span class="meeting-item-dur">${fmtDur(m.duration_sec)}</span>
      </div>
    </li>`).join("");
  ul.querySelectorAll(".meeting-item").forEach((li) =>
    li.addEventListener("click", () => selectMeeting(li.dataset.id)));
}

// ---------- 上传 ----------
el("#upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = el("#file").files[0];
  if (!file) { el("#upload-msg").textContent = "请先选择文件"; return; }
  const fd = new FormData();
  fd.append("file", file);
  fd.append("template_type", el("#template").value);
  const btn = el("#upload-btn");
  btn.disabled = true;
  el("#upload-msg").textContent = "上传中…";
  try {
    const res = await api("/api/meetings", { method: "POST", body: fd });
    el("#upload-msg").textContent = "已上传，开始处理。";
    el("#file").value = "";
    el("#file-name").textContent = "点击选择音频 / 视频文件";
    el("#file-label").classList.remove("has-file");
    await loadList();
    selectMeeting(res.meeting_id);
  } catch (err) {
    el("#upload-msg").textContent = "上传失败：" + err.message;
  } finally {
    btn.disabled = false;
  }
});

// ---------- 选择 / 轮询 ----------
async function selectMeeting(id) {
  currentId = id;
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  el("#empty").hidden = true;
  el("#meeting-view").hidden = false;
  el("#error-area").innerHTML = "";
  el("#result").hidden = true;
  el("#audio-card").hidden = true;
  await loadList();

  const m = await api(`/api/meetings/${id}`);
  renderHeader(m);
  el("#btn-export-md").href = `/api/meetings/${id}/export?format=md`;
  el("#btn-export-docx").href = `/api/meetings/${id}/export?format=docx`;

  if (m.status === "completed") {
    await renderResults(id);
  } else if (m.status === "failed") {
    showError(m);
  } else {
    showProcessing(m);
    startPoll(id);
  }
}

function renderHeader(m) {
  el("#m-title").textContent = m.title || "未命名会议";
  const badge = el("#m-status");
  badge.textContent = STATUS_LABEL[m.status] || m.status;
  badge.className = "badge " + (m.status === "completed" ? "completed"
    : m.status === "failed" ? "failed" : "running");
  if (m.template_type) el("#m-template").value = m.template_type;
}

function showProcessing(m) {
  el("#processing").hidden = false;
  el("#progressbar").style.width = (m.progress || 0) + "%";
  el("#status-text").textContent = `${STATUS_LABEL[m.status] || m.status}（${m.progress || 0}%）`;
}

function showError(m) {
  el("#processing").hidden = true;
  el("#error-area").innerHTML =
    `<div class="card"><div class="error-box">处理失败（步骤：${esc(m.failed_step || "?")}）<br>${esc(m.error_message || "")}</div></div>`;
}

function startPoll(id) {
  pollTimer = setInterval(async () => {
    if (currentId !== id) { clearInterval(pollTimer); return; }
    let s;
    try { s = await api(`/api/meetings/${id}/status`); } catch (e) { return; }
    showProcessing(s);
    const badge = el("#m-status");
    badge.textContent = STATUS_LABEL[s.status] || s.status;
    badge.className = "badge " + (RUNNING.has(s.status) ? "running" : s.status);
    if (s.status === "completed") {
      clearInterval(pollTimer); pollTimer = null;
      el("#processing").hidden = true;
      renderHeader(await api(`/api/meetings/${id}`));
      await renderResults(id);
      loadList();
    } else if (s.status === "failed") {
      clearInterval(pollTimer); pollTimer = null;
      showError(s);
      loadList();
    }
  }, 2000);
}

// ---------- 结果 ----------
async function renderResults(id) {
  el("#processing").hidden = true;
  el("#result").hidden = false;
  el("#audio-card").hidden = false;
  el("#audio").src = `/api/meetings/${id}/audio`;

  const [tr, sum, tasks, meta] = await Promise.all([
    api(`/api/meetings/${id}/transcript`),
    api(`/api/meetings/${id}/summary`).catch(() => null),
    api(`/api/meetings/${id}/tasks`).catch(() => ({ tasks: [] })),
    api(`/api/meetings/${id}`),
  ]);
  renderTranscript(tr.segments);
  renderSpeakerEdit(tr.segments, meta.speaker_map || {});
  renderSummary(sum);
  renderTodos(tasks.tasks);
}

function renderTranscript(segs) {
  if (!segs.length) { el("#transcript-body").innerHTML = `<div class="unset">无转写内容</div>`; return; }
  el("#transcript-body").innerHTML = segs.map((s) => `
    <div class="seg">
      <span class="ts" data-sec="${s.start_seconds}">${s.start}</span>
      <span class="spk">${esc(s.display)}</span>
      <span>${esc(s.text)}</span>
    </div>`).join("");
}

function renderSpeakerEdit(segs, speakerMap) {
  const speakers = [...new Set(segs.map((s) => s.speaker))];
  if (!speakers.length) { el("#speaker-edit").innerHTML = ""; return; }
  el("#speaker-edit").innerHTML = `
    <div class="se-title">说话人改名</div>
    ${speakers.map((sp) => `
      <div class="row">
        <label>${esc(sp)}</label>
        <input type="text" class="field" data-spk="${esc(sp)}" value="${esc(speakerMap[sp] || "")}" placeholder="真实姓名 / 角色">
      </div>`).join("")}
    <button id="save-speakers" class="btn btn-secondary btn-sm" style="margin-top:6px">保存改名</button>`;
  el("#save-speakers").addEventListener("click", saveSpeakers);
}

async function saveSpeakers() {
  const map = {};
  document.querySelectorAll("#speaker-edit input[data-spk]").forEach((i) => {
    if (i.value.trim()) map[i.dataset.spk] = i.value.trim();
  });
  const btn = el("#save-speakers");
  btn.disabled = true; btn.textContent = "保存中…";
  try {
    await api(`/api/meetings/${currentId}/speakers`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(map),
    });
    const tr = await api(`/api/meetings/${currentId}/transcript`);
    renderTranscript(tr.segments);
    renderSpeakerEdit(tr.segments, map);
  } catch (e) {
    alert("保存失败：" + e.message);
    btn.disabled = false; btn.textContent = "保存改名";
  }
}

function srcTag(ts) {
  const sec = tsToSeconds(ts);
  if (sec === null) return `<span class="unset">未明确</span>`;
  return `<span class="src" data-sec="${sec}">${esc(ts)}</span>`;
}
function valOrUnset(v) {
  return (!v || v === "未明确") ? `<span class="unset">未明确</span>` : esc(v);
}

function renderSummary(sum) {
  if (!sum) { el("#summary-body").innerHTML = `<div class="unset">纪要尚未生成</div>`; return; }
  const blocks = [`<div class="sum-summary">${esc(sum.summary)}</div>`];
  if (sum.topics?.length) blocks.push(listBlock("关键讨论点",
    sum.topics.map((t) => `<b>${esc(t.title)}</b>：${esc(t.summary)} ${srcTag(t.source_time)}`)));
  if (sum.decisions?.length) blocks.push(listBlock("已确认决策",
    sum.decisions.map((d) => `${esc(d.content)} ${srcTag(d.source_time)}`)));
  if (sum.risks?.length) blocks.push(listBlock("风险问题",
    sum.risks.map((r) => `${esc(r.content)} ${srcTag(r.source_time)}`)));
  if (sum.open_questions?.length) blocks.push(listBlock("未决问题",
    sum.open_questions.map((q) => `${esc(q.content)} ${srcTag(q.source_time)}`)));
  el("#summary-body").innerHTML = blocks.join("");
}
function listBlock(title, items) {
  return `<div class="sum-block"><h4>${title}</h4><ul>${items.map((i) => `<li>${i}</li>`).join("")}</ul></div>`;
}

function renderTodos(tasks) {
  if (!tasks?.length) { el("#todos-body").innerHTML = `<div class="unset">无待办事项</div>`; return; }
  el("#todos-body").innerHTML = `
    <table>
      <thead><tr><th>负责人</th><th>事项</th><th>截止时间</th><th>出处</th></tr></thead>
      <tbody>${tasks.map((t) => `<tr>
        <td>${valOrUnset(t.owner)}</td>
        <td>${esc(t.task)}</td>
        <td>${valOrUnset(t.deadline)}</td>
        <td>${srcTag(t.source_time)}</td>
      </tr>`).join("")}</tbody>
    </table>`;
}

// ---------- 重新生成 / 删除 ----------
el("#btn-regen").addEventListener("click", async () => {
  if (!currentId) return;
  const btn = el("#btn-regen");
  btn.disabled = true;
  try {
    await api(`/api/meetings/${currentId}/regenerate`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        template_type: el("#m-template").value,
        custom_instruction: el("#m-instruction").value.trim() || null,
      }),
    });
    el("#result").hidden = true;
    showProcessing({ status: "summarizing", progress: 80 });
    startPoll(currentId);
  } catch (e) {
    alert("重新生成失败：" + e.message);
  } finally {
    btn.disabled = false;
  }
});

el("#btn-delete").addEventListener("click", async () => {
  if (!currentId || !confirm("确定删除这场会议？")) return;
  await api(`/api/meetings/${currentId}`, { method: "DELETE" });
  currentId = null;
  el("#meeting-view").hidden = true;
  el("#empty").hidden = false;
  loadList();
});

// ---------- init ----------
loadHotwords();
loadList();
