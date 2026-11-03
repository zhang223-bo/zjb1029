// ===========================================================================
// 检索质量评测：量化前后（FP32 vs INT8 q8）对比
// 方法：固定语料集 + 查询集，每个查询标注 1 篇相关文档，
//       分别用 FP32 与 INT8 模型在同一语料上做向量检索，
//       计算 Recall@K 与 MRR@K，对比量化带来的质量变化。
// 运行：npm run eval（Node 20+，模型从魔搭下载，首次运行需联网）
// ===========================================================================
import { env, pipeline } from "@huggingface/transformers";

// 与前端保持一致的下载源（魔搭）
env.remoteHost = "https://www.modelscope.cn/";
env.remotePathTemplate = "{model}/resolve/master/";

const MODEL = "Xenova/bge-small-zh-v1.5";

// 语料：seekdb / 端侧推理主题 + 干扰项
const CORPUS = [
  "seekdb 是 OceanBase 推出的 AI 原生搜索数据库，统一支持关系、向量、全文和 JSON 数据。",
  "seekdb 的混合检索通过 RRF 把向量语义搜索与 BM25 全文搜索融合为统一排序。",
  "seekdb 支持嵌入式部署，可以直接以库的形式嵌入应用进程，适合个人开发者与边缘设备。",
  "seekdb 的向量索引支持 HNSW 与 IVF，全文索引基于 BM25 相关性打分。",
  "bge-small-zh-v1.5 是智源开源的中文嵌入模型，输出 512 维向量，适合端侧运行。",
  "模型量化把 FP32 权重压成 INT8，体积缩小约四倍，检索质量基本不变。",
  "WebGPU 让浏览器直接调用 GPU 进行并行计算，推理吞吐高于 WASM。",
  "Transformers.js 可以在浏览器里运行 ONNX 模型，文本不出本机即可完成向量化。",
  "倒排索引把分词后的词项映射到文档列表，是全文检索的基础数据结构。",
  "HNSW 通过分层图结构把近似最近邻搜索复杂度降到对数级别。",
  "RRF 倒数排名融合按 1/(k+rank) 对每路结果贡献求和后重新排序。",
  "seekdb 的写入是事务性的，新文档写入后向量与全文索引立即可见。",
  "批量推理把多条文本合成一个 batch，可以摊薄固定开销提升吞吐数倍。",
  "MySQL 协议兼容让 seekdb 可以复用现有 MySQL 生态的驱动与工具。",
  "知识库应用通常需要把文档切分成较小片段，再逐段向量化后入库。",
  "今天下午三点在会议室开项目评审会，请提前准备季度汇报材料。",
  "这家餐厅的红烧肉口味偏甜，米饭软硬适中，停车还算方便。",
  "最近的天气预报显示周末有降雨，气温下降，出门记得带伞。",
  "股票市场今天震荡整理，成交量较昨日有所放大，板块轮动加快。",
  "儿童绘本推荐：适合三到六岁孩子的识字启蒙读物有哪些。",
];

// 查询 → 相关文档在 CORPUS 中的下标（各 1 条）
const QUERIES = [
  { q: "seekdb 混合检索是怎么把向量和全文结果融合的", rel: 1 },
  { q: "嵌入式数据库适合什么样的部署场景", rel: 2 },
  { q: "bge-small 模型输出多少维向量", rel: 4 },
  { q: "INT8 量化后模型体积会小多少", rel: 5 },
  { q: "浏览器里怎么用 GPU 加速推理", rel: 6 },
  { q: "如何保证文本不出本机就完成向量化", rel: 7 },
  { q: "全文检索用的是什么索引结构", rel: 8 },
  { q: "HNSW 为什么能高效检索高维向量", rel: 9 },
  { q: "RRF 融合公式是怎么算的", rel: 10 },
  { q: "新写入的文档什么时候可以被检索到", rel: 11 },
  { q: "怎样提升端侧推理的吞吐量", rel: 12 },
  { q: "seekdb 兼容哪种数据库协议", rel: 13 },
  { q: "文档入库前要做哪些预处理", rel: 14 },
];

const K = 5;

async function embedAll(extractor, texts, batchSize = 8) {
  const vecs = [];
  for (let i = 0; i < texts.length; i += batchSize) {
    const out = await extractor(texts.slice(i, i + batchSize), {
      pooling: "mean",
      normalize: true,
    });
    vecs.push(...out.tolist());
  }
  return vecs;
}

function dot(a, b) {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += a[i] * b[i];
  return s;
}

async function evaluate(dtype) {
  const extractor = await pipeline("feature-extraction", MODEL, { dtype, device: "cpu" });
  const docVecs = await embedAll(extractor, CORPUS);
  let recallHits = 0;
  let mrrSum = 0;
  const details = [];
  for (const { q, rel } of QUERIES) {
    const [qv] = await embedAll(extractor, [q]);
    const scores = docVecs.map((v, i) => ({ i, s: dot(qv, v) }));
    scores.sort((a, b) => b.s - a.s);
    const top = scores.slice(0, K);
    const rank = top.findIndex((x) => x.i === rel) + 1; // 0 表示未命中
    if (rank > 0) {
      recallHits += 1;
      mrrSum += 1 / rank;
    }
    details.push({ q, rel, rank, top1: top[0].i });
  }
  return {
    dtype,
    recallAtK: recallHits / QUERIES.length,
    mrrAtK: mrrSum / QUERIES.length,
    details,
  };
}

console.log(`模型: ${MODEL}  语料: ${CORPUS.length} 条  查询: ${QUERIES.length} 条  K=${K}\n`);

const fp32 = await evaluate("fp32");
console.log(`FP32  Recall@${K}=${fp32.recallAtK.toFixed(3)}  MRR@${K}=${fp32.mrrAtK.toFixed(3)}`);
const q8 = await evaluate("q8");
console.log(`INT8  Recall@${K}=${q8.recallAtK.toFixed(3)}  MRR@${K}=${q8.mrrAtK.toFixed(3)}`);

console.log(`\n变化: Recall ${((q8.recallAtK - fp32.recallAtK) * 100).toFixed(1)}pp  MRR ${((q8.mrrAtK - fp32.mrrAtK) * 100).toFixed(1)}pp`);

console.log("\n逐查询对比（INT8 命中排名 vs FP32 命中排名）:");
for (let i = 0; i < QUERIES.length; i++) {
  const a = fp32.details[i];
  const b = q8.details[i];
  console.log(`  [${a.rank}/${b.rank}] ${a.q}`);
  if (a.rank !== b.rank) console.log(`        FP32 top1=${CORPUS[a.top1].slice(0, 24)}…  INT8 top1=${CORPUS[b.top1].slice(0, 24)}…`);
}
