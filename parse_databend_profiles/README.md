# parse-databend-profiles

解析 Databend 查询 profile JSON，以树形结构展示各算子的执行统计信息。

## 使用方法

```bash
# 从文件读取
uv run python main.py example.json

# 从 stdin 读取
cat example.json | uv run python main.py
```

## 输出示例

```
Query ID: 019c979ff94b7a30bc1d2d90af03f75d

└── [EvalScalar] sum(number) (#1) / CAST(...)
      cpu time: 7.17µs
    └── [AggregateFinal] sum(number), count(number)
          cpu time: 50.87µs
        └── [AggregatePartial] sum(number), count(number)
              cpu time: 67.15s, output rows: 8, output bytes: 136 B
            └── [TableScan] default.''.'numbers'
                  cpu time: 330.91s, output rows: 10.00B, output bytes: 74.51 GB, bytes scanned: 74.51 GB
```

## 输入格式

标准的 Databend query profile JSON，包含 `query_id`、`profiles` 和 `statistics_desc` 三个字段。可通过 `system.query_profile` 获取。
