const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const STATUS_LABEL = { unread: "未读", reading: "在读", read: "已读" };
let MODE = "hybrid";

/* ---------------- 基础工具 ---------------- */
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = { raw: text }; }
  if (!res.ok) {
    const msg = (data && (data.detail || data.error)) || `请求失败 ${res.status}`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

let toastTimer = null;
function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2400);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function highlight(text, terms) {
  let out = esc(text);
  (terms || []).filter(Boolean).forEach((t) => {
    const safe = esc(t);
    if (!safe) return;
    out = out.split(safe).join(`<em>${safe}</em>`);
  });
  return out;
}

/* ---------------- 顶部状态 ---------------- */
async function loadHealth() {
  try {
    const h = await api("/api/health");
    const sd = $("#pill-seekdb");
    sd.classList.add(h.seekdb.ok ? "ok" : "bad");
    sd.innerHTML = `<i class="dot"></i>seekdb ${h.seekdb.docs ?? 0} 文献 / ${h.seekdb.chunks ?? 0} 片段`;

    const pc = $("#pill-pc");
    pc.classList.add(h.powercontext.ok ? "ok" : "bad");
    pc.innerHTML = `<i class="dot"></i>PowerContext ${h.powercontext.ok ? "已连接" : "未连接"}`;
    pc.title = h.powercontext.ok ? h.powercontext.base_url : (h.powercontext.detail || "");

    $("#pill-scope").textContent = h.powercontext.scope_id
      ? `scope ${h.powercontext.scope_id.slice(0, 16)}…`
      : "scope 未建立";

    $("#s-docs").textContent = h.seekdb.docs ?? 0;
    $("#s-chunks").textContent = h.seekdb.chunks ?? 0;
    $("#s-searches").textContent = h.seekdb.searches ?? 0;
  } catch (e) {
    toast("无法连接后端：" + e.message);
  }
}

/* ---------------- 研究画像 ---------------- */
async function loadProfile() {
  try {
    const d = await api("/api/profile");
    const p = d.profile || {};
    $("#p-direction").value = p.direction || "";
    $("#p-focus").value = (p.focus || []).join(", ");
    $("#p-unread").checked = !!p.prefer_unread;
    $("#profile-source").textContent = d.artifact && d.artifact.artifact_id
      ? `memory ${String(d.artifact.artifact_id).slice(4, 12)}`
      : "memory";
  } catch (e) {
    $("#profile-source").textContent = "记忆未连接";
    $("#profile-hint").textContent = "PowerContext 未连接，画像暂不可用：" + e.message;
  }
}

async function saveProfile() {
  const body = {
    direction: $("#p-direction").value.trim(),
    focus: $("#p-focus").value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
    prefer_unread: $("#p-unread").checked,
  };
  try {
    await api("/api/profile", { method: "PUT", body: JSON.stringify(body) });
    toast("画像已写入 PowerContext，后续检索会读到它");
    loadProfile();
  } catch (e) {
    toast("保存失败：" + e.message);
  }
}

async function loadNotes() {
  try {
    const d = await api("/api/radar?limit=0");
    const box = $("#recent-notes");
    const notes = d.recent_notes || [];
    box.innerHTML = notes.length
      ? notes.map((n) => `<li>${esc(n)}</li>`).join("")
      : `<li class="muted tiny">还没有检索记录，先搜一次试试。</li>`;
  } catch (e) {
    $("#recent-notes").innerHTML = `<li class="muted tiny">记忆不可用：${esc(e.message)}</li>`;
  }
}

/* ---------------- 检索 ---------------- */
function currentFilters() {
  const f = {};
  const yf = $("#year-from").value; if (yf) f.year_from = Number(yf);
  const yt = $("#year-to").value; if (yt) f.year_to = Number(yt);
  const kind = $("#kind").value; if (kind) f.kind = kind;
  const st = $("#status-filter").value; if (st) f.status = st;
  return f;
}

async function doSearch() {
  const q = $("#q").value.trim();
  if (!q) return toast("先写一句你要找的东西");
  const btn = $("#btn-search");
  btn.disabled = true;
  btn.textContent = "检索中…";
  const box = $("#results");
  try {
    const d = await api("/api/search", {
      method: "POST",
      body: JSON.stringify({ query: q, mode: MODE, filters: currentFilters() }),
    });
    renderResults(d);
    $("#search-hint").textContent =
      `${MODE === "hybrid" ? "混合检索" : MODE === "vector" ? "仅向量" : "仅全文"} · 候选 ${d.candidates} 条片段 · ` +
      `seekdb 耗时 ${d.elapsed_ms} ms${d.fallback ? "（全文条件过严，已补一轮向量召回）" : ""}` +
      `${d.memory && d.memory.ok ? " · 已读你的研究画像" : " · 记忆未连接，未使用画像"}`;
    loadNotes();
    loadHealth();
  } catch (e) {
    box.innerHTML = `<div class="card empty">检索失败：${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "检索";
  }
}

function stripTagPrefix(text) {
  return String(text || "").replace(/^\s*【.*?】\s*/, "");
}

function renderResults(d) {
  const box = $("#results");
  const focus = (d.memory && d.memory.profile && d.memory.profile.focus) || [];
  if (!d.docs || !d.docs.length) {
    box.innerHTML = `<div class="card empty">没有命中。试试放宽年份或状态过滤，或者换一种说法。</div>`;
    return;
  }
  box.innerHTML = d.docs.map((doc) => {
    const hit = (doc.hits && doc.hits[0]) || {};
    const scores = (hit.scores) || {};
    const chips = [];
    if (scores.final != null) chips.push(`<span class="score">融合分 ${scores.final.toFixed(3)}</span>`);
    if (scores.vector != null) chips.push(`<span class="score dim">语义 ${scores.vector.toFixed(3)}</span>`);
    const ftShow = scores.fulltext_norm != null ? scores.fulltext_norm : scores.fulltext;
    if (ftShow != null) chips.push(`<span class="score dim">全文 ${ftShow.toFixed(3)}</span>`);
    if (hit.memory_boost) chips.push(`<span class="score dim">记忆加权 +${hit.memory_boost.toFixed(3)}</span>`);

    const extra = (doc.hits || []).slice(1).map((h) => `
      <div class="snippet" style="border-left-color:#e6e8ee">
        <span class="tiny muted">${esc(h.section || "正文")}${h.page ? " · p." + h.page : ""}</span><br>
        ${esc(h.content).slice(0, 260)}…
      </div>`).join("");

    return `
      <article class="card result">
        <div class="title">${doc.url ? `<a href="${esc(doc.url)}" target="_blank" rel="noreferrer">${esc(doc.title)}</a>` : esc(doc.title)}</div>
        <div class="meta">
          <span>${esc(doc.authors || "")}</span>
          <span>${esc(doc.venue || "")}${doc.year ? " · " + doc.year : ""}</span>
          <span class="status-badge status-${doc.status}">${STATUS_LABEL[doc.status] || doc.status}</span>
          ${doc.kind === "note" ? `<span class="tag">我的笔记</span>` : ""}
        </div>
        <div class="snippet">${hit.section ? `<span class="tiny muted">${esc(hit.section)}${hit.page ? " · p." + hit.page : ""}</span><br>` : ""}${highlight(stripTagPrefix((hit.content || "").slice(0, 320)), focus)}</div>
        ${extra}
        <div class="scores">${chips.join("")}</div>
        <div class="tags">${(doc.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
        <div class="why">为什么排在这：${esc(doc.reason || "")}</div>
        <div class="result-foot">
          <div class="actions">
            <button data-doc="${esc(doc.doc_id)}" data-title="${esc(doc.title)}" data-status="read" class="${doc.status === "read" ? "on" : ""}">标记已读</button>
            <button data-doc="${esc(doc.doc_id)}" data-title="${esc(doc.title)}" data-status="reading" class="${doc.status === "reading" ? "on" : ""}">在读</button>
            <button data-doc="${esc(doc.doc_id)}" data-title="${esc(doc.title)}" data-status="unread" class="${doc.status === "unread" ? "on" : ""}">未读</button>
          </div>
          <span class="tiny muted">${esc(doc.doc_id)} · ${(doc.hits || []).length} 个命中片段</span>
        </div>
      </article>`;
  }).join("");
  bindStatusButtons(box);
}

function bindStatusButtons(root) {
  root.querySelectorAll("button[data-doc]").forEach((btn) => {
    btn.addEventListener("click", () => setStatus(btn.dataset.doc, btn.dataset.title, btn.dataset.status));
  });
}

async function setStatus(docId, title, status) {
  try {
    const d = await api(`/api/docs/${encodeURIComponent(docId)}/status`, {
      method: "POST",
      body: JSON.stringify({ status }),
    });
    toast(d.memory_ok ? `「${title.slice(0, 18)}」已标记为${STATUS_LABEL[status]}，写入记忆` : "已更新本地状态，但记忆写入失败");
    doSearch();
  } catch (e) {
    toast("更新失败：" + e.message);
  }
}

/* ---------------- 每日雷达 ---------------- */
async function loadRadar() {
  const box = $("#radar-list");
  box.innerHTML = `<div class="card empty">正在根据你的画像召回…</div>`;
  try {
    const d = await api("/api/radar?limit=6");
    const c = d.counts || {};
    $("#radar-sub").textContent =
      `按画像「${d.profile.direction || "未设置"}」召回；库内 ${c.total || 0} 篇，未读 ${c.unread || 0} 篇` +
      (d.fallback_note ? `（${d.fallback_note}）` : "");
    if (!d.items.length) {
      box.innerHTML = `<div class="card empty">没有可推荐的文献，先往库里加点资料。</div>`;
      return;
    }
    box.innerHTML = d.items.map((it) => `
      <article class="card result">
        <div class="title">${esc(it.title)}</div>
        <div class="meta">
          <span>${esc(it.venue || "")}${it.year ? " · " + it.year : ""}</span>
          <span class="status-badge status-${it.status}">${STATUS_LABEL[it.status] || it.status}</span>
        </div>
        <div class="snippet">${esc(stripTagPrefix(it.snippet))}…</div>
        <div class="tags">${(it.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
        <div class="why">推荐理由：${esc(it.why)}</div>
        <div class="result-foot">
          <div class="actions">
            <button data-doc="${esc(it.doc_id)}" data-title="${esc(it.title)}" data-status="reading" class="${it.status === "reading" ? "on" : ""}">开始读</button>
            <button data-doc="${esc(it.doc_id)}" data-title="${esc(it.title)}" data-status="read" class="${it.status === "read" ? "on" : ""}">已读</button>
          </div>
          <span class="tiny muted">相关性 ${it.score}</span>
        </div>
      </article>`).join("");
    bindStatusButtons(box);
  } catch (e) {
    box.innerHTML = `<div class="card empty">加载失败：${esc(e.message)}</div>`;
  }
}

/* ---------------- 文献库 ---------------- */
async function loadLibrary() {
  const box = $("#library-list");
  try {
    const d = await api("/api/docs");
    $("#lib-total").textContent = d.items.length;
    box.innerHTML = d.items.map((it) => `
      <div class="lib-row">
        <div>
          <div class="t" title="${esc(it.title)}">${esc(it.title)}</div>
          <div class="s">${esc(it.authors || "")}</div>
        </div>
        <div class="s">${it.year || ""}</div>
        <div class="s">${esc(it.venue || "")}</div>
        <div>
          <select data-doc="${esc(it.id)}" data-title="${esc(it.title)}">
            <option value="unread" ${it.status === "unread" ? "selected" : ""}>未读</option>
            <option value="reading" ${it.status === "reading" ? "selected" : ""}>在读</option>
            <option value="read" ${it.status === "read" ? "selected" : ""}>已读</option>
          </select>
        </div>
      </div>`).join("");
    box.querySelectorAll("select[data-doc]").forEach((sel) => {
      sel.addEventListener("change", () => setStatus(sel.dataset.doc, sel.dataset.title, sel.value));
    });
  } catch (e) {
    box.innerHTML = `<div class="card empty">加载失败：${esc(e.message)}</div>`;
  }
}

/* ---------------- 技术证据 ---------------- */
async function loadEvidence() {
  try {
    const d = await api("/api/evidence?limit=8");
    $("#evidence-searches").innerHTML = d.searches.length
      ? d.searches.map((s) => `
        <details class="ev-item" ${s === d.searches[0] ? "open" : ""}>
          <summary class="ev-head">
            <b>${esc(s.query).slice(0, 46)}</b>
            <span class="t">${esc(String(s.ts)).slice(5, 19)} · ${s.mode} · ${s.candidates} 候选 · ${Number(s.elapsed_ms).toFixed(1)} ms</span>
          </summary>
          ${s.sql_text ? `<pre class="sql">${esc(s.sql_text)}</pre>` : ""}
          <div class="ev-meta"><span>filters: ${esc(s.filters || "{}")}</span><span>memory: ${esc(s.memory_used || "—")}</span></div>
        </details>`).join("")
      : `<div class="empty">还没有检索记录</div>`;

    $("#evidence-memory").innerHTML = d.memory_trace.length
      ? d.memory_trace.map((t) => `
        <div class="ev-item">
          <div class="ev-head">
            <b>${esc(t.op)}</b>
            <span class="t">${esc(t.ts || "")}</span>
          </div>
          <div class="ev-meta">
            ${t.kind ? `<span>kind: ${esc(t.kind)}</span>` : ""}
            ${t.revision ? `<span>revision: ${esc(t.revision)}</span>` : ""}
            ${t.text ? `<span>${esc(String(t.text).slice(0, 120))}</span>` : ""}
          </div>
        </div>`).join("")
      : `<div class="empty">还没有记忆读写记录</div>`;

    $("#evidence-sql").innerHTML = d.sql_index.map((x) => `
      <details class="ev-item">
        <summary class="ev-head"><b>${esc(x.name)}</b></summary>
        <pre class="sql">${esc(x.sql)}</pre>
      </details>`).join("");
  } catch (e) {
    $("#evidence-searches").innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

/* ---------------- 交互绑定 ---------------- */
$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    $$(".view").forEach((v) => v.classList.remove("active"));
    const view = $("#view-" + tab.dataset.view);
    view.classList.add("active");
    if (tab.dataset.view === "radar") loadRadar();
    if (tab.dataset.view === "library") loadLibrary();
    if (tab.dataset.view === "evidence") loadEvidence();
  });
});

$$("#mode button").forEach((b) => {
  b.addEventListener("click", () => {
    $$("#mode button").forEach((x) => x.classList.remove("on"));
    b.classList.add("on");
    MODE = b.dataset.mode;
    doSearch();
  });
});

$("#btn-search").addEventListener("click", doSearch);
$("#q").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
$("#btn-radar").addEventListener("click", loadRadar);
$("#btn-save-profile").addEventListener("click", saveProfile);
["#year-from", "#year-to", "#kind", "#status-filter"].forEach((s) =>
  $(s).addEventListener("change", doSearch)
);

/* ---------------- 启动 ---------------- */
loadHealth();
loadProfile();
loadNotes();
doSearch();
