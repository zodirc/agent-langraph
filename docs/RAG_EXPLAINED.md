# RAG 原理与本项目实现详解

本文面向当前项目，系统说明什么是 RAG、[`BM25`](app/services/knowledge_store.py:398)、[`RRF`](app/services/knowledge_store.py:633)、embedding、余弦相似度，以及它们在本仓库中的组合方式。重点不是泛泛而谈，而是结合当前代码和具体例子，解释“为什么这么做”“它解决什么问题”“实际是怎么跑起来的”。

---

## 1. 什么是 RAG

RAG 是 Retrieval-Augmented Generation，中文通常叫“检索增强生成”。

它的核心思想非常朴素：

1. 用户先提问
2. 系统先去外部知识中找相关内容
3. 把找回来的内容连同用户问题一起交给大模型
4. 大模型基于这些资料生成答案

所以，RAG 解决的不是“模型会不会说话”，而是：

- 模型**不知道**的知识，如何在回答前找回来
- 模型**记不住**的知识，如何临时补给它
- 模型**上下文放不下**的知识，如何只取最相关的那一小部分

在当前项目里，RAG 不是单一模块，而是一条检索流水线，入口在 [`retrieval_node()`](app/nodes/retrieval_node.py:16)。

---

## 2. 当前项目的 RAG 做了哪些事

一次完整知识检索，主入口是 [`KnowledgeStore.hybrid_search()`](app/services/knowledge_store.py:599)。

整体步骤是：

1. 构造 query
2. 用本地 embedding 模型把 query 变成向量，见 [`embed_text()`](app/services/embedding_service.py:34)
3. 用向量去做语义检索，见 [`vector_search()`](app/services/knowledge_store.py:466)
4. 同时对 query 做 token 化，再做关键词检索，见 [`keyword_search()`](app/services/knowledge_store.py:398)
5. 用 [`_rrf_merge()`](app/services/knowledge_store.py:633) 融合两路结果
6. 如果配置允许，再做 rerank，见 [`hybrid_search()`](app/services/knowledge_store.py:621)
7. 最后截断为 top-k，交给后续节点使用

也就是说，本项目不是“只做 embedding 检索”，而是：

- embedding 语义检索
- BM25 风格关键词检索
- RRF 融合
- 可选 rerank
- 再配合 domain 过滤与 memory 检索

---

## 3. 当前项目使用的是本地 embedding 模型

在本项目里，embedding 入口在 [`embed_texts()`](app/services/embedding_service.py:16)。

代码支持三层路径：

1. 外部 API：Voyage
2. 本地模型：MiniLM
3. 保底 fallback：deterministic hash embedding

而你提到“我们当前使用的是本地的 embedding 模型”，对应代码就是 [`_local_minilm_embed()`](app/services/embedding_service.py:80)。

它通过 [`SentenceTransformer`](app/services/embedding_service.py:69) 加载本地模型，默认模型名来自 [`settings.LOCAL_EMBEDDING_MODEL_NAME`](app/services/embedding_service.py:71)，通常会是 `sentence-transformers/all-MiniLM-L6-v2` 一类模型。

这意味着：

- 文本先被送入本地 MiniLM
- 输出一个实数向量，例如 384 维
- 向量会被归一化，见 [`normalize_embeddings=True`](app/services/embedding_service.py:84)
- 后续向量检索就拿这些向量做相似度比较

本地 embedding 的意义是：

- 不依赖外部 API
- 开发环境可跑
- 数据不必出本机
- 延迟和成本更可控

---

## 4. embedding 到底是什么

embedding 可以理解成：

> 把一句自然语言映射为一个高维数学向量，使得语义相近的文本在向量空间里也更接近。

比如：

- 句子 A：`如何减少小说里的 AI 味？`
- 句子 B：`怎样让文风更自然，不那么像机器生成？`

虽然字面差异很大，但语义接近，所以 embedding 后得到的向量位置也会比较近。

### 4.1 向量是什么

向量可以简单写成：

`q = [0.12, -0.33, 0.81, ...]`

如果是 384 维，就表示这个数组有 384 个数。

每个维度本身不一定有可解释的人类含义，但整个向量组合能编码语义特征。

### 4.2 为什么高维向量可以表示语义

因为训练 embedding 模型时，模型会学习让：

- 语义相近的句子 -> 向量更接近
- 语义差异大的句子 -> 向量更远

所以 embedding 不是“手工规则”，而是模型学习出来的空间映射。

---

## 5. 余弦相似度是什么

当 query 和文档都被变成向量后，需要一种方法衡量它们“像不像”。

最常见的方法就是余弦相似度 cosine similarity。

公式：

`cos(theta) = (q · d) / (||q|| ||d||)`

其中：

- `q`：query 向量
- `d`：document 向量
- `q · d`：点积
- `||q||`：向量长度
- `||d||`：向量长度

### 5.1 点积是什么

如果：

`q = [q1, q2, q3]`
`d = [d1, d2, d3]`

那么点积就是：

`q · d = q1*d1 + q2*d2 + q3*d3`

### 5.2 向量长度是什么

`||q|| = sqrt(q1^2 + q2^2 + q3^2)`

也就是欧几里得范数。

### 5.3 余弦相似度的直觉

余弦相似度看的是**方向相似程度**，而不是绝对长度。

- 值接近 1：方向很接近，语义通常相近
- 值接近 0：关系弱
- 值接近 -1：方向相反

在文本 embedding 中，常见是越接近 1 越相似。

### 5.4 一个简单计算例子

假设：

`q = [1, 2]`
`d = [2, 3]`

那么：

- 点积：`q · d = 1*2 + 2*3 = 8`
- `||q|| = sqrt(1^2 + 2^2) = sqrt(5)`
- `||d|| = sqrt(2^2 + 3^2) = sqrt(13)`

所以：

`cos(theta) = 8 / (sqrt(5)*sqrt(13)) ≈ 8 / 8.06 ≈ 0.993`

说明这两个向量方向非常接近。

如果换成：

`d2 = [2, -3]`

则：

- 点积：`1*2 + 2*(-3) = -4`
- `||d2|| = sqrt(13)`
- 余弦相似度：`-4 / (sqrt(5)*sqrt(13)) ≈ -0.496`

说明方向差异很大。

### 5.5 在本项目里的意义

在 [`_QdrantVectorIndex.__init__()`](app/services/knowledge_store.py:155) 中，向量集合创建时使用的是 cosine 距离。

这意味着：
- query embedding 和文档 embedding 越接近
- 文档越可能被排在前面

对于 Chroma，检索返回 distance 后，在 [`_ChromaVectorIndex.search()`](app/services/knowledge_store.py:119) 中又转成：

`score = 1 / (1 + distance)`

这是一个常见的距离到分数的映射。

例如：
- distance = 0.1 -> score ≈ 0.91
- distance = 0.5 -> score ≈ 0.67
- distance = 2.0 -> score ≈ 0.33

距离越小，分数越大，表示越相似。

---

## 6. BM25 是什么

[`keyword_search()`](app/services/knowledge_store.py:398) 里实现的是 BM25 风格的关键词检索。

BM25 是信息检索里非常经典的一个排序算法。它不是 embedding，也不是神经网络，而是一个词法统计模型。

它回答的问题是：

> 文档里是否出现了用户查询中的关键词？
> 这些关键词是不是重要词？
> 这篇文档里出现这些词的频率是否足够高？
> 这篇文档是不是因为太长而“虚高命中”？

### 6.1 BM25 的核心因素

BM25 主要关心三件事：

1. `tf`：term frequency，词在当前文档中出现多少次
2. `idf`：inverse document frequency，这个词在整个语料里有多稀有
3. 文档长度归一化：长文档不能天然占便宜

项目中的核心计算在 [`keyword_search()`](app/services/knowledge_store.py:448) 到 [`keyword_search()`](app/services/knowledge_store.py:450)：

`idf = log(1 + ((doc_count - df + 0.5) / (df + 0.5)))`

`score += idf * ((tf * (k1 + 1)) / denom)`

其中 `denom` 包含文档长度惩罚项。

### 6.2 BM25 的直觉解释

假设你搜索：

`TXT 排版 口吻`

有三篇文档：

- 文档 A：标题就叫“写作口吻与TXT排版规范”
- 文档 B：大篇幅谈小说情节，但只偶尔出现一次“排版”
- 文档 C：讲代码编辑，不含这些词

那么 BM25 通常会认为：
- A 最相关
- B 次之
- C 不相关

因为：
- A 中关键词出现频率高
- 且这些词具有较强区分度
- B 虽然提到过，但不够集中
- C 根本没命中

### 6.3 为什么 BM25 仍然非常重要

embedding 很强，但不是万能的。BM25 对下面这些内容尤其稳：

- 配置项名
- 函数名
- 类名
- 路径名
- 报错码
- 文档标题原词
- 短且精确的术语

例如：
- `SESSION_MEMORY_RETRIEVAL_ENABLED`
- `skip_retrieval`
- `builtin-writing-guidelines`

这类 query，BM25 往往比 embedding 更可靠。

### 6.4 本项目为何还特别处理中文

在 [`_tokenize_text()`](app/services/knowledge_store.py:64) 中，项目对中文做了特别处理：

- 提取普通 token
- 如果 token 含中文且长度大于 1，则追加 CJK bigram

例如“写作规范”可能被拆出更细的二元片段，改善中文无空格文本的召回效果。

这能解决：
- 中文 query 没有空格
- 分词边界不稳定
- 用户短语与文档写法略有差异

---

## 7. RRF 是什么

RRF 是 Reciprocal Rank Fusion，中文可以理解为“倒数排名融合”。

它是多路检索融合里非常经典、非常实用的方法。

本项目实现见 [`_rrf_merge()`](app/services/knowledge_store.py:633)。

### 7.1 RRF 要解决什么问题

假设你现在有两路检索结果：

- 一路来自 embedding 向量检索
- 一路来自 BM25 关键词检索

问题来了：
- embedding 分数和 BM25 分数不是一个量纲
- 不能简单粗暴相加
- 也很难统一校准

RRF 的想法是：

> 不直接比较原始分数，只比较“各自排第几”。

### 7.2 RRF 的公式

`RRF(d) = Σ 1 / (k + rank_i(d))`

在本项目里，等价代码是：

- [`scores[doc_id] += 1.0 / (k + rank + 1)`](app/services/knowledge_store.py:643)
- [`scores[doc_id] += 1.0 / (k + rank + 1)`](app/services/knowledge_store.py:647)

这里：
- `rank` 是文档在某个榜单中的名次，从 0 开始
- `k` 是平滑常数，项目默认是 60，见 [`_rrf_merge()`](app/services/knowledge_store.py:637)

### 7.3 一个 RRF 计算例子

假设 query 是：

`如何让小说写作减少 AI 味并保持 TXT 排版自然？`

向量检索结果：
1. `builtin-prose-voice-format`
2. `builtin-writing-guidelines`
3. `builtin-architecture`

BM25 结果：
1. `builtin-writing-guidelines`
2. `builtin-prose-voice-format`
3. `some-layout-note`

用 RRF 计算：

#### 文档 1：`builtin-prose-voice-format`
- 向量榜 rank=0 -> `1 / (60+1) = 1/61 ≈ 0.01639`
- BM25 榜 rank=1 -> `1 / (60+2) = 1/62 ≈ 0.01613`
- 总分 ≈ `0.03252`

#### 文档 2：`builtin-writing-guidelines`
- 向量榜 rank=1 -> `1/62 ≈ 0.01613`
- BM25 榜 rank=0 -> `1/61 ≈ 0.01639`
- 总分 ≈ `0.03252`

#### 文档 3：`builtin-architecture`
- 只在向量榜 rank=2 -> `1/63 ≈ 0.01587`

#### 文档 4：`some-layout-note`
- 只在 BM25 榜 rank=2 -> `1/63 ≈ 0.01587`

最后排序通常会变成：
- `builtin-prose-voice-format`
- `builtin-writing-guidelines`
- `builtin-architecture`
- `some-layout-note`

### 7.4 RRF 的意义

RRF 非常适合解决这个问题：

- embedding 会漏掉一些精确术语
- BM25 会漏掉一些语义表达
- 但如果某个文档在两边都靠前，那它往往真的值得信任

所以 RRF 的核心价值是：

**把多路检索中的“共识结果”优先排到前面。**

---

## 8. 本项目里的一次真实风格的 RAG 例子

下面用当前项目的写作场景举一个完整例子。

### 8.1 用户问题

用户输入：

> 帮我写一章小说，要求减少 AI 味，语气自然一点，TXT 排版规范。

### 8.2 第一步：确定检索域

[`retrieval_domains_for_state()`](app/services/retrieval_policy.py:56) 会识别这是写作任务，于是只检索：

- `writing`
- `common`

不会去搜 `code` 域的文档。

这一步解决的是“不要把不相干内容也放进上下文”。

### 8.3 第二步：查询增强

写作场景下，系统还会做 query enrichment，见 [`enrich_retrieval_query_for_writing()`](app/services/writing_knowledge.py:30)。

原始 query：

`减少 AI 味，语气自然，TXT 排版规范`

增强后可能变成：

`减少 AI 味，语气自然，TXT 排版规范 写作规范 口吻 去AI化 自然叙事 剧情连贯 伏笔 衔接 TXT排版 段落 章题 全角标点`

这样做的原因是：
- 用户的问题是自然表达
- 但知识库里的规范文档未必正好用同样措辞
- 增加一些先验关键词，能提高召回率

### 8.4 第三步：embedding 语义检索

[`vector_search()`](app/services/knowledge_store.py:466) 会对 query 生成 embedding，然后去向量库查近邻。

如果知识库里有：
- `写作口吻与TXT排版规范`
- `长文写作规范`
- `LangGraph Agent Runtime Overview`

那么前两篇通常更接近 query 的语义。

### 8.5 第四步：BM25 关键词检索

[`keyword_search()`](app/services/knowledge_store.py:398) 会对下面这些词很敏感：
- `TXT排版`
- `口吻`
- `写作规范`
- `去AI化`

因此标题和正文里直接含这些词的文档会被明显抬高。

### 8.6 第五步：RRF 融合

[`_rrf_merge()`](app/services/knowledge_store.py:633) 会把向量榜和关键词榜合并。

最后通常会得到：
- “写作口吻与TXT排版规范”
- “长文写作规范”
排在前面。

### 8.7 第六步：可选 rerank

若启用 [`settings.RAG_RERANK_ENABLED`](app/services/knowledge_store.py:609)，系统还会对召回候选做一次精排。

这一步的作用是：
- 前面两步先把“可能相关”的找回来
- rerank 再从候选里挑最符合当前 query 意图的结果

### 8.8 第七步：交给生成模型

最终，模型看到的不是整个知识库，而是最相关的几条规范内容。

于是生成时就更容易做到：
- 文风自然
- 少 AI 腔
- 符合 TXT 排版要求

这就是一次完整的 RAG 操作。

---

## 9. 再举一个代码场景例子

用户输入：

> 为什么我的多轮会话里，总能召回旧 session 的内容？

### 9.1 语义检索可能命中
embedding 可能命中：
- “First turn of a session window should not recall other sessions' episode memories”
- 与 session memory 隔离相关的说明

对应代码位置是 [`restrict_memory_to_current_session()`](app/services/retrieval_policy.py:82)。

### 9.2 BM25 可能命中
如果用户问得更精确：

> `new_session` 和 `session_is_new` 是做什么的？

那么 BM25 更可能直接命中 [`restrict_memory_to_current_session()`](app/services/retrieval_policy.py:96) 相关逻辑，因为里面有这些精确字段名。

### 9.3 RRF 融合后
如果某个文档同时被 embedding 和 BM25 都认为相关，它就会在融合排序后更靠前。

这比只靠单一检索器更稳。

---

## 10. 这套 RAG 分别解决什么问题

### 10.1 embedding 解决什么

解决：
- 同义表达
- 自然语言改写
- 用户提问和文档表述不一致
- 语义相近但词面不同的召回

例如：
- “减少 AI 味”
- “降低机器生成感”
- “让文风更自然”

这些词面不同，但语义接近。

### 10.2 BM25 解决什么

解决：
- 精确关键词
- 配置项名
- 错误码
- doc_id
- 文件名
- 类名、函数名

例如：
- `skip_retrieval`
- `RAG_RERANK_ENABLED`
- `builtin-writing-guidelines`

### 10.3 RRF 解决什么

解决：
- 两种检索器分数不能直接比较
- 希望融合多路召回
- 希望优先选择“两个检索器都觉得不错”的文档

### 10.4 rerank 解决什么

解决：
- 候选很多，但前几名不够准
- 希望用更高成本模型做最终精排

### 10.5 domain filter 解决什么

解决：
- 多任务系统里的跨域噪声
- 写作任务搜到代码规范
- 代码任务搜到写作规则

---

## 11. 为什么当前项目不只靠 embedding

很多人刚接触 RAG 时容易以为：

> 只要有 embedding，检索问题就解决了。

但真实工程里通常不是这样。

只靠 embedding 的问题是：
- 对精确 token 不稳定
- 对短 query 有时不稳
- 对函数名/配置项/文件名不一定敏感

只靠 BM25 的问题是：
- 语义泛化差
- 同义改写容易漏召回
- 用户问得不精确时表现一般

所以工程上经常采用：

- embedding 负责语义召回
- BM25 负责词法精确召回
- RRF 负责融合

当前项目正是这样做的，主逻辑就在 [`hybrid_search()`](app/services/knowledge_store.py:599)。

---

## 12. 这套方案的优点

### 12.1 兼顾“语义理解”和“精确命中”

- embedding：懂意思
- BM25：认关键词
- RRF：做融合

### 12.2 对多任务代理更友好

当前项目不是单纯问答，而是：
- 写作
- 代码
- 规划
- 任务执行

所以需要 domain-aware retrieval，见 [`retrieval_domains_for_state()`](app/services/retrieval_policy.py:56)。

### 12.3 本地模型可离线运行

使用本地 MiniLM 使得：
- 开发与测试环境更稳定
- 数据隐私更好
- 外部服务故障时不至于失效

### 12.4 结果更稳健

单一检索器会有自己的偏差，而混合检索能提升整体鲁棒性。

---

## 13. 这套方案的局限

### 13.1 embedding 不是理解一切

本地 MiniLM 很实用，但不是万能：
- 对复杂专业语义未必最强
- 对特别短的 token 不一定敏感
- 对代码标识符表现通常不如关键词检索稳定

### 13.2 BM25 仍然依赖词面

如果用户完全换了一种说法，而文档没有任何相近词面，BM25 可能召不回来。

### 13.3 RRF 只是稳健融合，不是智能理解

RRF 并不会“理解内容”，它只是一个排名融合器。

它做的是：
- 让多路榜单更稳定地合并
- 而不是自己判断文档语义真伪

### 13.4 最终效果依赖知识库质量

RAG 的上限，很大程度取决于：
- 文档有没有入库
- 文档是否切块合理
- 元数据是否干净
- 检索域划分是否合理

---

## 14. 一个最简记忆法

如果你要记住这套系统，只要记三句话：

1. **embedding**：把文本变成向量，解决“语义相近但字面不同”的问题
2. **BM25**：按关键词统计打分，解决“精确词命中”的问题
3. **RRF**：按排名融合多路结果，解决“多种检索器如何稳健合并”的问题

再加一句：

4. **RAG**：先检索相关知识，再让模型生成答案

---

## 15. Chunk 是什么，为什么它决定 RAG 上限

前面我们一直在说“文档检索”，但真正高质量的 RAG，往往检索的不是整篇文档，而是文档中的一个个 chunk。

chunk 可以理解成：

> 把长文档切成多个较短、相对完整、可独立被检索的片段。

例如一篇 8000 字的规范文档，不会整体作为一个检索单元，而是切成：

- chunk 1：概述
- chunk 2：术语定义
- chunk 3：写作规范
- chunk 4：排版规范
- chunk 5：示例与反例

### 15.1 为什么不能永远用整文档检索

如果总是整篇文档入向量库和关键词库，会出现几个问题：

1. **召回不够精确**
   - 用户只问“TXT 排版”，结果整篇“写作规范”都被召回
   - 真正相关的只有其中一小段

2. **上下文污染**
   - 一篇长文里可能同时有很多主题
   - 检索命中其中一处，却把整篇无关内容都带进 prompt

3. **embedding 被平均化**
   - 长文本向量会把多个主题揉在一起
   - 导致“语义中心”变模糊

4. **BM25 词频失真**
   - 长文档天然会包含更多词
   - 即使做长度归一化，依然可能让匹配不够尖锐

5. **rerank 成本变高**
   - rerank 面对的是“大块候选”，不是“精确证据片段”

所以，从工程上说：

**chunk 质量，几乎决定了 RAG 的检索上限。**

### 15.2 当前项目在 chunk 上的状态（已实现 chunk-first）

自 chunk-first 改造后，[`upsert_document()`](app/services/knowledge_store.py) 对超过 `rag.chunk_min_chars`（默认 600）的文档会自动切块：

1. [`chunk_document()`](app/services/knowledge_chunker.py) 按 Markdown 标题 + 段落边界切分，并注入「文档标题 / 章节 / 内容」前缀供 embedding 与 BM25 使用。
2. 父文档保留完整正文（`metadata.is_parent=true`，不参与检索）。
3. 每个 chunk 单独写入 SQLite 与向量索引（`doc_id` 形如 `{parent}__c0000`，`metadata.is_chunk=true`）。
4. [`hybrid_search()`](app/services/knowledge_store.py) 的向量路与 BM25 路都在 chunk 粒度检索；[`_limit_per_doc()`](app/services/knowledge_store.py) 按 `parent_doc_id` 限制同一来源进入上下文的片段数。
5. 可选邻接扩展：高分 chunk 命中后，按 `chunk_index ± rag.chunk_adjacency_radius` 补充相邻片段（`source=adjacency`）。

短文档、评测用例、以及 `metadata.skip_chunking=true` 的条目仍保持整文档单条索引，兼容原有 API 与 golden 测试。

### 15.3 一个整文档检索的误差例子

假设有一篇知识文档《长文写作规范》，内容如下：

- 第 1 节：总原则
- 第 2 节：人物塑造
- 第 3 节：剧情连贯
- 第 4 节：TXT 排版
- 第 5 节：常见 AI 腔示例

用户问题是：

> 这一章的 TXT 排版怎么做？

如果整篇作为一个向量：
- embedding 会把“人物塑造、剧情连贯、排版、AI 腔”全部压缩进同一个向量
- BM25 虽然会因为“TXT 排版”命中整篇文档，但仍然返回整篇

最后模型拿到的上下文里，有大量与当前问题无关的信息。

如果切成 chunk：
- chunk A：总原则
- chunk B：人物塑造
- chunk C：剧情连贯
- chunk D：TXT 排版
- chunk E：AI 腔示例

那么 query 很可能直接命中 chunk D，而不是整篇文档。

这就是 chunk 的价值：

**让检索返回“证据片段”，而不是“整本书”。**

---

## 16. 基于当前项目的 chunk 再优化建议

下面不是抽象建议，而是基于当前项目已有结构提出的增量优化方向。

### 16.1 优化方向一：从 document-first 升级到 chunk-first 索引

当前 [`upsert_document()`](app/services/knowledge_store.py:318) 的接口更像是按文档整体写入。如果要提升检索精度，建议把知识入库改成：

- 一个原始文档 `doc_id`
- 切分为多个 `chunk_id`
- 每个 chunk 单独 embedding、单独入索引
- metadata 中保留：
  - `doc_id`
  - `chunk_id`
  - `chunk_index`
  - `section_title`
  - `domain`
  - `source_url`
  - `token_count`

这样做后：
- [`vector_search()`](app/services/knowledge_store.py:466) 召回的是 chunk
- [`keyword_search()`](app/services/knowledge_store.py:398) 排序的是 chunk
- [`_limit_per_doc()`](app/services/knowledge_store.py:658) 也更有意义，因为能真正控制每个文档最多带几个 chunk 进入上下文

### 16.2 优化方向二：使用“结构化切块”，不要只按字数硬切

最差的 chunk 方法是：
- 每 500 字切一刀
- 不看标题
- 不看段落边界
- 不看代码块边界

这样的问题是：
- 语义会被切断
- 定义和示例可能被拆开
- 标题和正文分离后，检索会失去上下文

更适合当前项目的是“结构化切块”：

1. **Markdown / 文档类**
   - 先按标题层级切
   - 再对超长 section 做二次切分

2. **代码规范类**
   - 按章节、规则、示例块切
   - 标题与规则正文尽量保留在同一 chunk

3. **代码文件说明类**
   - 按函数/类/模块说明切
   - 保留符号名作为 metadata

4. **写作规范类**
   - 按“原则 / 反例 / 正例 / 排版 / 口吻”分块

这会比简单字符长度切块更适合当前仓库，因为这里的知识本来就有明显结构。

### 16.3 优化方向三：加入 overlap，但不要过大

chunk overlap 的意思是：

> 相邻 chunk 之间保留一部分重叠内容，避免关键语义刚好落在切分边界上。

例如：
- chunk1：字符 1~500
- chunk2：字符 451~950

中间 50 个字符就是 overlap。

其好处是：
- 跨段定义不会被切断得太厉害
- query 命中边缘内容时更稳

但 overlap 不能过大，否则会：
- 重复召回太多近似 chunk
- 增加向量库体积
- 增加 rerank 成本
- 让上下文里重复内容变多

基于当前项目的知识文档类型，我建议：
- 规则文档：10%~15% overlap
- 代码说明文档：按语义边界优先，必要时小 overlap
- 写作规范文档：保持段落完整优先，overlap 作为补充

### 16.4 优化方向四：为 chunk 增加“标题前缀注入”

一个常见问题是：
- 某个 chunk 本身正文很短
- 但离开章节标题后，语义变弱

例如 chunk 内容只有：

`尽量使用全角标点，段落之间空一行。`

如果单独 embedding，模型未必知道这是“写作排版规范”还是“通用中文排版建议”。

更好的做法是把父级标题一起拼进去，例如：

`文档标题：长文写作规范\n章节：TXT 排版\n内容：尽量使用全角标点，段落之间空一行。`

这样：
- embedding 语义更完整
- BM25 也能命中章节标题
- rerank 更容易分清语境

这对当前项目尤其重要，因为项目里有很多“规范型文档”，章节语义很强。

### 16.5 优化方向五：检索后做 chunk 邻接扩展，而不是一开始就给大块

当前系统可以返回多个命中片段。更进一步的优化思路是：

1. 先检索出最相关 chunk
2. 若命中 chunk 的分数足够高
3. 再按 `chunk_index` 补充它前后相邻 1 个 chunk

这样做的好处是：
- 主证据仍然精准
- 同时给模型一点局部上下文
- 比直接整篇召回更干净

例如命中：
- chunk 7：TXT 排版规则

则可以附带：
- chunk 6：章节标题规范
- chunk 8：排版示例

这比“整篇写作规范全文注入”更高效。

### 16.6 优化方向六：让 BM25 与 embedding 都在 chunk 级别工作

如果向量检索是 chunk 级，但 BM25 仍是整文档级，就会出现：
- 一个返回精确片段
- 一个返回整篇大文
- RRF 融合时粒度不一致

所以理想状态是：
- 向量检索：chunk 级
- BM25：chunk 级
- RRF：chunk 级
- 最终聚合：必要时再按 `doc_id` 合并展示

这样做后，[`_rrf_merge()`](app/services/knowledge_store.py:633) 融合的是同一粒度对象，效果会更稳定。

### 16.7 优化方向七：对不同 domain 使用不同 chunk 策略

当前项目已经有 domain-aware retrieval，见 [`retrieval_domains_for_state()`](app/services/retrieval_policy.py:56)。

同样的思路也应该延伸到 chunk 策略：

1. **writing 域**
   - 按标题 + 段落组切块
   - 保留示例与规则在一起
   - 更注重叙述连续性

2. **code 域**
   - 按函数、类、配置段、错误案例切块
   - 强化符号名、路径名 metadata
   - chunk 尺寸可略小，便于精确定位

3. **common 域**
   - 采用中等 chunk 尺寸
   - 结构优先于固定长度

这样做比全域统一 500 字切块更符合当前系统的实际任务分布。

### 16.8 优化方向八：引入 chunk 质量评估指标

chunk 优化不能只靠感觉，建议增加最基本的离线评估：

1. **命中率 Recall@k**
   - 目标 chunk 是否在 top-k 内

2. **MRR / nDCG**
   - 目标 chunk 是否排得足够前

3. **平均上下文冗余率**
   - 注入 prompt 的文本中，有多少比例其实与 query 无关

4. **平均相邻 chunk 重复率**
   - overlap 是否过大

5. **按 domain 分桶评估**
   - writing 的最优 chunk 大小不一定适合 code

对当前项目来说，如果只做一个指标，我最建议先做：

- `top_k 命中是否包含正确 chunk`
- `最终注入内容总字数中，无关内容占比`

因为这两个指标最能直接反映当前 RAG 是否“准且省上下文”。

---

## 17. chunk-first 实施状态与配置

### 已实现（对应原第一、二阶段）

| 能力 | 实现位置 |
|------|----------|
| 结构化切块 + 标题前缀 | [`knowledge_chunker.py`](app/services/knowledge_chunker.py) |
| 父文档目录 + chunk 向量/BM25 双索引 | [`KnowledgeStore.upsert_document()`](app/services/knowledge_store.py) |
| hybrid + RRF + rerank（chunk 级） | [`hybrid_search()`](app/services/knowledge_store.py) |
| 单文档 chunk 上限 | `rag.max_chunks_per_doc` + [`_limit_per_doc()`](app/services/knowledge_store.py) |
| 邻接扩展 | [`_expand_adjacent_chunks()`](app/services/knowledge_store.py) |
| 写作域 parent_doc_id 识别 | [`writing_knowledge.py`](app/services/writing_knowledge.py) |

### 配置项（`config.yaml` → `rag`）

```yaml
rag:
  chunk_enabled: true
  chunk_min_chars: 600
  chunk_max_chars: 1200
  chunk_overlap_chars: 150
  chunk_max_chars_writing: 1000
  chunk_overlap_chars_writing: 120
  chunk_max_chars_code: 900
  chunk_overlap_chars_code: 80
  chunk_adjacency_enabled: true
  chunk_adjacency_radius: 1
  max_chunks_per_doc: 2
```

关闭切块：`chunk_enabled: false`。仅对单篇文档禁用：`metadata.skip_chunking: true`。

### 待深化（原第三阶段）

- code 域按符号/代码块切分的专用规则
- 分 domain 的离线 Recall@k / 上下文冗余率评估集
- 检索结果按 `chunk_index` 排序后合并展示（当前以独立 hit 注入 prompt）

---

## 18. 最后总结：为什么 chunk 优化值得优先做

如果说：
- embedding 决定“有没有语义检索能力”
- BM25 决定“精确词命中稳不稳”
- RRF 决定“多路结果融合得好不好”

那么 chunk 决定的是：

**你拿去比较和排序的基本单位，究竟是不是正确的。**

基本单位错了，后面再好的 embedding、BM25、RRF 都只能在“粗粒度对象”上尽量补救。

对当前项目而言，chunk-first 主干已落地；后续优化重点：

1. 扩充分 domain 评估与调参（writing / code / common）
2. code 域符号级切块与 metadata（函数名、路径）
3. 检索命中后的顺序合并展示，进一步降低 prompt 冗余

## 19. 对当前项目的一句话总结

当前项目的 RAG 是一套面向代理系统的实用型检索方案：

- 用本地 MiniLM 做 embedding 语义检索
- 用 BM25 风格算法做关键词检索
- 用 RRF 融合两路结果
- 用 domain 过滤减少噪声
- 后续非常适合继续演进到 chunk-first 检索
- 最后把少量高价值知识喂给后续生成或推理节点

它解决的不是“模型聪不聪明”，而是：

**如何把当前这一步真正需要的知识，从知识库中又快又稳地找出来。**
