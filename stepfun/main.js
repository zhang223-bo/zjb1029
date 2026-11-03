// ===========================================================================
// 迷你知识库 · 前端
// - 文档与查询的 embedding 全部在本浏览器内完成（Transformers.js v4 + ONNX Web/WASM）
// - 模型：Xenova/bge-small-zh-v1.5（BAAI bge-small-zh-v1.5 的 ONNX 移植，512 维）
// - 优先 WebGPU + FP16；不可用时自动降级 WASM + INT8（q8），并在界面明确提示
// - 向量与原文写入 seekdb，混合检索（全文 + 向量，RRF）由 seekdb 库内完成
// ===========================================================================
import { env, pipeline } from "@huggingface/transformers";

// 模型从魔搭社区下载（国内网络可达），缓存由浏览器 Cache API 管理
env.remoteHost = "https://www.modelscope.cn/";
env.remotePathTemplate = "{model}/resolve/master/";
// WASM 运行时文件本地托管（/ort/），避免外网依赖
env.backends.onnx.wasm.wasmPaths = "/ort/";

const MODEL = "Xenova/bge-small-zh-v1.5";
const DIM = 512;
const CHUNK_MAX = 400;   // 单块最大字符数
const CHUNK_OVERLAP = 50;

const SAMPLE_DOCS = [
  "seekdb 是 OceanBase 推出的 AI 原生搜索数据库，在同一个引擎里同时支持关系表、向量、全文、JSON 和 GIS 数据，向量索引支持 HNSW 与 IVF，全文检索基于 BM25 相关性排序。",
  "seekdb 的混合检索把向量语义搜索和关键词全文搜索放进一条 SQL，通过加权分数或 RRF（倒数排名融合）把两路排序融合成统一排名，不需要在应用层分别取两路结果再手工拼合。",
  "seekdb 支持嵌入式与客户端/服务器两种部署模式。嵌入式模式以轻量库的形式直接嵌入应用进程，适合个人开发者与边缘设备；服务器模式基于 OceanBase 引擎，满足生产环境的并发与事务要求。",
  "bge-small-zh-v1.5 是智源开源的中文文本嵌入模型，输出 512 维向量，参数量约 24M，适合在端侧设备运行，在 C-MTEB 中文语义检索基准上有不错的表现。",
  "模型量化把 FP32 权重压成 INT8，模型体积大约缩小到四分之一，推理时用整数运算替代浮点运算，可以在几乎不损失检索质量的前提下显著降低内存占用与计算开销。",
  "WebGPU 是浏览器中暴露的现代 GPU 通用计算接口，让网页可以直接调用显卡进行大规模并行计算，比 WebAssembly 有更高的推理吞吐；不支持 WebGPU 的环境可以退回 WASM SIMD 路径。",
  "Transformers.js 让开发者直接在浏览器里运行 ONNX 格式的 Transformer 模型，文档向量化和查询向量化都在用户设备本地完成，文本内容不出本机，天然满足隐私要求。",
  "倒排索引是全文检索的基础数据结构：对文档分词后，把每个词映射到包含它的文档列表，检索时只需遍历查询词对应的倒排链，即可快速找出所有包含关键词的文档。",
  "HNSW（分层可导航小世界图）是目前最常用的向量索引之一，通过多层跳跃图结构把近似最近邻搜索的复杂度降到对数级别，支持高维向量的高效语义检索。",
  "RRF 倒数排名融合把多路检索结果按排名倒数求和：每篇文档在每一路结果中的贡献是 1/(k+rank)，排名越靠前贡献越大，融合分数越高，最终按融合分数重新排序。",
  "增量写入要求新文档在索引刷新完成后立即可检索。seekdb 的写入是事务性提交，向量与全文索引随写入同步维护，应用层只需要在写入完成后验证数据可见性。",
  "批量处理是端侧推理的关键优化：把多条文本组成一个 batch 一次送入模型，可以摊薄单条推理的固定开销，在 WebGPU 上还能提高 GPU 并行利用率，通常能把吞吐提升数倍。",
];

// ---------------------------------------------------------------------------
// DOM 与状态
// ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);
const statusEl = $("status");
let extractor = null;
let backend = null; // { device, dtype, label }
let perf = new Map(); // stage -> number[]
let activeCollection = localStorage.getItem("kb.collection") || "kb_demo";

// ---------------------------------------------------------------------------
// 性能统计
// ---------------------------------------------------------------------------
function median(arr) {
  if (!arr.length) return null;
  const s = [...arr].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function record(stage, ms) {
  if (!perf.has(stage)) perf.set(stage, []);
  perf.get(stage).push(Math.round(ms * 100) / 100);
  renderPerf();
}

function renderPerf() {
  const table = $("perf-table");
  const tbody = table.querySelector("tbody");
  tbody.replaceChildren();
  const labels = {
    "model_load": "模型下载与加载",
    "import_embed_batch": "文档向量化吞吐",
    "query_embed": "查询向量化",
    "db_write": "seekdb 写入",
    "db_search": "seekdb 混合检索",
    "e2e_query": "端到端查询延迟",
  };
  for (const [stage, label] of Object.entries(labels)) {
    const arr = perf.get(stage) || [];
    if (!arr.length) continue;
    const tr = document.createElement("tr");
    const last = arr[arr.length - 1];
    const med = median(arr);
    const unit = stage === "import_embed_batch" ? " docs/s" : " ms";
    tr.innerHTML = `<td>${label}</td><td>${last}${unit}</td><td>${med}${unit}</td><td>${arr.length}</td>`;
    tbody.appendChild(tr);
  }
  table.classList.toggle("hidden", tbody.children.length === 0);
}

// ---------------------------------------------------------------------------
// 模型加载：WebGPU + FP16 优先，失败自动降级 WASM + INT8
// ---------------------------------------------------------------------------
function setBackendBadge(cls, text) {
  const el = $("backend-badge");
  el.className = "badge " + cls;
  el.textContent = text;
}

async function tryLoad(device, dtype) {
  const t0 = performance.now();
  const ext = await pipeline("feature-extraction", MODEL, {
    device,
    dtype,
    progress_callback: (e) => {
      if (e && e.status === "progress" && e.total) {
        const pct = Math.round((e.loaded / e.total) * 100);
        $("model-progress-label").textContent = `下载 ${e.file || "模型文件"} ${pct}%`;
        $("model-progress-bar").style.width = pct + "%";
      }
    },
  });
  record("model_load", performance.now() - t0);
  return ext;
}

async function loadModel() {
  $("model-progress-wrap").classList.remove("hidden");
  const hasApi = typeof navigator !== "undefined" && navigator.gpu;
  let adapter = null;
  if (hasApi) {
    try { adapter = await navigator.gpu.requestAdapter(); } catch { /* ignore */ }
  }

  const attempts = [];
  if (adapter) {
    attempts.push(
      { device: "webgpu", dtype: "fp16", label: "WebGPU (FP16)", badge: "webgpu" },
      { device: "webgpu", dtype: "q8", label: "WebGPU (INT8 q8)", badge: "webgpu" },
    );
  }
  attempts.push({ device: "wasm", dtype: "q8", label: "WASM (INT8 q8)", badge: "wasm" });

  const errors = [];
  for (const a of attempts) {
    try {
      statusEl.textContent = `正在初始化推理后端：${a.label} …`;
      extractor = await tryLoad(a.device, a.dtype);
      backend = a;
      const note = a.device === "wasm" && adapter === null
        ? "（本浏览器无 WebGPU，已自动降级）"
        : "";
      setBackendBadge(a.badge, `推理后端：${a.label} · ${MODEL}${note}`);
      statusEl.textContent = `模型就绪。后端 ${a.label}，维度 ${DIM}，向量化与查询均在本地浏览器完成。${note}`;
      $("model-progress-wrap").classList.add("hidden");
      return;
    } catch (e) {
      errors.push(`${a.label}: ${String(e && e.message || e).slice(0, 120)}`);
    }
  }
  setBackendBadge("error", "推理后端：加载失败");
  statusEl.textContent = "模型加载失败：" + errors.join(" | ");
  throw new Error("所有推理后端均不可用");
}

// ---------------------------------------------------------------------------
// 文本切分与向量化
// ---------------------------------------------------------------------------
function splitChunks(text, title) {
  const paras = text.split(/\n\s*\n/).map((s) => s.trim()).filter(Boolean);
  const chunks = [];
  for (const p of paras) {
    if (p.length <= CHUNK_MAX) {
      chunks.push({ text: p, title });
      continue;
    }
    for (let i = 0; i < p.length; i += CHUNK_MAX - CHUNK_OVERLAP) {
      const piece = p.slice(i, i + CHUNK_MAX).trim();
      if (piece) chunks.push({ text: piece, title });
      if (i + CHUNK_MAX >= p.length) break;
    }
  }
  return chunks;
}

// FNV-1a 32bit：同一文本得到稳定 id，重复导入自动去重（upsert）
function hashId(text) {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return "d" + (h >>> 0).toString(16).padStart(8, "0");
}

async function embedBatch(texts) {
  const t0 = performance.now();
  const output = await extractor(texts, { pooling: "mean", normalize: true });
  const vectors = output.tolist();
  record("import_embed_batch", texts.length / ((performance.now() - t0) / 1000)); // docs/s
  return vectors;
}

// ---------------------------------------------------------------------------
// API 封装
// ---------------------------------------------------------------------------
async function api(path, body) {
  const res = await fetch(path, body ? {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  } : undefined);
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);
  return payload;
}

// ---------------------------------------------------------------------------
// 集合管理
// ---------------------------------------------------------------------------
async function refreshCollections(keep) {
  const { collections } = await api("/api/collections");
  const sel = $("collection-select");
  sel.replaceChildren();
  for (const c of collections) {
    const opt = document.createElement("option");
    opt.value = c.name;
    opt.textContent = `${c.name}（${c.count ?? "?"} 条）`;
    sel.appendChild(opt);
  }
  if (keep && collections.some((c) => c.name === keep)) activeCollection = keep;
  if (!collections.some((c) => c.name === activeCollection)) {
    activeCollection = collections[0] ? collections[0].name : activeCollection;
  }
  sel.value = activeCollection;
  localStorage.setItem("kb.collection", activeCollection);
  await refreshDocList();
}

async function refreshDocList() {
  try {
    const { rows } = await api(`/api/docs?collection=${encodeURIComponent(activeCollection)}&limit=100`);
    const list = $("doc-list");
    list.replaceChildren();
    const docs = (rows && rows.documents) || [];
    const ids = (rows && rows.ids) || [];
    $("doc-count").textContent = docs.length ? `（显示前 ${docs.length} 条）` : "（空）";
    docs.forEach((text, i) => {
      const li = document.createElement("li");
      li.textContent = `${ids[i] || i} · ${String(text).slice(0, 80)}`;
      li.title = text;
      list.appendChild(li);
    });
    if (!docs.length) {
      const li = document.createElement("li");
      li.className = "hint";
      li.textContent = "还没有文档，先在上方导入。";
      list.appendChild(li);
    }
  } catch (e) {
    $("doc-list").innerHTML = `<li class="hint">加载失败：${e.message}</li>`;
  }
}

// ---------------------------------------------------------------------------
// 导入流程：切分 → 分批端侧向量化 → upsert 写入 seekdb → 索引可见性验证
// ---------------------------------------------------------------------------
function collectInputDocs() {
  const items = [];
  const ta = $("import-text").value.trim();
  if (ta) {
    splitChunks(ta, "粘贴文本").forEach((c) => items.push(c));
  }
  return items;
}

async function importDocs(items) {
  if (!items.length) throw new Error("没有可导入的内容");
  const batchSize = Number($("batch-size").value);
  const wrap = $("import-progress-wrap");
  const bar = $("import-progress-bar");
  const label = $("import-progress-label");
  wrap.classList.remove("hidden");

  const t0 = performance.now();
  let done = 0;
  const pending = [];
  for (const c of items) {
    pending.push({
      id: hashId(c.text),
      text: c.text,
      metadata: { title: c.title || "", importedAt: new Date().toISOString() },
    });
  }

  // 分批向量化（端侧推理的主要耗时阶段）
  for (let i = 0; i < pending.length; i += batchSize) {
    const batch = pending.slice(i, i + batchSize);
    const vectors = await embedBatch(batch.map((b) => b.text));
    batch.forEach((b, j) => { b.embedding = vectors[j]; });
    done += batch.length;
    const sec = (performance.now() - t0) / 1000;
    label.textContent = `向量化进度 ${done}/${pending.length}（${(done / sec).toFixed(1)} docs/s）`;
    bar.style.width = Math.round((done / pending.length) * 100) + "%";
  }
  const embedMs = performance.now() - t0;

  // 写入 seekdb（upsert：同文本去重，增量导入）
  const writeRes = await api("/api/docs", { collection: activeCollection, items: pending });
  record("db_write", writeRes.dbMs);

  // 索引刷新/可见性验证
  const refreshRes = await api("/api/refresh", { collection: activeCollection });

  $("import-result").classList.remove("hidden");
  $("import-result").innerHTML = `
    <span>条目 <b>${pending.length}</b>（原始段 → 切分后）</span>
    <span>向量化 <b>${Math.round(embedMs)} ms</b></span>
    <span>写入 <b>${writeRes.dbMs} ms</b>（${writeRes.writeFn}）</span>
    <span>刷新验证 <b>${refreshRes.refreshMs} ms</b>，库内共 <b>${refreshRes.count}</b> 条</span>`;
  statusEl.textContent = `导入完成：${pending.length} 条已写入 seekdb 并可检索。当前后端：${backend.label}`;

  wrap.classList.add("hidden");
  await refreshCollections(activeCollection);
}

// ---------------------------------------------------------------------------
// 检索：查询向量本地计算 → seekdb 库内混合检索（全文 + 向量 RRF）
// ---------------------------------------------------------------------------
function extractKeyword(query) {
  // 提取全文检索用关键词：去掉常见疑问词与虚词，保留核心词
  let s = query.replace(/[，。！？、；：""''（）()《》\s]+/g, " ").trim();
  const stop = /(什么|哪些|怎么|怎样|如何|为什么|多少|请问|帮我|介绍一下|介绍|一下|还是|以及|关于|找出|查找|搜索|检索|的|了|吗|呢|吧|啊|是|有|和|与|或|对|把|被|向|从|在)/g;
  s = s.replace(stop, " ");
  const terms = s.split(/\s+/).filter((t) => t.length >= 2 || /[a-zA-Z0-9]/.test(t));
  return terms.slice(0, 4).join(" ").trim() || null;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function highlight(text, keyword) {
  const safe = escapeHtml(text);
  if (!keyword) return safe;
  const terms = keyword.split(/\s+/).filter(Boolean).map(escapeHtml);
  let out = safe;
  for (const t of terms) {
    out = out.replace(new RegExp(t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g"), `<mark>${t}</mark>`);
  }
  return out;
}

async function search() {
  const query = $("query").value.trim();
  if (!query) return;
  const t0 = performance.now();
  $("btn-search").disabled = true;

  try {
    // 1) 查询向量：本地端侧计算
    const qv = (await embedBatch([query]))[0];
    const queryEmbedMs = performance.now() - t0;
    record("query_embed", queryEmbedMs);

    // 2) seekdb 库内混合检索
    const keyword = extractKeyword(query);
    const res = await api("/api/search", {
      collection: activeCollection,
      keyword,
      embedding: qv,
      nResults: Number($("topk").value),
    });
    record("db_search", res.dbMs);
    record("e2e_query", performance.now() - t0);

    // 3) 渲染
    const r = res.result || {};
    const docs = (r.documents && r.documents[0]) || [];
    const ids = (r.ids && r.ids[0]) || [];
    const distances = (r.distances && r.distances[0]) || [];
    $("search-timing").classList.remove("hidden");
    $("search-timing").innerHTML = `
      <span>查询向量化 <b>${queryEmbedMs.toFixed(1)} ms</b></span>
      <span>seekdb 混合检索 <b>${res.dbMs} ms</b></span>
      <span>端到端 <b>${(performance.now() - t0).toFixed(1)} ms</b></span>
      <span>检索方式 <b>${res.hybrid ? "全文+向量 RRF 融合" : "纯向量（无可用关键词）"}</b></span>`;
    const hits = $("hits");
    hits.replaceChildren();
    if (!docs.length) {
      hits.innerHTML = `<li class="empty">没有命中结果，试试导入更多内容。</li>`;
    }
    docs.forEach((text, i) => {
      const li = document.createElement("li");
      const score = distances[i] != null ? Number(distances[i]).toFixed(4) : "";
      li.innerHTML = `<span class="score">#${i + 1}${score ? ` · ${score}` : ""}</span>${highlight(String(text), keyword)}
        <span class="meta">id: ${ids[i] || "-"}</span>`;
      hits.appendChild(li);
    });
    statusEl.textContent = `检索完成（${docs.length} 条结果）。当前后端：${backend.label}`;
  } finally {
    $("btn-search").disabled = false;
  }
}

// ---------------------------------------------------------------------------
// 事件绑定
// ---------------------------------------------------------------------------
function setModelReady(on) {
  for (const id of ["btn-import", "btn-sample", "btn-search"]) $(id).disabled = !on;
}

$("collection-select").onchange = (e) => {
  activeCollection = e.target.value;
  localStorage.setItem("kb.collection", activeCollection);
  refreshDocList();
};

$("btn-new-collection").onclick = async () => {
  const name = prompt("新集合名（字母开头，字母/数字/下划线）：", "kb_" + Date.now().toString(36));
  if (!name) return;
  try {
    await api("/api/collections", { collection: name.trim() });
    activeCollection = name.trim();
    await refreshCollections(activeCollection);
  } catch (e) { alert(e.message); }
};

$("btn-del-collection").onclick = async () => {
  if (!confirm(`确认删除集合 ${activeCollection} 及其全部数据？`)) return;
  try {
    await api("/api/collections/delete", { collection: activeCollection });
    activeCollection = "kb_demo";
    await refreshCollections(activeCollection);
  } catch (e) { alert(e.message); }
};

$("btn-import").onclick = async () => {
  $("btn-import").disabled = true;
  try {
    const items = collectInputDocs();
    if (!items.length) { alert("请先粘贴文本或选择文件"); return; }
    await importDocs(items);
  } catch (e) {
    statusEl.textContent = "导入失败：" + e.message;
    alert("导入失败：" + e.message);
  } finally {
    setModelReady(true);
  }
};

$("import-files").onchange = async () => {
  const files = [...$("import-files").files];
  if (!files.length) return;
  const parts = [];
  for (const f of files) {
    const text = await f.text();
    parts.push(`# ${f.name}\n\n${text}`);
  }
  $("import-text").value = ($("import-text").value ? $("import-text").value + "\n\n" : "") + parts.join("\n\n");
  $("import-files").value = "";
};

$("btn-sample").onclick = async () => {
  $("btn-sample").disabled = true;
  try {
    const items = SAMPLE_DOCS.map((text) => ({ text, title: "示例语料" }));
    await importDocs(items);
  } catch (e) {
    statusEl.textContent = "导入失败：" + e.message;
  } finally { setModelReady(true); }
};

$("btn-clear-input").onclick = () => { $("import-text").value = ""; };
$("btn-search").onclick = search;
$("query").addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
$("btn-reset-perf").onclick = () => { perf = new Map(); renderPerf(); $("perf-summary").innerHTML = `<span class="hint">统计已清空</span>`; };

// ---------------------------------------------------------------------------
// 启动
// ---------------------------------------------------------------------------
(async function init() {
  try {
    const health = await api("/api/health");
    $("db-badge").textContent = `seekdb：${health.seekdb}（${health.mode}）`;
  } catch (e) {
    $("db-badge").className = "badge error";
    $("db-badge").textContent = "seekdb：连接失败，请先启动 server.mjs";
  }
  await refreshCollections(activeCollection).catch(() => {});
  try {
    await loadModel();
    setModelReady(true);
  } catch { /* 状态栏已提示 */ }
})();
