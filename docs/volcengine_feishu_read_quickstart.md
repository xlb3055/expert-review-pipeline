# 火山引擎飞书读取节点简明教程

这份文档只讲一个节点：

- `feishu-read`

只解决一个问题：

怎么在火山引擎里把飞书多维表的数据读出来。

---

## 1. 这个节点能做什么

`feishu-read` 支持两种模式：

- `record`：读取单条记录
- `query`：按条件查询多条记录

---

## 2. 你需要准备什么

至少准备这 5 个值：

- `App ID`
- `App Secret`
- `AppToken`
- `Table ID`
- `record_id`（只有单条读取时需要）

如果飞书表链接是：

```text
https://xxx.feishu.cn/base/TdiNb881Ja89b9s6FwNcOH1Vn4c?table=tblE2Qdot3No2kUC&view=vewxxxx
```

那么：

- `app-token = TdiNb881Ja89b9s6FwNcOH1Vn4c`
- `table-id = tblE2Qdot3No2kUC`

---

## 3. 页面里每个参数怎么填

| 页面字段 | 怎么填 |
|---|---|
| 读取模式 | `record` 或 `query` |
| 记录ID | 单条读取时填真实 `record_id`，多条查询时留空 |
| 飞书AppID | 你的飞书应用 `App ID` |
| 飞书AppSecret | 你的飞书应用 `App Secret` |
| 飞书多维表AppToken | 飞书表链接里的 `app-token` |
| 飞书数据表ID | 飞书表链接里的 `table-id` |
| 字段映射JSON | 左边是输出键名，右边是真实飞书字段名 |
| 查询条件JSON | 只有 `query` 模式需要 |
| 最大记录数 | 查询模式可填，`0` 表示不限制 |
| 输出文件路径 | 调试时建议填 `/dev/stdout` |

---

## 4. 第一次怎么测最稳

第一次不要一上来就读很多字段。

先做最小验证：

- 读取模式：`record`
- 记录ID：填一条真实存在的 `record_id`
- 字段映射JSON：

```json
{"record_id":"record_id"}
```

- 查询条件JSON：留空
- 最大记录数：`0`
- 输出文件路径：

```text
/dev/stdout
```

这一步的目的只是确认：

- 飞书凭证对了
- 表权限对了
- 这条记录能读到

---

## 5. 单条读取正式示例

如果你的表里有这些字段：

- `任务说明`
- `Trace 文件`
- `提交人`
- `最终产物`
- `审核状态`

那就这样填：

- 读取模式：`record`
- 记录ID：你的真实 `record_id`
- 字段映射JSON：

```json
{
  "record_id": "record_id",
  "task_description": "任务说明",
  "trace_file": "Trace 文件",
  "submitter": "提交人",
  "final_product": "最终产物",
  "review_status": "审核状态"
}
```

- 查询条件JSON：留空
- 最大记录数：`0`
- 输出文件路径：

```text
/dev/stdout
```

注意：

- 左边是你想输出成 JSON 的键名
- 右边必须和飞书字段名完全一致
- `Trace 文件` 中间有空格时，必须原样填写

---

## 6. 多条查询示例

如果你想查“审核状态=待审”的前 5 条，可以这样填：

- 读取模式：`query`
- 记录ID：留空
- 字段映射JSON：

```json
{
  "record_id": "record_id",
  "task_description": "任务说明",
  "review_status": "审核状态"
}
```

- 查询条件JSON：

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
  }
}
```

- 最大记录数：`5`
- 输出文件路径：

```text
/dev/stdout
```

---

## 7. 成功后你会看到什么

如果输出文件路径填的是 `/dev/stdout`，任务日志里会直接打印 JSON。

单条读取时，常见格式类似：

```json
{
  "record_id": "recxxxx",
  "task_description": "一段任务描述",
  "trace_file": "trace.jsonl",
  "_raw_fields": {
    "任务说明": "一段任务描述"
  },
  "_meta": {
    "record_id": "recxxxx",
    "app_token": "app_xxx",
    "table_id": "tbl_xxx"
  }
}
```

多条查询时，常见格式类似：

```json
{
  "items": [
    {
      "record_id": "rec1",
      "task_description": "xxx"
    }
  ],
  "count": 1
}
```

---

## 8. 最常见的 3 个报错

### 8.1 `错误: 缺少参数`

通常是：

- `record` 模式没填 `record_id`
- `fields-json` 没填
- `app-token` 或 `table-id` 没填

### 8.2 `不是合法 JSON`

通常是：

- 用了中文引号
- 末尾多了逗号
- JSON 格式被输入框改坏了

最稳的办法：

- 优先粘贴单行 JSON

### 8.3 `获取飞书记录失败`

通常检查这几项：

- `record_id` 对不对
- App 是否已发布
- App 是否被加成该表协作者
- 多维表权限是否已开通

---

## 9. 当前项目推荐的读取测试参数

如果你现在就在这套“专家考核评审”表上测，推荐先用：

```json
{"record_id":"record_id"}
```

跑通后再换成：

```json
{
  "record_id": "record_id",
  "task_description": "任务说明",
  "trace_file": "Trace 文件",
  "submitter": "提交人",
  "final_product": "最终产物",
  "review_status": "审核状态"
}
```

这就是最稳的读取测试路径。
