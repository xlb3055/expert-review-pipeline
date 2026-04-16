# 火山引擎飞书自定义步骤使用教程

这份文档是给实际使用者看的。

目标只有一个：

把已经上传好的两个火山引擎自定义步骤真正跑起来，并且能独立完成测试。

当前对外暴露的两个步骤是：

- `feishu-read`
- `feishu-write`

当前镜像版本示例：

- `meetchances-cn-beijing.cr.volces.com/ci/feishu-io-template:1.0.1`

---

## 1. 这两个步骤分别做什么

### 1.1 `feishu-read`

用于从飞书多维表读取数据。

支持两种模式：

- `record`：读取单条记录
- `query`：按条件查询多条记录

### 1.2 `feishu-write`

用于把数据回写到飞书多维表。

支持两种模式：

- `single`：回写单条记录
- `batch`：批量回写多条记录

---

## 2. 使用前准备

在开始测试前，先确认下面几项已经准备好：

- 已经在火山引擎“自定义步骤”里上传了 `feishu-read` 和 `feishu-write`
- 步骤版本和镜像 tag 对齐，例如都使用 `1.0.1`
- 飞书应用已经开通了多维表相关权限
- 飞书应用已经被加入目标表格为协作者
- 你已经拿到了下面这些参数

必需参数：

- `App ID`
- `App Secret`
- `AppToken`
- `Table ID`

单条读取 / 单条写入时还需要：

- `record_id`

---

## 3. 参数是什么意思

### 3.1 `feishu-read` 参数说明

| 页面字段 | 含义 | 是否必填 | 说明 |
|---|---|---:|---|
| 读取模式 | `record` 或 `query` | 是 | 单条读取或多条查询 |
| 记录ID | 目标记录的 `record_id` | `record` 模式必填 | `query` 模式留空 |
| 飞书AppID | 飞书应用 ID | 是 | 例如 `cli_xxx` |
| 飞书AppSecret | 飞书应用 Secret | 是 | 建议用密文变量管理 |
| 飞书多维表AppToken | 多维表 AppToken | 是 | 从表格链接中提取 |
| 飞书数据表ID | 目标表 ID | 是 | 从表格链接中提取 |
| 字段映射JSON | 输出字段映射 | 是 | 左边是输出键名，右边是真实飞书字段名 |
| 查询条件JSON | 查询过滤条件 | `query` 模式可填 | `record` 模式留空 |
| 最大记录数 | 最多查询多少条 | 否 | `0` 表示不限制 |
| 输出文件路径 | 输出写到哪里 | 否 | 调试建议填 `/dev/stdout` |

### 3.2 `feishu-write` 参数说明

| 页面字段 | 含义 | 是否必填 | 说明 |
|---|---|---:|---|
| 写入模式 | `single` 或 `batch` | 是 | 单条写入或批量写入 |
| 记录ID | 目标记录的 `record_id` | `single` 模式必填 | `batch` 模式留空 |
| 飞书AppID | 飞书应用 ID | 是 | 例如 `cli_xxx` |
| 飞书AppSecret | 飞书应用 Secret | 是 | 建议用密文变量管理 |
| 飞书多维表AppToken | 多维表 AppToken | 是 | 从表格链接中提取 |
| 飞书数据表ID | 目标表 ID | 是 | 从表格链接中提取 |
| 输出字段映射JSON | 写回字段映射 | 是 | 左边是输入数据里的键，右边是真实飞书字段名 |
| 状态映射JSON | 状态值映射 | 否 | 例如把 `pass` 转成 `审核通过` |
| 输入数据文件路径 | 要写回的数据文件 | 是 | 建议放在 `/workspace` 下 |
| 批量写入分片大小 | 每批写多少条 | 否 | 默认 `500` |
| 输出文件路径 | 输出摘要路径 | 否 | 调试建议填 `/dev/stdout` |

---

## 4. 如何从飞书链接里提取参数

假设你的飞书链接长这样：

```text
https://xxx.feishu.cn/base/TdiNb881Ja89b9s6FwNcOH1Vn4c?table=tblE2Qdot3No2kUC&view=vewxxxx
```

可以提取出：

- `app-token = TdiNb881Ja89b9s6FwNcOH1Vn4c`
- `table-id = tblE2Qdot3No2kUC`

如果你已经打开了一条记录并拿到了记录 ID，例如：

```text
recvgQSZO3YKLR
```

那它就是单条读写时的 `record_id`。

---

## 5. 第一次测试推荐顺序

建议严格按下面顺序测试：

1. 先测 `feishu-read` 的 `record` 模式
2. 再测 `feishu-read` 的 `query` 模式
3. 再测 `feishu-write` 的 `single` 模式
4. 最后测 `feishu-write` 的 `batch` 模式

这样排查最省时间。

原因很简单：

- 先确认连接和权限是通的
- 再确认查询语法是对的
- 最后再测试写回，避免误写数据

---

## 6. 测试 `feishu-read`

### 6.1 单条读取：最小可用测试

这是最推荐的第一步。

参数这样填：

- 读取模式：`record`
- 记录ID：填一条真实存在的 `record_id`
- 字段映射JSON：

```json
{"record_id":"record_id"}
```

- 查询条件JSON：留空
- 最大记录数：`0`
- 输出文件路径：`/dev/stdout`

这一步的目的不是业务验证，而是只验证三件事：

- App 凭证正确
- 表权限正确
- 这条记录可以被读到

### 6.2 单条读取：正式测试示例

如果你的表里有这些字段：

- `任务说明`
- `Trace 文件`
- `提交人`
- `最终产物`
- `审核状态`

那可以填：

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

注意：

- 左边是你输出 JSON 想看到的键名
- 右边必须和飞书字段名完全一致
- `Trace 文件` 中间有空格时必须照写

### 6.3 运行成功后你会看到什么

如果输出文件路径填的是 `/dev/stdout`，日志里会直接看到 JSON，例如：

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

### 6.4 多条查询测试

把读取模式改成：

```text
query
```

然后：

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
- 输出文件路径：`/dev/stdout`

成功后会返回：

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

## 7. 测试 `feishu-write`

写入测试建议优先找一个专门测试用字段，比如：

- `机审说明`
- `机审备注`
- `测试字段`

不要第一次就写正式状态字段，避免误伤业务数据。

### 7.1 单条写入测试

先在同一个 Task 里准备一个输入文件，例如上一阶段写出：

`/workspace/result.json`

文件内容示例：

```json
{
  "review_status": "pass",
  "machine_review_note": "这是一次火山引擎写入测试"
}
```

然后在 `feishu-write` 填：

- 写入模式：`single`
- 记录ID：目标记录的 `record_id`
- 输出字段映射JSON：

```json
{
  "review_status": "审核状态",
  "machine_review_note": "机审说明"
}
```

- 状态映射JSON：

```json
{
  "pass": "审核通过",
  "reject": "已拒绝"
}
```

- 输入数据文件路径：

```text
/workspace/result.json
```

- 输出文件路径：

```text
/dev/stdout
```

成功后日志里会看到类似摘要：

```json
{
  "record_id": "recxxxx",
  "updated_count": 2,
  "dry_run": false
}
```

### 7.2 批量写入测试

假设准备了 `/workspace/batch_result.json`：

```json
[
  {
    "record_id": "rec1",
    "review_status": "pass"
  },
  {
    "record_id": "rec2",
    "review_status": "reject"
  }
]
```

那 `feishu-write` 可以这样填：

- 写入模式：`batch`
- 记录ID：留空
- 输出字段映射JSON：

```json
{
  "review_status": "审核状态"
}
```

- 状态映射JSON：

```json
{
  "pass": "审核通过",
  "reject": "已拒绝"
}
```

- 输入数据文件路径：

```text
/workspace/batch_result.json
```

- 批量写入分片大小：`100`
- 输出文件路径：`/dev/stdout`

成功后会看到：

```json
{
  "record_count": 2,
  "chunk_count": 1,
  "dry_run": false
}
```

---

## 8. `/workspace` 和 `/dev/stdout` 怎么用

### 8.1 `/dev/stdout`

调试阶段非常推荐。

作用：

- 让输出 JSON 直接打印到日志
- 不用再进容器里找文件
- 最适合验证“我到底读到了什么 / 写回了什么”

建议：

- `feishu-read` 测试时优先填 `/dev/stdout`
- `feishu-write` 测试时也先填 `/dev/stdout`

### 8.2 `/workspace`

适用于同一个 Task 内前后步骤传文件。

例如：

- 步骤 A：`/workspace/input.json`
- 步骤 B：读取 `/workspace/input.json`

注意：

- 同一个 Task 内可以共享 `/workspace`
- 不同 Task 之间不能直接共享 `/workspace`
- 跨 Task 传递要用 Artifact 或别的显式机制

---

## 9. 常见报错怎么排查

### 9.1 `exec format error`

典型报错：

```text
fork/exec /tekton/scripts/script-xxx: exec format error
```

优先检查：

- 镜像是不是 `linux/amd64`
- `step.yaml` 的 `script` 第一行有没有 `#!/bin/sh`
- 是否真的重新上传了新版本模板
- 流水线里是不是还在用旧节点 / 旧版本

### 9.2 `错误: 缺少参数`

典型原因：

- `record` 模式没填 `record_id`
- `single` 模式没填 `record_id`
- `fields-json` / `field-mapping-json` 没填
- `data-file` 没填

### 9.3 `不是合法 JSON`

典型原因：

- 粘贴时用了中文引号
- JSON 末尾多了逗号
- 把 JSON 写成了多行但平台输入框自动改掉了格式

建议：

- 一律先用在线 JSON 校验器检查
- 优先粘贴单行 JSON

### 9.4 `获取飞书记录失败`

优先检查：

- `record_id` 是否真实存在
- App 是否被加入该表为协作者
- App 是否已发布
- 权限是否开通

### 9.5 推了新镜像但平台还是旧代码

优先检查：

- 镜像 tag 是否递增了，例如 `1.0.1 -> 1.0.2`
- `step.yaml` 的 `image` 是否也同步更新
- 自定义步骤是否重新上传
- 流水线节点是否重新添加

---

## 10. 推荐测试清单

建议每次改完后按这个顺序过一遍：

1. `feishu-read` `record` 模式，字段映射只填 `{"record_id":"record_id"}`
2. `feishu-read` `record` 模式，验证正式字段映射
3. `feishu-read` `query` 模式，限制 `max-records=5`
4. `feishu-write` `single` 模式，写测试字段
5. `feishu-write` `batch` 模式，先拿 2 条记录试

只要这 5 步都能过，这两个通用节点基本就可以在项目里复用了。

---

## 11. 当前项目建议字段映射

如果你当前是在“专家考核评审”这套表上测试，优先推荐下面这组读映射：

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

对应的单条写入测试映射：

```json
{
  "review_status": "审核状态",
  "machine_review_note": "机审说明",
  "machine_review_remark": "机审备注"
}
```

状态映射可以先用：

```json
{
  "pass": "审核通过",
  "reject": "已拒绝",
  "manual_review": "待人工复核"
}
```

---

## 12. 发布更新时怎么做

如果你后面还要继续迭代这两个步骤，建议固定遵循下面流程：

1. 修改代码或镜像内容
2. 递增镜像 tag，例如 `1.0.1 -> 1.0.2`
3. 更新 `step.yaml` 中的 `version`
4. 更新 `step.yaml` 中的 `image`
5. 重新上传自定义步骤
6. 重新在流水线里添加新版本节点

这样最不容易踩缓存坑。
