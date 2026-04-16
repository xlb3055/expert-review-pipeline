# 火山引擎通用飞书节点

这份文档只回答一件事：

如何把当前仓库里的飞书读写能力，直接当成火山引擎里的两个通用节点来复用。

如果你现在更关心“控制台里具体怎么填、怎么测试”，直接看：

- `docs/volcengine_step_usage_guide.md`

---

## 1. 推荐对外暴露的 2 个模板

如果你在火山引擎里只想让业务方看到 2 个自定义模板，推荐就是：

- `feishu-read`
- `feishu-write`

对应统一入口脚本：

- `scripts/feishu_read_template.py`
- `scripts/feishu_write_template.py`

它们内部再根据 `mode` 分发到底层 4 个动作。

### 模板 A：`feishu-read`

支持两种模式：

- `mode=record`：读取单条记录
- `mode=query`：查询多条记录

### 模板 B：`feishu-write`

支持两种模式：

- `mode=single`：回写单条记录
- `mode=batch`：批量回写多条记录

这样业务方只需要记住：

1. 读
2. 处理
3. 写

---

## 2. 底层有哪四个动作

### 节点 A：读取单条记录字段

脚本：

- `scripts/feishu_fetch_node.py`

作用：

- 给 `record_id`
- 给飞书表参数（`app_id/app_secret/app_token/table_id`）
- 给字段映射（`data_source.fields`）
- 读取这一条记录里一个或多个字段
- 输出成 JSON 文件或 stdout

### 节点 B：回写单条记录字段

脚本：

- `scripts/feishu_write_node.py`

作用：

- 给 `record_id`
- 给飞书表参数（`app_id/app_secret/app_token/table_id`）
- 给输出字段映射（`data_sink.field_mapping`）
- 给待写入 JSON 数据
- 将一个或多个字段回写到飞书

### 节点 C：查询多条记录字段

脚本：

- `scripts/feishu_query_node.py`

作用：

- 给飞书表参数（`app_id/app_secret/app_token/table_id`）
- 给字段映射（`data_source.fields`）
- 给查询条件（`search_body_json`）
- 查询多条记录
- 输出统一 JSON 数组结果

### 节点 D：批量回写多条记录字段

脚本：

- `scripts/feishu_batch_write_node.py`

作用：

- 给飞书表参数（`app_id/app_secret/app_token/table_id`）
- 给输出字段映射（`data_sink.field_mapping`）
- 给待回写的多条 JSON 数据
- 自动按批次回写多条记录

---

## 3. 读取节点怎么用

### 2.1 最小命令

```bash
python3 scripts/feishu_fetch_node.py \
  --record-id recxxxx \
  --app-id "$FEISHU_APP_ID" \
  --app-secret "$FEISHU_APP_SECRET" \
  --app-token "$BITABLE_APP_TOKEN" \
  --table-id "$BITABLE_TABLE_ID" \
  --fields-json '{"task_description":"任务说明","trace_file":"Trace 文件"}' \
  --output-file /workspace/input.json
```

### 2.2 输出格式

输出 JSON 与当前 `ctx_data.json` 风格保持一致，例如：

```json
{
  "task_description": "请帮我审一段内容",
  "_raw_task_description": "请帮我审一段内容",
  "trace_file": "trace.jsonl",
  "_raw_trace_file": [
    {
      "file_token": "boxcnxxx"
    }
  ],
  "_raw_fields": {
    "任务说明": "请帮我审一段内容",
    "Trace 文件": [
      {
        "file_token": "boxcnxxx"
      }
    ]
  },
  "_meta": {
    "record_id": "recxxxx",
    "app_token": "app_token_xxx",
    "table_id": "tblxxxx"
  }
}
```

这意味着：

- 你可以直接把这个 JSON 交给已有业务脚本继续处理
- 附件字段、超链接字段也保留了原始结构，方便后续下载或提取 URL

---

## 4. 回写节点怎么用

### 3.1 最小命令

```bash
python3 scripts/feishu_write_node.py \
  --record-id recxxxx \
  --app-id "$FEISHU_APP_ID" \
  --app-secret "$FEISHU_APP_SECRET" \
  --app-token "$BITABLE_APP_TOKEN" \
  --table-id "$BITABLE_TABLE_ID" \
  --field-mapping-json '{"review_status":"审核状态","machine_review_note":"机审说明"}' \
  --status-mapping-json '{"pass":"审核通过","reject":"已拒绝"}' \
  --data-file /workspace/result.json
```

### 3.2 输入数据支持两种格式

#### 直接平铺

```json
{
  "review_status": "pass",
  "machine_review_note": "整体通过"
}
```

#### 嵌套在 `data` 下

```json
{
  "data": {
    "review_status": "pass",
    "machine_review_note": "整体通过"
  }
}
```

---

## 5. 火山引擎怎么配置

如果你对外只暴露 2 个模板，建议火山引擎里实际配置成下面这两个命令。

### 模板 1：`feishu-read`

```bash
python3 scripts/feishu_read_template.py \
  --mode "${FEISHU_READ_MODE}" \
  --record-id "${RECORD_ID:-}" \
  --app-token "$BITABLE_APP_TOKEN" \
  --table-id "$BITABLE_TABLE_ID" \
  --fields-json "$DATA_SOURCE_FIELDS_JSON" \
  --search-body-json "${SEARCH_BODY_JSON:-}" \
  --max-records "${MAX_RECORDS:-0}" \
  --output-file "${READ_OUTPUT_FILE:-/workspace/input.json}"
```

推荐模板参数：

- `FEISHU_READ_MODE`
- `RECORD_ID`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `BITABLE_APP_TOKEN`
- `BITABLE_TABLE_ID`
- `DATA_SOURCE_FIELDS_JSON`
- `SEARCH_BODY_JSON`
- `MAX_RECORDS`
- `READ_OUTPUT_FILE`

### 模板 2：`feishu-write`

```bash
python3 scripts/feishu_write_template.py \
  --mode "${FEISHU_WRITE_MODE}" \
  --record-id "${RECORD_ID:-}" \
  --app-token "$BITABLE_APP_TOKEN" \
  --table-id "$BITABLE_TABLE_ID" \
  --field-mapping-json "$DATA_SINK_FIELD_MAPPING_JSON" \
  --status-mapping-json "${STATUS_MAPPING_JSON:-}" \
  --data-file "${WRITE_DATA_FILE:-/workspace/result.json}" \
  --chunk-size "${BATCH_CHUNK_SIZE:-500}" \
  --output-file "${WRITE_OUTPUT_FILE:-/workspace/write_summary.json}"
```

推荐模板参数：

- `FEISHU_WRITE_MODE`
- `RECORD_ID`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `BITABLE_APP_TOKEN`
- `BITABLE_TABLE_ID`
- `DATA_SINK_FIELD_MAPPING_JSON`
- `STATUS_MAPPING_JSON`
- `WRITE_DATA_FILE`
- `BATCH_CHUNK_SIZE`
- `WRITE_OUTPUT_FILE`

### 5.1 上传火山自定义步骤时的 YAML 规则

火山引擎这块有几个容易踩坑的地方：

- 顶层 `step` 不是固定值，而是模板唯一标识，建议用中横线命名，比如 `feishu-read`
- 顶层必须带 `version`，例如 `1.0.0`
- `category` 不能写中文，命令执行类模板要写 `Command`
- `inputs[].name` 官方约束是英文/数字/中横线，尽量不要用下划线
- `script` 第一行建议显式写 shebang，例如 `#!/bin/sh`，否则在 Tekton 运行时可能报 `exec format error`

所以这两个模板上传时，建议直接使用仓库里的成品 YAML：

- `ci-steps/feishu-read/step.yaml`
- `ci-steps/feishu-write/step.yaml`

### 两个模板的典型用法

- 读单条 -> 写单条
  读模板：`FEISHU_READ_MODE=record`
  写模板：`FEISHU_WRITE_MODE=single`
- 查多条 -> 批量写多条
  读模板：`FEISHU_READ_MODE=query`
  写模板：`FEISHU_WRITE_MODE=batch`
- 查多条 -> 中间筛选 -> 单条写
  读模板：`FEISHU_READ_MODE=query`
  写模板：`FEISHU_WRITE_MODE=single`

---

### 下面是底层 4 个动作的直连命令

### 节点 1：飞书取数

```bash
python3 scripts/feishu_fetch_node.py \
  --record-id "$RECORD_ID" \
  --app-token "$BITABLE_APP_TOKEN" \
  --table-id "$BITABLE_TABLE_ID" \
  --fields-json "$DATA_SOURCE_FIELDS_JSON" \
  --output-file /workspace/input.json
```

这里可以把这些变量配到火山引擎：

- `RECORD_ID`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `BITABLE_APP_TOKEN`
- `BITABLE_TABLE_ID`
- `DATA_SOURCE_FIELDS_JSON`

示例：

```json
{"task_description":"任务说明","trace_file":"Trace 文件","final_product":"最终产物"}
```

### 节点 2：飞书写数

```bash
python3 scripts/feishu_write_node.py \
  --record-id "$RECORD_ID" \
  --app-token "$BITABLE_APP_TOKEN" \
  --table-id "$BITABLE_TABLE_ID" \
  --field-mapping-json "$DATA_SINK_FIELD_MAPPING_JSON" \
  --status-mapping-json "$STATUS_MAPPING_JSON" \
  --data-file /workspace/result.json
```

这里可以把这些变量配到火山引擎：

- `DATA_SINK_FIELD_MAPPING_JSON`
- `STATUS_MAPPING_JSON`

示例：

```json
{"review_status":"审核状态","machine_review_note":"机审说明","machine_review_remark":"机审备注"}
```

### 节点 3：飞书查多条

```bash
python3 scripts/feishu_query_node.py \
  --app-token "$BITABLE_APP_TOKEN" \
  --table-id "$BITABLE_TABLE_ID" \
  --fields-json "$DATA_SOURCE_FIELDS_JSON" \
  --search-body-json "$SEARCH_BODY_JSON" \
  --max-records "${MAX_RECORDS:-0}" \
  --output-file /workspace/query_result.json
```

示例 `SEARCH_BODY_JSON`：

```json
{
  "filter": {
    "conjunction": "and",
    "conditions": [
      {
        "field_name": "审核状态",
        "operator": "is",
        "value": ["待审"]
      }
    ]
  },
  "sort": [
    {
      "field_name": "提交时间",
      "desc": true
    }
  ]
}
```

### 节点 4：飞书批量写数

```bash
python3 scripts/feishu_batch_write_node.py \
  --app-token "$BITABLE_APP_TOKEN" \
  --table-id "$BITABLE_TABLE_ID" \
  --field-mapping-json "$DATA_SINK_FIELD_MAPPING_JSON" \
  --status-mapping-json "$STATUS_MAPPING_JSON" \
  --data-file /workspace/batch_result.json \
  --chunk-size "${BATCH_CHUNK_SIZE:-500}"
```

批量输入支持两种格式：

#### 直接数组

```json
[
  {"record_id":"rec1","review_status":"pass"},
  {"record_id":"rec2","review_status":"reject"}
]
```

#### `items` / `records` 包装

```json
{
  "items": [
    {"record_id":"rec1","data":{"review_status":"pass"}},
    {"record_id":"rec2","data":{"review_status":"reject"}}
  ]
}
```

---

## 6. 这意味着什么

做到这里之后，火山引擎上的复用边界就很清晰了：

1. 单条取数节点只负责“从任意飞书表的指定记录中取指定字段”
2. 多条查询节点只负责“按条件查多条记录并输出标准 JSON”
3. 中间业务节点只负责“处理 JSON”
4. 单条写数节点只负责“把 JSON 中的指定键回写到指定记录”
5. 批量写数节点只负责“把多条 JSON 中的指定键批量回写到多条记录”

这样一来：

- 核心业务逻辑和飞书 I/O 解耦
- 不同项目只需要换字段映射和中间处理逻辑
- 不需要为每个项目重写一遍飞书访问代码

---

## 7. 当前边界

当前通用节点支持的是：

- 任意飞书多维表
- 单条记录读取
- 多条记录查询
- 单条记录回写
- 多条记录批量回写
- 一个或多个字段

当前还不包含：

- 非飞书数据源（MySQL / HTTP / S3 / Kafka 等）
- 批量创建新记录

如果后面需要，可以在这个模式上继续扩成：

- `feishu_query_node.py`
- `feishu_batch_write_node.py`
- `http_fetch_node.py`
- `s3_read_node.py`
