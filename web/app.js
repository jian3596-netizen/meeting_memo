"use strict";

const el = (s) => document.querySelector(s);

let currentId = null;
let pollTimer = null;
let templatesCache = null;
let hotwords = [];
let voiceprints = [];
let currentSummary = null;

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

// ---------- 声纹库 ----------
async function loadVoiceprints() {
  try {
    const d = await api("/api/voiceprints");
    voiceprints = d.voiceprints || [];
  } catch (e) { voiceprints = []; }
  renderVoiceprints();
}
function renderVoiceprints() {
  const box = el("#voiceprint-list");
  if (!voiceprints.length) {
    box.innerHTML = `<span class="tags-empty">还没有声纹。在会议详情里给说话人填名字后点「存声纹」，以后会自动识别</span>`;
    return;
  }
  box.innerHTML = voiceprints.map((v) => {
    const badge = v.count > 1 ? ` <span class="vp-count">×${v.count}</span>` : "";
    return `<span class="tag" title="${v.count} 份模板 / 共 ${v.sample_count || 0} 段语音聚合">${esc(v.name)}${badge}<span class="x" data-name="${esc(v.name)}">×</span></span>`;
  }).join("");
  box.querySelectorAll(".x").forEach((x) => x.addEventListener("click", () => deleteVoiceprint(x.dataset.name)));
}
async function deleteVoiceprint(name) {
  if (!confirm(`删除「${name}」的全部声纹模板？`)) return;
  try {
    const d = await api(`/api/voiceprints?name=${encodeURIComponent(name)}`, { method: "DELETE" });
    voiceprints = d.voiceprints || [];
    renderVoiceprints();
  } catch (e) { alert("删除失败：" + e.message); }
}
async function enrollVoiceprint(spk, btn) {
  const input = el(`#speaker-edit input[data-spk="${spk}"]`);
  const name = (input && input.value || "").trim();
  if (!name) { alert("请先在该说话人后面填写姓名，再点「存声纹」"); if (input) input.focus(); return; }
  if (btn) { btn.disabled = true; btn.textContent = "保存中…"; }
  try {
    const d = await api(`/api/meetings/${currentId}/voiceprints`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ speaker: spk, name }),
    });
    await loadVoiceprints();
    if (btn) btn.textContent = d.action === "merged" ? "已增强✓" : "已新增✓";
  } catch (e) {
    alert("保存声纹失败：" + e.message);
    if (btn) { btn.disabled = false; btn.textContent = "存声纹"; }
  }
}

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

  const [tr, sum, meta] = await Promise.all([
    api(`/api/meetings/${id}/transcript`),
    api(`/api/meetings/${id}/summary`).catch(() => null),
    api(`/api/meetings/${id}`),
  ]);
  currentSummary = sum;
  renderTranscript(tr.segments);
  renderSpeakerEdit(tr.segments, meta.speaker_map || {});
  renderSummary(sum);
  renderTodos(sum ? sum.todos : []);
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
    <div class="se-title">说话人改名 / 声纹</div>
    ${speakers.map((sp) => `
      <div class="row">
        <label>${esc(sp)}</label>
        <input type="text" class="field" data-spk="${esc(sp)}" value="${esc(speakerMap[sp] || "")}" placeholder="真实姓名 / 角色">
        <button class="btn btn-ghost btn-sm vp-enroll" data-spk="${esc(sp)}" title="保存该说话人的声纹，以后新会议自动识别">存声纹</button>
      </div>`).join("")}
    <button id="save-speakers" class="btn btn-secondary btn-sm" style="margin-top:6px">保存改名</button>`;
  el("#save-speakers").addEventListener("click", saveSpeakers);
  el("#speaker-edit").querySelectorAll(".vp-enroll").forEach((b) =>
    b.addEventListener("click", () => enrollVoiceprint(b.dataset.spk, b)));
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
    const [tr, sum] = await Promise.all([
      api(`/api/meetings/${currentId}/transcript`),
      api(`/api/meetings/${currentId}/summary`).catch(() => null),
    ]);
    renderTranscript(tr.segments);
    renderSpeakerEdit(tr.segments, map);
    // 改名会同步替换纪要/待办里的旧名，刷新展示
    currentSummary = sum;
    renderSummary(sum);
    renderTodos(sum ? sum.todos : []);
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

// ----- 双击行内编辑：通用工具 -----
function getByPath(obj, path) {
  return path.split(".").reduce((o, k) => (o == null ? o : o[k]), obj);
}
function setByPath(obj, path, val) {
  const ks = path.split(".");
  const last = ks.pop();
  ks.reduce((o, k) => o[k], obj)[last] = val;
}

async function saveSummary() {
  await api(`/api/meetings/${currentId}/summary`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(currentSummary),
  });
}

/** 把元素临时替换为输入框；提交（回车/失焦）调用 onSave(新值)，Esc 取消。值未变则不保存。 */
function inlineEdit(target, value, multiline, onSave) {
  if (target.dataset.editing === "1") return;
  target.dataset.editing = "1";
  const original = target.innerHTML;
  const restoreDisplay = target.style.display;
  if (multiline && target.tagName === "SPAN") target.style.display = "block";
  const field = document.createElement(multiline ? "textarea" : "input");
  field.className = "inline-field";
  field.value = value || "";
  target.innerHTML = "";
  target.appendChild(field);
  field.focus();
  field.select();
  let done = false;
  const finish = async (commit) => {
    if (done) return;
    done = true;
    target.dataset.editing = "0";
    const nv = field.value.trim();
    if (commit && nv !== (value || "").trim()) {
      try { await onSave(nv); }
      catch (e) { alert("保存失败：" + e.message); target.style.display = restoreDisplay; target.innerHTML = original; }
    } else {
      target.style.display = restoreDisplay;
      target.innerHTML = original;
    }
  };
  field.addEventListener("blur", () => finish(true));
  field.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); finish(false); }
    else if (e.key === "Enter" && (!multiline || e.ctrlKey || e.metaKey)) { e.preventDefault(); field.blur(); }
  });
}

/** 在容器上做事件委托：双击 .editable → 行内编辑 currentSummary 中 data-path 指向的字段。 */
function bindInlineEditing(containerSel, rerender) {
  el(containerSel).addEventListener("dblclick", (e) => {
    const t = e.target.closest(".editable");
    if (!t || !el(containerSel).contains(t) || t.dataset.editing === "1") return;
    const path = t.dataset.path;
    const multiline = t.dataset.multiline === "1";
    const unset = t.dataset.unset === "1";
    const cur = getByPath(currentSummary, path);
    const prefill = (cur == null || cur === "未明确") ? "" : String(cur);
    inlineEdit(t, prefill, multiline, async (nv) => {
      setByPath(currentSummary, path, nv || (unset ? "未明确" : ""));
      await saveSummary();
      rerender();
      if (path === "title") { el("#m-title").textContent = currentSummary.title; loadList(); }
    });
  });
}

function renderSummary(sum) {
  if (!sum) { el("#summary-body").innerHTML = `<div class="unset">纪要尚未生成</div>`; return; }
  const ed = (path, html, ml) =>
    `<span class="editable" data-path="${path}"${ml ? ' data-multiline="1"' : ""} title="双击编辑">${html}</span>`;
  const srcEd = (path, ts) =>
    `<span class="editable" data-path="${path}" data-unset="1" title="双击编辑出处时间">${srcTag(ts)}</span>`;
  const blocks = [`<div class="sum-summary">${ed("summary", esc(sum.summary) || '<span class="unset">（双击添加摘要）</span>', true)}</div>`];
  if (sum.topics?.length) blocks.push(listBlock("关键讨论点",
    sum.topics.map((t, i) => `${ed(`topics.${i}.title`, "<b>" + esc(t.title) + "</b>")}：${ed(`topics.${i}.summary`, esc(t.summary), true)} ${srcEd(`topics.${i}.source_time`, t.source_time)}`)));
  if (sum.decisions?.length) blocks.push(listBlock("已确认决策",
    sum.decisions.map((d, i) => `${ed(`decisions.${i}.content`, esc(d.content), true)} ${srcEd(`decisions.${i}.source_time`, d.source_time)}`)));
  if (sum.risks?.length) blocks.push(listBlock("风险问题",
    sum.risks.map((r, i) => `${ed(`risks.${i}.content`, esc(r.content), true)} ${srcEd(`risks.${i}.source_time`, r.source_time)}`)));
  if (sum.open_questions?.length) blocks.push(listBlock("未决问题",
    sum.open_questions.map((q, i) => `${ed(`open_questions.${i}.content`, esc(q.content), true)} ${srcEd(`open_questions.${i}.source_time`, q.source_time)}`)));
  el("#summary-body").innerHTML = blocks.join("");
}
function listBlock(title, items) {
  return `<div class="sum-block"><h4>${title}</h4><ul>${items.map((i) => `<li>${i}</li>`).join("")}</ul></div>`;
}

function renderTodos(todos) {
  todos = todos || [];
  const addBtn = `<button id="todo-add" class="btn btn-secondary btn-sm" style="margin-top:10px">+ 添加一行</button>`;
  if (!todos.length) {
    el("#todos-body").innerHTML = `<div class="unset" style="margin-bottom:6px">无待办事项</div>${addBtn}`;
  } else {
    const cell = (i, field, html, unset) =>
      `<td class="editable" data-path="todos.${i}.${field}"${field === "task" ? ' data-multiline="1"' : ""}${unset ? ' data-unset="1"' : ""} title="双击编辑">${html}</td>`;
    el("#todos-body").innerHTML = `
      <table>
        <thead><tr><th>负责人</th><th>事项</th><th>截止时间</th><th>出处</th><th></th></tr></thead>
        <tbody>${todos.map((t, i) => `<tr data-i="${i}">
          ${cell(i, "owner", valOrUnset(t.owner), true)}
          ${cell(i, "task", esc(t.task) || '<span class="unset">（双击填写）</span>')}
          ${cell(i, "deadline", valOrUnset(t.deadline), true)}
          ${cell(i, "source_time", srcTag(t.source_time), true)}
          <td><button class="row-del" title="删除该行">×</button></td>
        </tr>`).join("")}</tbody>
      </table>${addBtn}`;
    el("#todos-body").querySelectorAll(".row-del").forEach((b) => b.addEventListener("click", async () => {
      const i = +b.closest("tr").dataset.i;
      if (!confirm("删除该行待办？")) return;
      currentSummary.todos.splice(i, 1);
      try { await saveSummary(); renderTodos(currentSummary.todos); }
      catch (e) { alert("保存失败：" + e.message); }
    }));
  }
  el("#todo-add").addEventListener("click", async () => {
    if (!currentSummary) return;
    currentSummary.todos = currentSummary.todos || [];
    currentSummary.todos.push({ owner: "未明确", task: "", deadline: "未明确", source_time: "未明确" });
    try { await saveSummary(); renderTodos(currentSummary.todos); }
    catch (e) { alert("保存失败：" + e.message); }
  });
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

// ---------- 双击行内编辑（纪要 / 待办 / 标题） ----------
bindInlineEditing("#summary-body", () => renderSummary(currentSummary));
bindInlineEditing("#todos-body", () => renderTodos(currentSummary?.todos || []));
el("#m-title").addEventListener("dblclick", () => {
  if (!currentSummary) return;
  inlineEdit(el("#m-title"), el("#m-title").textContent, false, async (nv) => {
    currentSummary.title = nv || currentSummary.title;
    await saveSummary();
    el("#m-title").textContent = currentSummary.title;
    loadList();
  });
});

// ---------- 侧栏收起 / 展开 ----------
function setSidebar(collapsed) {
  el("#app").classList.toggle("sidebar-collapsed", collapsed);
  el("#sidebar-expand").hidden = !collapsed;
  try { localStorage.setItem("sidebarCollapsed", collapsed ? "1" : "0"); } catch (e) {}
}
el("#sidebar-collapse").addEventListener("click", () => setSidebar(true));
el("#sidebar-expand").addEventListener("click", () => setSidebar(false));
setSidebar((() => { try { return localStorage.getItem("sidebarCollapsed") === "1"; } catch (e) { return false; } })());

// ---------- init ----------
loadHotwords();
loadVoiceprints();
loadList();
