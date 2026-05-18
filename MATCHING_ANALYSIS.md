# Translation Memory 查询实现分析

## 1. 当前实现做了什么

当前匹配流程的核心代码在：

- `app/services/sentence_splitter.py`
- `app/services/normalizer.py`
- `app/services/matcher.py`

整体流程如下：

1. 读取上传文件内容
2. 先按换行分行
3. 每一行再按句末符号拆句
4. 对每个句子做标准化
5. 生成用于匹配的键
6. 对本次上传句子去重
7. 先做批量精确匹配
8. 未命中部分再做批量模糊匹配
9. 最后把结果回填到原始顺序

这和最早版本最大的区别是：

- 以前是“每句单独查数据库”
- 现在是“整批句子一起查数据库”

## 2. 断句逻辑

当前断句逻辑不是只靠 `。？！!?`。

它现在是两层：

1. 先按换行拆分
2. 如果一行里存在句末符号，再继续按句末符号拆
3. 如果一行里没有句末符号，就把整行当成一个句子

这就是为什么下面这种文本现在能正常拆开：

```text
欢迎使用翻译记忆库系统
让我来解释一下原因的实施方法与策略 (4)
这个错误需要立即纠正 (4)
```

会得到：

- `欢迎使用翻译记忆库系统`
- `让我来解释一下原因的实施方法与策略 (4)`
- `这个错误需要立即纠正 (4)`

## 3. 标准化逻辑

当前标准化分成两类：

### 3.1 通用标准化

用于清洗文本：

- 去除首尾空白
- 压缩连续空白
- 清理不可见字符

### 3.2 匹配标准化

用于提升命中率：

- 去除尾部句末标点
- 例如把以下内容归一为同一匹配键

```text
软件架构遵循微服务模式
软件架构遵循微服务模式.
软件架构遵循微服务模式。
```

这就是你之前那条 `软件架构遵循微服务模式.` 现在能命中的直接原因。

## 4. 当前查询实现方式

## 4.1 预处理阶段

每个句子会生成 3 个关键值：

- `source_sentence`
  原始句子，用于最终展示
- `normalized_sentence`
  常规标准化后的句子
- `match_text`
  用于匹配的归一化句子，通常会去掉尾部句末标点

同时还会生成：

- `source_hash`
  基于标准化文本的 SHA-256

## 4.2 去重阶段

系统会先按 `match_text` 去重。

也就是说，如果上传的 1000 句里有大量重复句子，数据库只查一次，最后再把结果复制回所有重复项。

这个步骤非常重要，因为真实文档里重复句子很常见。

## 4.3 精确匹配阶段

当前 exact match 不是只查一种键，而是三层回退：

1. `source_hash`
2. `source_normalized`
3. `source_text`

而且全部都是批量查，不是逐句查。

### 为什么要加 `source_text` 回退

因为你当前库里已有不少脏数据：

- `source_text` 是中文
- 但 `source_normalized` 却是英文
- `source_hash` 也不可靠

这会导致“数据库里明明有原文，但 hash 和 normalized 都命不中”。

所以现在临时加了 `source_text` 的批量 exact 回退，来保证库里已有脏数据时也尽量命中。

## 4.4 模糊匹配阶段

未命中的句子不会一个个查，而是走批量模糊匹配。

实现方式是：

- 用 `VALUES (...)` 把一批待查句子送进 SQL
- 再对每个句子做 `LATERAL` 子查询
- 用 `pg_trgm` 的 `%` 先筛候选
- 用 `similarity()` 排序取 Top 1

简化后的思路相当于：

```sql
WITH input(query_text) AS (
  VALUES
    ('句子1'),
    ('句子2'),
    ('句子3')
)
SELECT ...
FROM input
LEFT JOIN LATERAL (
  SELECT ...
  FROM translation_memory tm
  WHERE tm.source_normalized % input.query_text
  ORDER BY similarity(tm.source_normalized, input.query_text) DESC
  LIMIT 1
) matched ON TRUE;
```

这比 Python 循环 1000 次、每次都发 SQL 快得多。

## 5. 为什么现在会快很多

这次提速是“真实可解释”的，不是错觉。

### 5.1 数据库往返次数大幅下降

原始版本的最坏情况：

- 1000 句
- 每句 1 次 exact
- 未命中再 1 次 fuzzy

最坏接近：

- 2000 次数据库查询

现在的版本：

- exact 按批次查
- fuzzy 按批次查
- 默认 chunk 大小：
  - exact: 1000
  - fuzzy: 200

如果是 1000 句，理论上数据库往返通常会变成：

- exact 大约 1 次
- fuzzy 大约 5 次

也就是从“接近 2000 次”下降到“个位数级别”

### 5.2 重复句子只查一次

如果上传文档中有重复句子，收益会更明显。

例如：

- 原始句子数 1000
- 去重后只剩 350

后续 exact/fuzzy 都只按 350 个句子查询。


### 5.3 模糊匹配利用了数据库能力

现在不是把模糊计算拆散到 Python 里，而是交给 PostgreSQL 一次性处理一批输入。

这能减少：

- Python 循环开销
- ORM 构造开销
- SQL 编译开销
- 网络往返开销

## 6. 为什么你会怀疑“快得不真实”

这种提速看起来夸张，主要是因为原始实现确实非常低效。

原来慢，不是因为：

- 1 万条 TM 特别大

而是因为：

- 1000 句上传时做了太多次数据库往返

对 PostgreSQL 来说：

- 1 万条
- 甚至 10 万条

只要索引合理，批量查询通常都不算大问题。

所以现在你感受到的“突然变快”，本质上是：

- 从低效率查询模式
- 切换到了更接近数据库正确用法的模式

## 7. 现阶段仍然存在的限制

### 7.1 你库里的 `source_hash` 和 `source_normalized` 有脏数据

这是当前最大的稳定性风险。

已经观察到的情况：

- `source_text` 是中文
- `source_normalized` 却保存成英文译文式文本

这会导致：

- hash exact 失效
- normalized exact 失效
- trigram fuzzy 也可能失效

当前代码虽然已经用 `source_text` 回退兜住一部分问题，但这不是最优状态。

### 7.2 `source_text` 精确回退需要索引支持

为了兼容现有脏数据，当前 exact 增加了：

- `source_text IN (...)`
- `source_normalized IN (...)`

如果库再变大，而数据库上没有对应 btree 索引，这部分也会慢。

### 7.3 模糊匹配仍然是最贵的一段

即便已经批量化，fuzzy 仍然比 exact 贵得多。

如果未来变成：

- TM 50 万
- 上传句子 5000

那下一轮优化就要看：

- 执行计划
- 索引类型
- 阈值设计
- 是否增加缓存

## 8. 建议立刻做的数据库修复

### 8.1 重建派生字段

项目里已经加了脚本：

- `scripts/rebuild_tm_fields.py`

建议执行一次，把当前库里的：

- `source_hash`
- `source_normalized`

按现在的正确规则全部重算。

### 8.2 补索引

建议确保数据库里至少有这些索引：

- `source_hash`
- `source_text`
- `source_normalized`
- `source_normalized gin_trgm_ops`

### 8.3 更新统计信息

执行：

```sql
ANALYZE translation_memory;
```

否则 PostgreSQL 可能拿不到好的执行计划。

## 9. 现在的实现是否“真实可靠”

结论是：

- 提速本身是真实的
- 原理上也是合理的
- 不是假快

但目前有一个现实前提：

- 你的数据库字段质量要跟上

如果 `source_hash/source_normalized` 长期是脏数据，当前代码虽然还能依靠 `source_text` 回退兜住部分问题，但会拖累命中质量和长期性能。

## 10. 建议下一步

建议按这个顺序继续：

1. 运行 `scripts/rebuild_tm_fields.py`
2. 在库里补 `source_text/source_normalized` btree 索引
3. 执行 `ANALYZE translation_memory`
4. 再用你那份 1000 行文本做一次真实压测
5. 如果还要继续提速，再上 `EXPLAIN ANALYZE`

## 11. 一句话总结

当前变快的核心原因不是“算法神奇”，而是：

- 上传句子先去重
- exact 从逐句查询改成批量查询
- fuzzy 从逐句查询改成批量 SQL
- 句末标点归一化提升了命中率
- 按换行分行修复了无标点文本的拆句问题

所以这次提速是工程实现上的收益，不是偶然现象。
