# Databend `GROUP_CONCAT(DISTINCT ...)` 内存泄漏尸检报告

日期：2026-03-12

## 1. 摘要

目标 SQL：

```sql
SELECT
    org_code,
    patient_id,
    GROUP_CONCAT(DISTINCT wm_disease_name, '||') AS combined_diseases,
    GROUP_CONCAT(DISTINCT wm_disease_code, '||') AS combined_disease_codes,
    GROUP_CONCAT(DISTINCT disease_code, '||') AS disease_code,
    GROUP_CONCAT(DISTINCT disease_name, '||') AS disease_name
FROM dwd_emr_activity_info
GROUP BY org_code, patient_id;
```

结论：

- 根因不是 `TransformFinalAggregate` 或查询对象整体未释放。
- 根因是 nullable aggregate 的 merge 路径对“已经初始化过的 grouped aggregate state”再次执行了 `init_state`。
- 对 `group_concat_distinct` 这类带手动析构状态的聚合，这次重复初始化会直接覆盖旧状态，旧状态不会被 drop，形成真实泄漏。
- 泄漏点位于 `AggregateNullAdaptor`，不是 `GROUP_CONCAT` 自身逻辑，也不是 jemalloc retain。

## 2. 现象

- 执行 `GROUP_CONCAT(DISTINCT ...)` 后，query 从 `HttpQueryManager` 移除，进程 RSS 和 jemalloc `stats.allocated` 仍持续上升。
- 单次查询约残留 1.4GB，重复执行会累积。
- `/proc/PID/smaps` 显示 Anonymous RSS 持续保留。
- `/proc/PID/mem` 可直接扫到中文疾病名字符串，说明内存里保留的是聚合后的 distinct string state。

## 3. 影响面

- 触发条件是 `DISTINCT` + string-like aggregate state + grouped final merge。
- 典型受影响函数：`group_concat_distinct` / `string_agg_distinct` / `listagg_distinct` 这类走 `DistinctCombinator` 且内部状态需要手动 drop 的聚合。
- 大 group cardinality 时影响很大，因为每个 group 都会多泄漏一份 distinct state。

## 4. 最终根因

实际函数包装链是：

```text
OrNull(
  NullUnaryAdaptor(
    DistinctCombinator(
      group_concat
    )
  )
)
```

正常生命周期：

1. `Payload::append_rows` 为每个新 group 分配 state 内存。
2. `aggr.init_state(...)` 在 group 创建时初始化一次 state。
3. final merge 只应合并已有 state，不应再次初始化。
4. `Payload::drop` 最终调用 `drop_state`，释放带手动析构的聚合状态。

实际出错点：

- 在 grouped final merge 阶段，`CommonNullAdaptor::merge_states` 和 `CommonNullAdaptor::update_flag` 发现 `!get_flag(place)` 时，会再次调用 `self.init_state(place)`。
- 但 `place` 对应的 grouped aggregate state 在 group 创建时已经初始化过。
- 对 `DistinctCombinator(group_concat)`，第二次 `init_state` 会再次执行 `State::new()`，构造新的 `AggregateDistinctStringState`。
- 这个新状态通过 `ptr::write` 直接覆盖旧状态地址，旧状态没有机会执行 drop。
- 最终 `Payload::drop` 只能 drop 到“最后一次写进去的那份状态”，第一次被覆盖的状态永久泄漏。

一句话总结：

`AggregateNullAdaptor` 在 merge 路径里把“未置 flag”错误当成“未初始化”，从而对已初始化 state 做了原地二次初始化，覆盖并泄漏了旧的 distinct string state。

## 5. 关键代码链路

第一次初始化发生在 group 创建时：

```text
Payload::append_rows
  -> aggr.init_state(...)
```

对应文件：

- `src/query/expression/src/aggregate/payload.rs`

`DISTINCT` 状态的初始化：

```text
AggregateDistinctCombinator::init_state
  -> State::new()
  -> AggregateDistinctStringState::new()
  -> ShortStringHashSet::with_capacity(...)
```

对应文件：

- `src/query/functions/src/aggregates/adaptors/aggregate_combinator_distinct.rs`
- `src/query/functions/src/aggregates/aggregate_distinct_state.rs`

错误的二次初始化发生在 final merge：

```text
CommonNullAdaptor::merge_states
  if !get_flag(place) {
      self.init_state(place);   // 错误
  }

CommonNullAdaptor::update_flag
  if !get_flag(place) {
      self.init_state(place);   // 错误
  }
```

对应文件：

- `src/query/functions/src/aggregates/adaptors/aggregate_null_adaptor.rs`

## 6. 为什么只在这类 SQL 上炸得特别明显

这个 SQL 有两个放大器：

1. group 数很多
   - 约 808,877 个 group。

2. 有 4 个 `GROUP_CONCAT(DISTINCT ...)`
   - 每个 group 会维护 4 份 distinct string state。

粗略量级：

- `808,877 * 4 ~= 3.2M` 个 distinct string state。
- 每个 state 内部持有 `ShortStringHashtable`，字符串数据又是疾病名/编码，整体体量很大。
- 每个 group 如果多泄漏 1 份 state，累积起来就是 GB 级。

## 7. 证据链

### 7.1 线上/进程侧证据

- jemalloc `stats.allocated` 在查询结束后不回落。
- `/proc/PID/smaps` 显示 RSS 仍在。
- `/proc/PID/mem` 可以扫到业务字符串。
- heap profile 指向 `AggregateDistinctStringState::new -> ShortStringHashtable::with_capacity`。

### 7.2 最小复现证据

增加了最小回归测试：

- `src/query/functions/tests/it/aggregates/agg_hashtable.rs`
- 测试名：`test_group_concat_distinct_state_drop_after_final_merge`

测试方法：

1. 构造 `group_concat_distinct` 聚合。
2. 先建 partial hashtable。
3. 再通过 `combine_payloads` 合成 final hashtable。
4. 执行 `merge_result`。
5. 分别 drop partial 和 final hashtable。
6. 用 live counter 统计 `AggregateDistinctStringState` 存活数。

修复前观测到的计数模式：

```text
after_partial     = 20000
after_combine     = 60000
after_drop_partial= 40000
after_drop_final  = 20000
```

解释：

- partial 创建了 20000 份 state。
- final merge 正常应该只再创建 20000 份，但实际变成了 40000 份新增。
- 说明在 final merge 中，除了 final hashtable 自己那份 state，又额外重建并覆盖了一次。
- 最终 drop 只能回收后一份，早先被覆盖的那一份永远丢失。

修复后计数模式：

```text
after_partial     = 20000
after_combine     = 40000
after_drop_partial= 20000
after_drop_final  = 0
```

这与正确生命周期完全一致。

## 8. 修复思路

修复原则：

- grouped aggregate state 在 row/group 创建时已经初始化。
- merge 路径只能设置 flag 或 merge，不能再调用 `init_state`。

具体改动：

- 去掉 `CommonNullAdaptor::merge_states` 中的二次 `init_state(place)`。
- 去掉 `CommonNullAdaptor::update_flag` 中的二次 `init_state(place)`。

这不会影响 grouped aggregate 正确性，因为：

- grouped payload 在 `Payload::append_rows` 已经初始化；
- 后续 merge 的目标 state 一直存在；
- `flag` 的职责只是标记“该 state 已经见过有效值”，不是决定是否为该地址分配/构造 state。

## 9. 验证结果

已通过：

```bash
cargo test -p databend-common-functions --test it -- aggregates::agg_hashtable::test_group_concat_distinct_state_drop_after_final_merge --exact
```

已通过：

```bash
cargo clippy -p databend-common-functions --tests -- -D warnings
```

## 10. 风险评估

低风险，但建议补两类回归：

1. unit test
   - 保留当前最小复现测试，专门卡住“final merge 后 state 必须归零”。

2. integration/sql regression
   - 增加一个 `GROUP_CONCAT(DISTINCT ...) GROUP BY ...` 的稳定回归用例。

需要重点关注的兼容点：

- `AggregateNullAdaptor` 还有非 grouped 路径。
- 但当前这次修复针对的是 grouped state 已经由 payload owner 初始化的场景，逻辑与现有内存模型一致。

## 11. 行动项

- 保留最小复现测试，防止回归。
- 把本次 root cause 补回主线调查文档 `find-bug.md`。
- 增加一条 SQL 级回归，覆盖 `GROUP_CONCAT(DISTINCT ...)`。
- 如果后续提交 PR，建议在说明里明确写出：
  - 不是 allocator retain；
  - 不是 query manager 未释放；
  - 是 nullable adaptor merge path 对 grouped state 的重复初始化。

## 12. 一句话结论

这次泄漏的本质是：

`AggregateNullAdaptor` 在 final merge 时错误地对已初始化的 `DistinctCombinator(group_concat)` state 做了第二次原地初始化，导致旧的 `AggregateDistinctStringState` 被覆盖且永远无法 drop，最终表现为 `GROUP_CONCAT(DISTINCT ...)` 查询结束后内存持续保留。
