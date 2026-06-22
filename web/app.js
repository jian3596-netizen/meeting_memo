"use strict";

const el = (s) => document.querySelector(s);

let currentId = null;
let pollTimer = null;
let categories = [];   // [{name, prompt}]
let hotwords = [];
let voiceprints = [];
let currentSummary = null;
let allMeetings = [];
let activeTagFilter = new Set();
let queuePollTimer = null;

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
function fmtDate(iso) { return iso ? String(iso).slice(0, 10) : ""; }
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
function statusClass(s) { return s === "completed" ? "completed" : s === "failed" ? "failed" : "running"; }
function fillDatalist(sel, arr) { el(sel).innerHTML = arr.map((v) => `<option value="${esc(v)}">`).join(""); }

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

// ---------- 分类（下拉填充） ----------
function categoryNames() { return categories.map((c) => c.name); }

function fillCategories() {
  const names = categoryNames();
  const opts = `<option value="">未分类</option>`
    + names.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
  el("#upload-category").innerHTML = opts;
  el("#m-category-sel").innerHTML = opts;
}

// ---------- 视图切换 ----------
function showLibrary() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  currentId = null;
  el("#meeting-view").hidden = true;
  el("#library-view").hidden = false;
  el("#nav-library").classList.add("active");
  loadLibrary();
}
function openMeeting(id) {
  el("#library-view").hidden = true;
  el("#meeting-view").hidden = false;
  selectMeeting(id);
}
el("#nav-library").addEventListener("click", showLibrary);
el("#btn-back").addEventListener("click", showLibrary);

// ---------- 录音库 ----------
async function loadLibrary() {
  const data = await api("/api/meetings");
  if (data.categories) { categories = data.categories; fillCategories(); }
  allMeetings = data.meetings || [];
  buildFilterOptions();
  renderCards();
  updateQueueBadge();
  ensureQueuePolling();
}

// ---------- 处理队列（右上角） ----------
function pendingMeetings() {
  return allMeetings.filter((m) => m.status !== "completed");  // 排队中/处理中/失败
}
function updateQueueBadge() {
  const n = pendingMeetings().length;
  const b = el("#queue-badge");
  b.textContent = n;
  b.hidden = n === 0;
}
function ensureQueuePolling() {
  const running = allMeetings.some((m) => RUNNING.has(m.status));
  if (running && !queuePollTimer) {
    queuePollTimer = setInterval(refreshQueue, 2500);
  } else if (!running && queuePollTimer) {
    clearInterval(queuePollTimer); queuePollTimer = null;
  }
}
async function refreshQueue() {
  let data;
  try { data = await api("/api/meetings"); } catch (e) { return; }
  allMeetings = data.meetings || [];
  updateQueueBadge();
  if (!el("#queue-modal").hidden) renderQueue();
  if (!el("#library-view").hidden) { buildFilterOptions(); renderCards(); }
  ensureQueuePolling();
}
function renderQueue() {
  const box = el("#queue-list");
  const list = pendingMeetings();
  if (!list.length) {
    box.innerHTML = `<div class="tags-empty" style="padding:10px">队列为空，所有录音都已处理完成。</div>`;
    return;
  }
  box.innerHTML = list.map((m) => {
    const failed = m.status === "failed";
    const label = (STATUS_LABEL[m.status] || m.status) + (failed ? "" : `（${m.progress || 0}%）`);
    return `<div class="q-item" data-id="${m.meeting_id}">
      <div class="q-main">
        <div class="q-title" title="${esc(m.title || "未命名")}">${esc(m.title || "未命名")}</div>
        <span class="badge ${statusClass(m.status)}">${esc(label)}</span>
        <button class="q-del rec-btn rec-del" title="删除">🗑</button>
      </div>
      ${failed ? `<div class="q-err">${esc(m.error_message || "处理失败")}</div>`
        : `<div class="q-progress"><div style="width:${m.progress || 0}%"></div></div>`}
    </div>`;
  }).join("");
  box.querySelectorAll(".q-del").forEach((b) => b.addEventListener("click", async () => {
    const id = b.closest(".q-item").dataset.id;
    if (!confirm("从队列中删除这条录音？")) return;
    try { await api(`/api/meetings/${id}`, { method: "DELETE" }); await refreshQueue(); }
    catch (e) { alert("删除失败：" + e.message); }
  }));
}
function openQueueModal() { renderQueue(); el("#queue-modal").hidden = false; refreshQueue(); }
el("#btn-queue-open").addEventListener("click", openQueueModal);
el("#queue-close").addEventListener("click", () => { el("#queue-modal").hidden = true; });
el("#queue-modal").addEventListener("click", (e) => { if (e.target.id === "queue-modal") el("#queue-modal").hidden = true; });

// ---------- 分类库管理（左右栏：左选分类，右编辑 Prompt） ----------
let catDraft = [];
let catSel = -1;

function openCategoryModal() {
  catDraft = categories.map((c) => ({ name: c.name, prompt: c.prompt || "" }));
  catSel = catDraft.length ? 0 : -1;
  renderCatModal();
  el("#category-modal").hidden = false;
}
function closeCategoryModal() { el("#category-modal").hidden = true; }

function flushCatRight() {
  if (catSel < 0 || catSel >= catDraft.length) return;
  const ni = el("#cat-name-input"), pi = el("#cat-prompt-input");
  if (ni) catDraft[catSel].name = ni.value;
  if (pi) catDraft[catSel].prompt = pi.value;
}
function renderCatModal() { renderCatLeft(); renderCatRight(); }

function renderCatLeft() {
  const box = el("#cat-left-list");
  if (!catDraft.length) {
    box.innerHTML = `<div class="tags-empty" style="padding:8px">还没有分类</div>`;
    return;
  }
  box.innerHTML = catDraft.map((c, i) =>
    `<div class="cat-item${i === catSel ? " active" : ""}" data-i="${i}">${esc(c.name || "（未命名）")}</div>`).join("");
  box.querySelectorAll(".cat-item").forEach((it) => it.addEventListener("click", () => {
    flushCatRight();
    catSel = +it.dataset.i;
    renderCatModal();
  }));
}

function renderCatRight() {
  const box = el("#cat-right");
  if (catSel < 0 || catSel >= catDraft.length) {
    box.innerHTML = `<div class="tags-empty" style="padding:20px">从左侧选择一个分类，或点「＋ 新增分类」</div>`;
    return;
  }
  const c = catDraft[catSel];
  box.innerHTML = `
    <label class="edit-label">分类名称</label>
    <input id="cat-name-input" class="field" value="${esc(c.name)}" placeholder="分类名称">
    <label class="edit-label" style="margin-top:12px">总结 Prompt（关注重点）</label>
    <textarea id="cat-prompt-input" class="edit-ta cat-prompt-big" placeholder="这个分类生成纪要时的关注重点，例如：关注项目进展、阻塞、决策、各事项负责人与截止时间…">${esc(c.prompt)}</textarea>`;
  el("#cat-name-input").addEventListener("input", () => {
    catDraft[catSel].name = el("#cat-name-input").value;
    renderCatLeft();
  });
}

async function saveCategories() {
  flushCatRight();
  const list = catDraft
    .map((c) => ({ name: (c.name || "").trim(), prompt: (c.prompt || "").trim() }))
    .filter((c) => c.name);
  const btn = el("#cat-save");
  btn.disabled = true; btn.textContent = "保存中…";
  try {
    const d = await api("/api/categories", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ categories: list }),
    });
    categories = d.categories || [];
    fillCategories();
    buildFilterOptions();
    renderCards();
    closeCategoryModal();
  } catch (e) { alert("保存失败：" + e.message); }
  finally { btn.disabled = false; btn.textContent = "保存"; }
}

el("#btn-category-open").addEventListener("click", openCategoryModal);
el("#cat-close").addEventListener("click", closeCategoryModal);
el("#cat-cancel").addEventListener("click", closeCategoryModal);
el("#cat-save").addEventListener("click", saveCategories);
el("#cat-add").addEventListener("click", () => {
  flushCatRight();
  catDraft.push({ name: "新分类", prompt: "" });
  catSel = catDraft.length - 1;
  renderCatModal();
  const ni = el("#cat-name-input"); if (ni) { ni.focus(); ni.select(); }
});
el("#cat-del").addEventListener("click", () => {
  if (catSel < 0) return;
  if (!confirm(`删除分类「${catDraft[catSel].name || ""}」？`)) return;
  catDraft.splice(catSel, 1);
  catSel = catDraft.length ? Math.min(catSel, catDraft.length - 1) : -1;
  renderCatModal();
});
el("#category-modal").addEventListener("click", (e) => { if (e.target.id === "category-modal") closeCategoryModal(); });

function allCategories() {
  // 分类库里的 + 录音里已用到的（兼容已删除的分类仍能筛选）
  const cats = new Set(categoryNames());
  allMeetings.forEach((m) => { if (m.category) cats.add(m.category); });
  return [...cats];
}
function allTags() {
  const tags = new Set();
  allMeetings.forEach((m) => (m.tags || []).forEach((t) => tags.add(t)));
  return [...tags];
}
function allPeople() {
  const ppl = new Set();
  allMeetings.forEach((m) => (m.participants || []).forEach((p) => ppl.add(p)));
  voiceprints.forEach((v) => ppl.add(v.name));
  return [...ppl];
}

function buildFilterOptions() {
  const cats = allCategories();
  const sel = el("#lib-category");
  const cur = sel.value;
  sel.innerHTML = `<option value="">全部分类</option>` +
    cats.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join("");
  sel.value = cats.includes(cur) ? cur : "";

  const tags = allTags();
  // 清掉已不存在的标签筛选
  activeTagFilter = new Set([...activeTagFilter].filter((t) => tags.includes(t)));
  el("#lib-tagfilter").innerHTML = tags.length
    ? tags.map((t) => `<span class="tag tag-filter${activeTagFilter.has(t) ? " active" : ""}" data-tag="${esc(t)}">${esc(t)}</span>`).join("")
    : `<span class="tags-empty">还没有标签，进某条录音点「编辑信息」可添加</span>`;
  el("#lib-tagfilter").querySelectorAll(".tag-filter").forEach((x) => x.addEventListener("click", () => {
    const t = x.dataset.tag;
    if (activeTagFilter.has(t)) activeTagFilter.delete(t); else activeTagFilter.add(t);
    buildFilterOptions(); renderCards();
  }));
}

function renderCards() {
  const grid = el("#card-grid");
  const empty = el("#lib-empty");
  // 录音库只展示「已完成」；处理中/失败的在右上角「队列」里看
  const completed = allMeetings.filter((m) => m.status === "completed");
  if (!completed.length) {
    grid.innerHTML = "";
    empty.hidden = false;
    empty.innerHTML = `<div class="empty-icon">📋</div><div class="empty-title">还没有已完成的录音</div>
      <div class="empty-sub">点右上角「＋ 上传录音」开始；处理进度看「队列」</div>`;
    return;
  }
  const q = el("#lib-search").value.trim().toLowerCase();
  const cat = el("#lib-category").value;
  const list = completed.filter((m) => {
    if (cat && (m.category || "") !== cat) return false;
    if (activeTagFilter.size) {
      const mt = new Set(m.tags || []);
      for (const t of activeTagFilter) if (!mt.has(t)) return false;
    }
    if (q) {
      const hay = [m.title, m.description, m.category, m.audio_time, (m.tags || []).join(" "), (m.participants || []).join(" ")]
        .join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  if (!list.length) {
    grid.innerHTML = "";
    empty.hidden = false;
    empty.innerHTML = `<div class="empty-icon">🔍</div><div class="empty-title">没有匹配的录音</div>
      <div class="empty-sub">换个搜索词或清掉筛选</div>`;
    return;
  }
  empty.hidden = true;
  grid.innerHTML = `<table class="lib-table">
    <thead><tr>
      <th>分类</th><th>标题</th><th>描述</th><th>音频时间</th><th>时长</th><th>人员</th><th>标签</th><th></th>
    </tr></thead>
    <tbody>${list.map(rowHtml).join("")}</tbody>
  </table>`;
}

function rowHtml(m) {
  const muted = `<span class="lib-muted">—</span>`;
  const title = esc(m.title || "未命名会议");
  const cat = m.category ? esc(m.category) : muted;
  const desc = m.description ? esc(m.description) : "";
  const atime = m.audio_time || fmtDate(m.created_at);
  const peopleStr = (m.participants || []).join("、");
  const people = peopleStr ? esc(peopleStr) : muted;
  const tags = (m.tags || []).length
    ? (m.tags || []).map((t) => `<span class="tag tag-mini">${esc(t)}</span>`).join("")
    : muted;
  return `<tr class="lib-row" data-id="${m.meeting_id}">
    <td class="lib-edit lib-cat" data-field="category" title="双击编辑分类">${cat}</td>
    <td class="lib-edit lib-titlecell" data-field="title" title="${title}"><div class="lib-title">${title}</div></td>
    <td class="lib-edit lib-desccell" data-field="description" title="${esc(m.description || "")}"><div class="lib-desc">${desc || muted}</div></td>
    <td class="lib-edit lib-nowrap" data-field="audio_time" title="双击编辑（如 2026-06-22）">${esc(atime) || muted}</td>
    <td class="lib-nowrap">${fmtDur(m.duration_sec)}</td>
    <td class="lib-people" title="${esc(peopleStr)}（由声纹识别，不可手动改）">${people}</td>
    <td class="lib-edit lib-tagcell" data-field="tags" title="双击编辑（顿号/逗号分隔）">${tags}</td>
    <td class="lib-ops"><button class="rec-btn rec-view" title="查看详情">👁</button></td>
  </tr>`;
}

// 录音库：点「👁 查看」进详情；双击单元格行内编辑（两者分开，避免误操作）
el("#card-grid").addEventListener("click", (e) => {
  const v = e.target.closest(".rec-view");
  if (!v) return;
  const tr = v.closest(".lib-row");
  if (tr) openMeeting(tr.dataset.id);
});
el("#card-grid").addEventListener("dblclick", (e) => {
  const td = e.target.closest("td.lib-edit");
  if (!td || td.dataset.editing === "1") return;
  const m = allMeetings.find((x) => x.meeting_id === td.closest("tr").dataset.id);
  if (!m) return;
  const field = td.dataset.field;
  if (field === "category") { inlineSelectCategory(td, m); return; }
  const cur = field === "tags" ? (m.tags || []).join("、")
    : field === "audio_time" ? (m.audio_time || fmtDate(m.created_at))
    : (m[field] || "");
  inlineEdit(td, cur, field === "description", async (nv) => {
    const body = {};
    body[field] = field === "tags" ? nv.split(/[,，、]/).map((s) => s.trim()).filter(Boolean) : nv;
    await saveMetaField(m, body);
  });
});

/** 把某条会议的元数据局部更新并重渲染。 */
async function saveMetaField(m, body) {
  const r = await api(`/api/meetings/${m.meeting_id}/meta`, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  Object.assign(m, r.meeting);
  buildFilterOptions();
  renderCards();
}

/** 分类单元格：双击 → 从分类库下拉选择；改后提示是否按新分类重生成纪要。 */
function inlineSelectCategory(td, m) {
  if (td.dataset.editing === "1") return;
  td.dataset.editing = "1";
  const original = td.innerHTML;
  const cur = m.category || "";
  const sel = document.createElement("select");
  sel.className = "inline-field";
  sel.innerHTML = `<option value="">-</option>`
    + categoryNames().map((c) => `<option value="${esc(c)}"${c === cur ? " selected" : ""}>${esc(c)}</option>`).join("");
  td.innerHTML = "";
  td.appendChild(sel);
  sel.focus();
  let done = false;
  const restore = () => { td.dataset.editing = "0"; td.innerHTML = original; };
  sel.addEventListener("change", async () => {
    if (done) return;
    done = true;
    td.dataset.editing = "0";
    const val = sel.value;
    if (val === cur) { td.innerHTML = original; return; }
    try {
      await saveMetaField(m, { category: val });
      // 分类变了 → 询问是否按新分类的 Prompt 重新生成纪要
      if (val && confirm(`已改为「${val}」分类。是否按该分类的 Prompt 重新生成会议纪要？`)) {
        await api(`/api/meetings/${m.meeting_id}/regenerate`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ category: val }),
        });
        await refreshQueue();   // 重生成期间该条会进队列，转完自动回到列表
      }
    } catch (e) { alert("操作失败：" + e.message); td.innerHTML = original; }
  });
  sel.addEventListener("blur", () => { if (!done) restore(); });
}

el("#lib-search").addEventListener("input", renderCards);
el("#lib-category").addEventListener("change", renderCards);

// ---------- 录音信息编辑（抽屉） ----------
let metaEditId = null;
let tagChips;

function makeChipEditor(boxSel, inputSel) {
  let items = [];
  const box = el(boxSel), input = el(inputSel);
  function render() {
    box.innerHTML = items.length
      ? items.map((w, i) => `<span class="tag">${esc(w)}<span class="x" data-i="${i}">×</span></span>`).join("")
      : `<span class="tags-empty">（空）</span>`;
    box.querySelectorAll(".x").forEach((x) => x.addEventListener("click", () => { items.splice(+x.dataset.i, 1); render(); }));
  }
  function add(v) { v = (v || "").trim(); if (v && !items.includes(v)) { items.push(v); render(); } }
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); add(input.value); input.value = ""; }
  });
  input.addEventListener("blur", () => { add(input.value); input.value = ""; });
  return {
    get() { add(input.value); input.value = ""; return items.slice(); },
    set(a) { items = (a || []).slice(); render(); },
  };
}

function openMetaDrawer(id) {
  const m = allMeetings.find((x) => x.meeting_id === id);
  if (!m) return;
  metaEditId = id;
  el("#meta-title").value = m.title || "";
  el("#meta-category").value = m.category || "";
  el("#meta-desc").value = m.description || "";
  el("#meta-audio-time").value = m.audio_time || fmtDate(m.created_at);
  el("#meta-people-view").textContent = (m.participants || []).join("、") || "（声纹识别后自动出现）";
  tagChips.set(m.tags || []);
  fillDatalist("#category-options", allCategories());
  fillDatalist("#tag-options", allTags());
  el("#meta-drawer").hidden = false;
  el("#meta-title").focus();
}
function closeMetaDrawer() { el("#meta-drawer").hidden = true; metaEditId = null; }

async function saveMeta() {
  if (!metaEditId) return;
  const body = {
    title: el("#meta-title").value.trim(),
    category: el("#meta-category").value.trim(),
    description: el("#meta-desc").value.trim(),
    audio_time: el("#meta-audio-time").value.trim(),
    tags: tagChips.get(),
  };
  const btn = el("#meta-save");
  btn.disabled = true; btn.textContent = "保存中…";
  try {
    await api(`/api/meetings/${metaEditId}/meta`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const wasCurrent = metaEditId === currentId;
    closeMetaDrawer();
    await loadLibrary();
    if (wasCurrent) el("#m-title").textContent = body.title || el("#m-title").textContent;
  } catch (e) { alert("保存失败：" + e.message); }
  finally { btn.disabled = false; btn.textContent = "保存"; }
}

el("#meta-close").addEventListener("click", closeMetaDrawer);
el("#meta-cancel").addEventListener("click", closeMetaDrawer);
el("#meta-save").addEventListener("click", saveMeta);
el("#meta-drawer").addEventListener("click", (e) => { if (e.target.id === "meta-drawer") closeMetaDrawer(); });
el("#btn-edit-meta").addEventListener("click", () => { if (currentId) openMetaDrawer(currentId); });

// ---------- 上传弹窗 ----------
function openUploadModal() {
  el("#upload-msg").textContent = "";
  el("#upload-modal").hidden = false;
}
function closeUploadModal() { el("#upload-modal").hidden = true; }
el("#btn-upload-open").addEventListener("click", openUploadModal);
el("#upload-close").addEventListener("click", closeUploadModal);
el("#upload-modal").addEventListener("click", (e) => { if (e.target.id === "upload-modal") closeUploadModal(); });

el("#file").addEventListener("change", () => {
  const fs = el("#file").files;
  el("#file-name").textContent = !fs.length ? "点击选择音频 / 视频文件（可多选）"
    : fs.length === 1 ? fs[0].name : `已选择 ${fs.length} 个文件`;
  el("#file-label").classList.toggle("has-file", fs.length > 0);
  renderUploadList();
});

function renderUploadList() {
  const files = [...el("#file").files];
  const box = el("#upload-list");
  if (!files.length) { box.hidden = true; box.innerHTML = ""; return; }
  box.hidden = false;
  box.innerHTML = `<div class="up-head"><span>文件</span><span>说话人数</span></div>`
    + files.map((f, i) => `<div class="up-row">
        <span class="up-name" title="${esc(f.name)}">${esc(f.name)}</span>
        <input type="number" min="1" max="50" class="field field-sm up-spk" data-i="${i}" placeholder="自动">
      </div>`).join("");
}

el("#upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const files = [...el("#file").files];
  if (!files.length) { el("#upload-msg").textContent = "请先选择文件"; return; }
  const cat = el("#upload-category").value;
  const btn = el("#upload-btn");
  btn.disabled = true;
  let firstId = null, ok = 0;
  const fails = [];
  for (let i = 0; i < files.length; i++) {
    el("#upload-msg").textContent = `上传中 ${i + 1}/${files.length}…`;
    const fd = new FormData();
    fd.append("file", files[i]);
    if (cat) fd.append("category", cat);
    const spkEl = el(`.up-spk[data-i="${i}"]`);
    const spk = spkEl ? parseInt(spkEl.value, 10) : NaN;
    if (spk > 0) fd.append("spk_num", String(spk));
    try {
      const res = await api("/api/meetings", { method: "POST", body: fd });
      if (!firstId) firstId = res.meeting_id;
      ok++;
    } catch (err) {
      fails.push(`${files[i].name}：${err.message}`);
    }
  }
  btn.disabled = false;
  el("#file").value = "";
  el("#file-name").textContent = "点击选择音频 / 视频文件（可多选）";
  el("#file-label").classList.remove("has-file");
  renderUploadList();
  if (fails.length) {
    el("#upload-msg").textContent = `成功 ${ok} 个，失败 ${fails.length} 个：${fails.join("；")}`;
    await loadLibrary();
    return;
  }
  // 全部成功：单个直接进详情，多个回到录音库看队列
  closeUploadModal();
  el("#upload-msg").textContent = "";
  if (files.length === 1 && firstId) { openMeeting(firstId); refreshQueue(); }
  else showLibrary();
});

// ---------- 热词 ----------
let hwDraft = [];

async function loadHotwords() {
  try { const d = await api("/api/hotwords"); hotwords = d.hotwords || []; }
  catch (e) { hotwords = []; }
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
    closeHotwordModal();
  } catch (e) { alert("保存失败：" + e.message); }
  finally { btn.disabled = false; }
}

el("#btn-hotword-open").addEventListener("click", openHotwordModal);
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
    el("#hw-bulk-text").value = hwDraft.join("\n");
    bulk.hidden = false;
    el("#hw-chips").hidden = true;
    el("#hw-bulk-toggle").textContent = "完成编辑";
  } else {
    hwApplyBulk();
  }
});
el("#hw-bulk-apply").addEventListener("click", hwApplyBulk);

// ---------- 声纹库 ----------
async function loadVoiceprints() {
  try { const d = await api("/api/voiceprints"); voiceprints = d.voiceprints || []; }
  catch (e) { voiceprints = []; }
  renderVoiceprints();
}
function renderVoiceprints() {
  const box = el("#voiceprint-list");
  if (!box) return;
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
el("#btn-voiceprint-open").addEventListener("click", () => { el("#voiceprint-modal").hidden = false; });
el("#vp-close").addEventListener("click", () => { el("#voiceprint-modal").hidden = true; });
el("#voiceprint-modal").addEventListener("click", (e) => { if (e.target.id === "voiceprint-modal") el("#voiceprint-modal").hidden = true; });

// ---------- 模型初始化（预热） ----------
let warmupTimer = null;

function renderWarmup(st) {
  const btn = el("#btn-warmup");
  if (!btn) return;
  btn.classList.remove("warmup-ready", "warmup-loading", "warmup-failed");
  if (st.loaded || st.status === "ready") {
    btn.textContent = "模型就绪 ✓";
    btn.classList.add("warmup-ready");
    btn.disabled = true;
    btn.title = `本地转写模型已加载（device=${st.device || "?"}），第一场会议可直接转写`;
  } else if (st.status === "loading") {
    btn.textContent = "模型加载中…";
    btn.classList.add("warmup-loading");
    btn.disabled = true;
    btn.title = "正在下载/加载本地模型（首次约 1.3GB）；此时可照常上传，转写会排队等模型就绪";
  } else if (st.status === "failed") {
    btn.textContent = "初始化失败 ⟳";
    btn.classList.add("warmup-failed");
    btn.disabled = false;
    btn.title = (st.message || "加载失败") + "（点击重试）";
  } else {
    btn.textContent = "初始化模型";
    btn.disabled = false;
    btn.title = "提前下载并加载本地转写模型，避免第一场会议才开始下载";
  }
}

function warmupPolling(on) {
  if (on && !warmupTimer) warmupTimer = setInterval(refreshWarmup, 3000);
  else if (!on && warmupTimer) { clearInterval(warmupTimer); warmupTimer = null; }
}

async function refreshWarmup() {
  try {
    const st = await api("/api/warmup");
    renderWarmup(st);
    warmupPolling(st.status === "loading");
  } catch (e) { /* 静默：初始化状态不影响主流程 */ }
}

async function startWarmup() {
  el("#btn-warmup").disabled = true;
  try {
    const st = await api("/api/warmup", { method: "POST" });
    renderWarmup(st);
    warmupPolling(st.status === "loading");
  } catch (e) {
    alert("初始化失败：" + e.message);
    refreshWarmup();
  }
}
el("#btn-warmup").addEventListener("click", startWarmup);

// ---------- 选择 / 轮询 ----------
async function selectMeeting(id) {
  currentId = id;
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  el("#error-area").innerHTML = "";
  el("#result").hidden = true;
  el("#audio-card").hidden = true;
  el("#processing").hidden = true;

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
  badge.className = "badge " + statusClass(m.status);
  const sel = el("#m-category-sel");
  // 当前分类若不在库里（被删过/历史值），临时补一个选项以正确显示
  if (m.category && !categoryNames().includes(m.category)) {
    sel.insertAdjacentHTML("beforeend", `<option value="${esc(m.category)}">${esc(m.category)}</option>`);
  }
  sel.value = m.category || "";
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
    badge.className = "badge " + (RUNNING.has(s.status) ? "running" : statusClass(s.status));
    if (s.status === "completed") {
      clearInterval(pollTimer); pollTimer = null;
      el("#processing").hidden = true;
      renderHeader(await api(`/api/meetings/${id}`));
      await renderResults(id);
    } else if (s.status === "failed") {
      clearInterval(pollTimer); pollTimer = null;
      showError(s);
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
      if (path === "title") { el("#m-title").textContent = currentSummary.title; loadLibrary(); }
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
        category: el("#m-category-sel").value,
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
  try { await api(`/api/meetings/${currentId}`, { method: "DELETE" }); showLibrary(); }
  catch (e) { alert("删除失败：" + e.message); }
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
    loadLibrary();
  });
});

// ---------- 全局 Esc 关闭弹窗 / 抽屉 ----------
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!el("#meta-drawer").hidden) closeMetaDrawer();
  else if (!el("#hotword-modal").hidden) closeHotwordModal();
  else if (!el("#category-modal").hidden) closeCategoryModal();
  else if (!el("#upload-modal").hidden) closeUploadModal();
  else if (!el("#queue-modal").hidden) el("#queue-modal").hidden = true;
  else if (!el("#voiceprint-modal").hidden) el("#voiceprint-modal").hidden = true;
});

// ---------- init ----------
tagChips = makeChipEditor("#meta-tags", "#meta-tag-input");
loadHotwords();
loadVoiceprints();
refreshWarmup();
showLibrary();
