import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import {
  FulltextIndexConfig,
  Schema,
  SeekdbClient,
  VectorIndexConfig,
} from "seekdb";

// ---------------------------------------------------------------------------
// seekdb 连接配置
// 默认连接本地局域网内的 seekdb server（MySQL 兼容协议）。
// 也可切换为嵌入式模式：SEEKDB_MODE=embedded node server.mjs
// ---------------------------------------------------------------------------
const CFG = {
  mode: process.env.SEEKDB_MODE || "server",
  host: process.env.SEEKDB_HOST || "192.168.10.128",
  port: Number(process.env.SEEKDB_PORT || 2881),
  user: process.env.SEEKDB_USER || "root",
  password: process.env.SEEKDB_PASSWORD || "",
  database: process.env.SEEKDB_DATABASE || "test",
  path: process.env.SEEKDB_PATH || "./data/seekdb.db",
};

const DIM = Number(process.env.EMBED_DIM || 512); // bge-small-zh-v1.5 输出维度
const WRITE_BATCH = 64; // 写入分批大小
const collections = new Map(); // 集合连接缓存

function makeClient() {
  if (CFG.mode === "embedded") {
    fs.mkdirSync(path.dirname(CFG.path), { recursive: true });
    return new SeekdbClient({ path: CFG.path, database: CFG.database });
  }
  return new SeekdbClient({
    host: CFG.host,
    port: CFG.port,
    user: CFG.user,
    password: CFG.password,
    database: CFG.database,
  });
}

// 向量索引（HNSW, cosine）+ 全文索引（ik smart，支持中文分词）
// embeddingFunction 置空：向量一律由浏览器端侧推理产生后传入，
// 数据库只负责存储与检索，不承担向量化（满足“端侧推理”要求）。
const schema = new Schema({
  vectorIndex: new VectorIndexConfig({
    hnsw: { dimension: DIM, distance: "cosine" },
    embeddingFunction: null,
  }),
  fulltextIndex: new FulltextIndexConfig("ik", { ik_mode: "smart" }),
});

const client = makeClient();

async function openCollection(name) {
  if (collections.has(name)) return collections.get(name);
  let collection;
  try {
    collection = await client.getCollection({ name });
  } catch {
    collection = await client.createCollection({ name, schema });
  }
  collections.set(name, collection);
  return collection;
}

// ---------------------------------------------------------------------------
// HTTP 基础
// ---------------------------------------------------------------------------
function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch (e) {
        reject(new Error("请求体不是合法 JSON: " + e.message));
      }
    });
    req.on("error", reject);
  });
}

function send(res, code, data) {
  res.writeHead(code, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
  });
  res.end(JSON.stringify(data));
}

function collectionName(body) {
  const name = String(body.collection || "").trim();
  if (!/^[A-Za-z][A-Za-z0-9_]{0,30}$/.test(name)) {
    throw new Error("集合名必须以字母开头，只含字母、数字和下划线，长度不超过 31");
  }
  return name;
}

function chunk(arr, size) {
  const out = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

// ---------------------------------------------------------------------------
// 路由
// ---------------------------------------------------------------------------
const server = http.createServer(async (req, res) => {
  const started = performance.now();
  const dbMs = () => Math.round((performance.now() - started) * 100) / 100;

  if (req.method === "OPTIONS") return send(res, 204, {});

  try {
    // 健康检查：报告 seekdb 部署方式与维度
    if (req.method === "GET" && req.url === "/api/health") {
      return send(res, 200, {
        ok: true,
        mode: CFG.mode,
        seekdb: CFG.mode === "embedded"
          ? `embedded:${CFG.path}`
          : `${CFG.host}:${CFG.port}/${CFG.database}`,
        dim: DIM,
        writeBatch: WRITE_BATCH,
      });
    }

    // 集合列表（含文档数）
    if (req.method === "GET" && req.url === "/api/collections") {
      const names = await client.listCollections();
      const list = [];
      for (const raw of Array.isArray(names) ? names : []) {
        const name = typeof raw === "string" ? raw : raw && (raw.name || raw.collection_name);
        if (!name) continue;
        try {
          const col = await openCollection(name);
          const count = typeof col.count === "function" ? await col.count() : null;
          list.push({ name, count });
        } catch {
          list.push({ name, count: null });
        }
      }
      return send(res, 200, { collections: list, dbMs: dbMs() });
    }

    // 创建集合
    if (req.method === "POST" && req.url === "/api/collections") {
      const body = await readBody(req);
      const name = collectionName(body);
      await openCollection(name);
      return send(res, 200, { ok: true, name });
    }

    // 删除集合
    if (req.method === "POST" && req.url === "/api/collections/delete") {
      const body = await readBody(req);
      const name = collectionName(body);
      await client.deleteCollection(name);
      collections.delete(name);
      return send(res, 200, { ok: true, name });
    }

    // 批量写入（增量 upsert：同 id 覆盖，不重复）
    if (req.method === "POST" && req.url === "/api/docs") {
      const body = await readBody(req);
      const name = collectionName(body);
      const items = Array.isArray(body.items) ? body.items : [];
      if (items.length === 0) throw new Error("没有要写入的文档");
      for (const it of items) {
        if (!Array.isArray(it.embedding) || it.embedding.length !== DIM) {
          throw new Error(`文档 ${it.id} 的向量维度必须是 ${DIM}（浏览器端侧计算）`);
        }
      }
      const collection = await openCollection(name);
      const writeFn = typeof collection.upsert === "function" ? "upsert" : "add";
      let written = 0;
      for (const part of chunk(items, WRITE_BATCH)) {
        const payload = {
          ids: part.map((x) => String(x.id)),
          documents: part.map((x) => String(x.text)),
          embeddings: part.map((x) => x.embedding),
          metadatas: part.map((x) => x.metadata && typeof x.metadata === "object" ? x.metadata : {}),
        };
        if (writeFn === "upsert") await collection.upsert(payload);
        else await collection.add(payload);
        written += part.length;
      }
      return send(res, 200, {
        ok: true,
        collection: name,
        written,
        writeFn,
        dbMs: dbMs(),
      });
    }

    // 文档列表（知识库内容预览）
    if (req.method === "GET" && req.url.startsWith("/api/docs?")) {
      const q = new URL(req.url, "http://x").searchParams;
      const name = collectionName({ collection: q.get("collection") });
      const limit = Math.min(Number(q.get("limit") || 50), 500);
      const collection = await openCollection(name);
      const rows = await collection.get({ limit, include: ["documents", "metadatas"] });
      return send(res, 200, { collection: name, rows, dbMs: dbMs() });
    }

    // 混合检索：全文（关键词）+ 向量（语义）由 seekdb 在库内完成，RRF 融合
    if (req.method === "POST" && req.url === "/api/search") {
      const body = await readBody(req);
      const name = collectionName(body);
      const nResults = Math.min(Number(body.nResults || 5), 20);
      const keyword = String(body.keyword || "").trim();
      if (!Array.isArray(body.embedding) || body.embedding.length !== DIM) {
        throw new Error(`查询向量必须是浏览器算出的 ${DIM} 维数组`);
      }
      const collection = await openCollection(name);
      const knn = { queryEmbeddings: [body.embedding] };
      const params = keyword
        ? { query: { whereDocument: { $contains: keyword } }, knn, nResults }
        : { knn, nResults }; // 无可用关键词时退化为库内向量检索（仍在 seekdb 内完成）
      const result = await collection.hybridSearch(params);
      return send(res, 200, { collection: name, hybrid: Boolean(keyword), dbMs: dbMs(), result });
    }

    // 索引刷新探测：seekdb 写入为事务性提交，这里用 count 验证可见性并计时
    if (req.method === "POST" && req.url === "/api/refresh") {
      const body = await readBody(req);
      const name = collectionName(body);
      const collection = await openCollection(name);
      const count = typeof collection.count === "function" ? await collection.count() : null;
      return send(res, 200, { ok: true, collection: name, count, refreshMs: dbMs() });
    }

    return send(res, 404, { error: "not found" });
  } catch (error) {
    const msg = error && error.cause && error.cause.sqlMessage
      ? error.cause.sqlMessage
      : String(error && error.message ? error.message : error);
    return send(res, 500, { error: msg });
  }
});

server.listen(Number(process.env.PORT || 8787), "127.0.0.1", () => {
  console.log(`[api] http://127.0.0.1:8787  (seekdb mode=${CFG.mode}, dim=${DIM})`);
});
