# 专家考核产物自动审核流水线 —Phase 1：基础搭建教程

## 前言

本教程覆盖从零开始搭建流水线的前两个阶段：代码准备和飞书基础设施配通。完成本教程后，你的代码将能正常读写飞书多维表格，为后续的粗筛、AI 评审打好基础。

---

## Step 1：代码编写 + 推送 GitHub

### 1.1 这一步在干什么

编写流水线所需的全部代码，推送到你自己的 GitHub 仓库。先在独立仓库跑通流程，后续再考虑迁移到公司仓库。

### 1.2 仓库结构

```
expert-review-pipeline/
  feishu_utils.py                 # 飞书 API 工具函数（token 获取、记录读写、附件下载）
  trace_parser.py                 # Trace JSONL 解析器（提取轮次、模型、工具调用统计）
  pre_screen.py                   # 第一层：脚本粗筛（6 项硬性校验）
  ai_review.py                    # 第二层：AI 评审（Daytona 沙箱 + Claude Code 评分）
  writeback.py                    # 第三层：结果回填飞书多维表格
  prompt_expert_review.md         # AI 评审的 Prompt（3 个评分维度）
  schema_expert_review.json       # AI 评审的输出 JSON Schema
  run_expert_review_pipeline.sh   # 流水线入口 Shell 脚本
```

### 1.3 各文件的作用

| 文件 | 一句话说明 |
|------|-----------|
| `feishu_utils.py` | 封装飞书 API 调用，提供 `get_feishu_token()`、`get_record()`、`update_record()`、`download_attachment()` 等函数，其他脚本都依赖它 |
| `trace_parser.py` | 解析 Claude Code 生成的 `.jsonl` trace 日志，统计对话轮次、识别模型名称、检测工具调用 |
| `pre_screen.py` | 粗筛脚本，执行 6 项硬性检查（trace 是否存在、轮次是否够、模型是否为 opus 等），不通过直接拒绝 |
| `ai_review.py` | 在 Daytona 云沙箱中启动 Claude Code，让 AI 从 3 个维度（任务复杂度、迭代质量、专业判断）给专家评分 |
| `writeback.py` | 读取粗筛和 AI 评审的结果 JSON，提取分数，判定最终结论（通过/拒绝/待复核），回填到飞书表格 |
| `prompt_expert_review.md` | AI 评审时给 Claude 的系统提示词，定义了 3 个评分维度的详细标准 |
| `schema_expert_review.json` | 约束 AI 的输出格式，确保返回结构化的评分 JSON |
| `run_expert_review_pipeline.sh` | 流水线的入口，按顺序调用粗筛 → AI 评审 → 回填，供火山引擎流水线执行 |

### 1.4 操作步骤

```bash
# 1. 在 GitHub 上创建空仓库（名称如 expert-review-pipeline）

# 2. 本地克隆
git clone git@github.com:<你的用户名>/expert-review-pipeline.git
cd expert-review-pipeline

# 3. 把写好的 8 个文件放进来（或直接在此目录编写）

# 4. 提交并推送
git add .
git commit -m "feat: 专家考核产物自动审核流水线"
git push -u origin main
```

---

## Step 2：创建飞书多维表格

### 2.1 这一步在干什么

创建流水线的"数据库"。整个流水线的数据都存在飞书多维表格中——专家在这里提交产物，脚本从这里读取数据，审核结果也回填到这里。

### 2.2 操作步骤

1. 打开飞书，新建一个**多维表格**，命名为"专家考核评审"
2. 按下表创建 20 个字段：

| # | 字段名 | 类型 | 用途 |
|---|--------|------|------|
| 1 | `record_id` | 公式: `RECORD_ID()` | 每行的唯一 ID，流水线靠它定位数据 |
| 2 | `专家姓名` | 文本 | 专家名称 |
| 3 | `专家ID` | 文本 | 专家唯一标识 |
| 4 | `岗位方向` | 单选 | 选项: `Coding`, `网页设计`, `白领/Agent` |
| 5 | `任务描述` | 文本 | 专家写的 Prompt（≥100字） |
| 6 | `Trace文件` | 附件 | 专家上传的 .jsonl trace 文件 |
| 7 | `最终产物` | 超链接 | GitHub 链接或其他在线链接 |
| 8 | `最终附件` | 附件 | 上传的 zip/pdf 等文件（和链接二选一） |
| 9 | `提交时间` | 日期 | 专家提交时间 |
| 10 | `开始评审` | 按钮 | 点击触发审核流水线（后续配置） |
| 11 | `粗筛状态` | 单选 | 选项: `待审`, `通过`, `拒绝`, `待人工复核` |
| 12 | `粗筛详情` | 文本 | 粗筛 6 项检查的详细结果 |
| 13 | `AI评审状态` | 单选 | 选项: `待审`, `通过`, `待人工复核`, `拒绝` |
| 14 | `AI评审结果` | 文本 | AI 评分完整 JSON 输出 |
| 15 | `任务复杂度` | 数字 | AI 评分 (0-3) |
| 16 | `迭代质量` | 数字 | AI 评分 (0-3) |
| 17 | `专业判断` | 数字 | AI 评分 (0-4) |
| 18 | `总分` | 公式: `任务复杂度 + 迭代质量 + 专业判断` | 自动求和 (0-10) |
| 19 | `最终结论` | 单选 | 选项: `通过`, `拒绝`, `待人工复核` |
| 20 | `人工备注` | 文本 | 人工审核员的备注 |

### 2.3 字段分组说明

这 20 个字段按角色分为 3 组：

- **专家填写**（1-9）：专家提交考核产物时填写
- **按钮触发**（10）：点击后启动自动审核
- **系统回填**（11-20）：由流水线代码自动填入审核结果

### 2.4 验证

建好后在表格里随便手动填一行数据，确认 `record_id` 公式能正常显示（类似 `recXXXXXXXXXX`），`总分` 公式能自动求和。

---

## Step 3：创建飞书自建应用 + 配置权限

### 3.1 这一步在干什么

你的 Python 代码要通过 API 读写飞书表格，飞书需要先验证"你是谁"以及"你有权做什么"。这一步就是注册一个"应用身份"并授予它操作权限。

整个授权机制可以用这张图理解：

```
代码要读写飞书表格
    │
    ├─ 凭什么读写？→ App ID + App Secret（身份凭证）
    ├─ 能做什么？  → 权限配置（API 级别的授权）
    └─ 能访问哪个表？→ 文档协作者设置（文档级别的授权）
```

飞书有**双重权限机制**：应用要同时拥有「API 权限」和「文档级别的访问权」才能正常工作。

### 3.2 创建应用

1. 打开 [飞书开放平台](https://open.feishu.cn/app)
2. 点击 **「创建企业自建应用」**
3. 填写应用名称（如"专家考核审核机器人"）和描述
4. 创建后，进入应用详情页，在左侧菜单 **「凭证与基础信息」** 中记下：
   - **App ID**（如 `cli_a95d98f987785bdb`）
   - **App Secret**（如 `m5ukOIrL7t1ULg5TcVbRkDCCyULEmuHX`）

这两个值就是代码里 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 环境变量的来源。

### 3.3 添加 API 权限

在应用详情页左侧菜单 → **「权限管理」**，搜索并开通以下权限：

| 权限 | 作用 |
|------|------|
| `bitable:record` | 读写多维表格记录（读取专家提交的数据、回填审核结果） |
| `drive:media:readonly` | 下载云文档中的附件（下载专家上传的 Trace 文件） |

### 3.4 发布应用

页面顶部会有橙色提示"应用发布后，当前配置方可生效"：

1. 点击 **「创建版本」**
2. 填写版本号（如 `1.0.0`）和更新说明
3. 提交 → 等待管理员审批通过

**注意**：权限配置必须发布后才会真正生效。

### 3.5 授权应用访问你的多维表格

这是容易遗漏的一步。即使应用有了 API 权限，它也必须被加为**文档协作者**才能访问具体的表格。

1. 打开你创建的多维表格
2. 点击右上角 **「分享」** 或 **「...」→「更多」→「添加文档应用」**
3. 搜索你刚创建的飞书应用名称
4. 添加为协作者，给予 **编辑权限**

不做这一步，读取记录可能成功（取决于权限配置），但写入会报 `403 Forbidden`。

---

## Step 4：获取表格的 APP_TOKEN 和 TABLE_ID

### 4.1 这一步在干什么

告诉代码"往哪张表读写"。你的飞书里可能有很多多维表格，每个表格里可能有多张数据表，代码需要精确定位。

### 4.2 从 URL 中提取

打开你的多维表格，URL 格式如下：

```
https://xxx.feishu.cn/base/INiPbaSwsaKCffszIDwc1dYPnph?table=tblzyPQ33dOle6lY&view=vewTJ1ue3f
                            ├──────────────────────────┤       ├──────────────┤
                                  APP_TOKEN                      TABLE_ID
```

- **APP_TOKEN** = `INiPbaSwsaKCffszIDwc1dYPnph`（`/base/` 后面、`?` 前面的部分）
- **TABLE_ID** = `tblzyPQ33dOle6lY`（`table=` 后面的部分）

这两个值对应代码里的 `BITABLE_APP_TOKEN` 和 `BITABLE_TABLE_ID` 环境变量。

---

## Step 5：填写测试数据 + 获取 record_id

### 5.1 这一步在干什么

在表格中准备一行测试数据，并拿到它的唯一标识 `record_id`。整个流水线的入口就是一个 `record_id`——粗筛、AI 评审、回填结果，全都靠这个 ID 定位到"审核的是哪一行"。

### 5.2 填写测试数据

可以通过代码自动填写（需设置好环境变量后执行）：

```bash
export FEISHU_APP_ID="你的App ID"
export FEISHU_APP_SECRET="你的App Secret"
export BITABLE_APP_TOKEN="你的APP_TOKEN"
export BITABLE_TABLE_ID="你的TABLE_ID"

python3 -c "
from feishu_utils import get_feishu_token, update_record
token = get_feishu_token()

fields = {
    '专家姓名': '测试专家',
    '专家ID': 'test_001',
    '岗位方向': 'Coding',
    '任务描述': '我是一名后端开发工程师，我要解决的问题是搭建一套专家考核产物自动审核流水线。该流水线需要对接飞书多维表格API，实现记录的自动读取和回填功能。同时需要解析Claude Code生成的JSONL格式trace日志，从中提取对话轮次、模型信息和工具调用统计数据。流水线分为脚本粗筛和AI评审两个阶段，粗筛阶段执行6项硬性校验，AI评审阶段通过Daytona沙箱调用Claude进行3个维度的专业评分。整个系统需要保证高可靠性和可扩展性。',
    '最终产物': {'link': 'https://github.com/你的仓库地址', 'text': '项目仓库'},
}
update_record(token, '你的record_id', fields)
print('写入成功')
"
```

也可以直接在飞书表格界面手动填写各字段。

Trace 附件需要通过飞书 API 上传（手动在表格界面上传也可以）：

```python
# 上传附件的代码示例（需要先获取 token）
import requests, os

# 1. 上传文件，获取 file_token
upload_url = "https://open.feishu.cn/open-apis/drive/v1/medias/upload_all"
headers = {"Authorization": f"Bearer {token}"}

with open("你的trace.jsonl路径", "rb") as f:
    resp = requests.post(upload_url, headers=headers,
        data={"file_name": "trace.jsonl", "parent_type": "bitable_file",
              "parent_node": BITABLE_APP_TOKEN, "size": str(os.path.getsize("你的trace.jsonl路径"))},
        files={"file": ("trace.jsonl", f, "application/octet-stream")})

file_token = resp.json()["data"]["file_token"]

# 2. 将 file_token 关联到记录的附件字段
update_record(token, record_id, {"Trace文件": [{"file_token": file_token}]})
```

### 5.3 获取 record_id

表格第一个字段 `record_id` 是公式 `RECORD_ID()`，它会自动生成每行的唯一 ID（形如 `recvgglWDZxZHZ`）。直接复制该字段的值即可。

---

## Step 6：验证 API 连通性

### 6.1 这一步在干什么

确认前面所有配置都正确——应用凭证有效、权限到位、表格能读能写。这是进入粗筛测试前的最后一道关卡。

### 6.2 测试读取

```bash
cd expert-review-pipeline/

export FEISHU_APP_ID="你的App ID"
export FEISHU_APP_SECRET="你的App Secret"
export BITABLE_APP_TOKEN="你的APP_TOKEN"
export BITABLE_TABLE_ID="你的TABLE_ID"

python3 -c "
from feishu_utils import get_feishu_token, get_record
token = get_feishu_token()
print('Token 获取成功')
record = get_record(token, '你的record_id')
print('记录获取成功')
import json
print(json.dumps(record, ensure_ascii=False, indent=2))
"
```

预期输出：能看到你填入的测试数据。

### 6.3 测试写入

```bash
python3 -c "
from feishu_utils import get_feishu_token, update_record
token = get_feishu_token()
update_record(token, '你的record_id', {'粗筛状态': '待审'})
print('写入成功')
"
```

预期输出：`写入成功`，同时飞书表格中该行的「粗筛状态」变为「待审」。

### 6.4 常见问题

| 报错 | 原因 | 解决 |
|------|------|------|
| `获取飞书 tenant_access_token 失败` | App ID 或 App Secret 错误 | 检查凭证是否复制完整 |
| `获取飞书记录失败` | record_id 错误，或应用无表格访问权 | 检查 record_id；确认应用已被添加为表格协作者 |
| `403 Forbidden`（写入时） | 应用有读权限但没有写权限 | 确认应用已添加为表格**编辑者**（不是只读） |
| `FieldNameNotFound` | 代码中的字段名与表格实际字段名不一致 | 用字段列表 API 检查真实字段名，修改代码适配 |

---

## 4 个环境变量速查

完成上述步骤后，你会得到 4 个关键值，后续所有脚本都依赖它们：

```bash
export FEISHU_APP_ID="cli_xxxxx"          # Step 3 → 凭证与基础信息
export FEISHU_APP_SECRET="xxxxx"          # Step 3 → 凭证与基础信息
export BITABLE_APP_TOKEN="INiPbxxxxx"     # Step 4 → 从 URL 提取
export BITABLE_TABLE_ID="tblxxxxx"        # Step 4 → 从 URL 提取
```

---

 **Phase 2：粗筛测试**                                             

​                               

 **目标**：验证 pre_screen.py 的 6 项检查能正常跑通并回填结果到表格。                     

​                               

 \- 用已有的测试行，执行 pre_screen.py --record-id recvgglWDZxZHZ

 \- 检查飞书表格的「粗筛状态」和「粗筛详情」是否正确更新

 \- 再测几种失败场景（空描述、无 trace、轮次不足）确认拒绝逻辑正确



 **Phase** **3：AI** **评审测试**



 **目标**：验证 ai_review.py 能在 Daytona 沙箱中调 Claude 完成评分。



 \- 需要准备 DAYTONA_API_KEY 和 OPENROUTER_API_KEY

 \- 执行 ai_review.py --record-id recvgglWDZxZHZ

 \- 检查输出的评分 JSON 是否结构正确



 **Phase** **4：回填** **+** **端到端串联**



 **目标**：三个阶段串起来跑一遍完整流程。



 \- 执行 writeback.py 验证分数和结论正确回填

 \- 用 run_expert_review_pipeline.sh 跑完整流程

 \- 测试不同场景：正常通过、粗筛拒绝、AI 低分拒绝、边界待复核



 **Phase** **5：火山流水线上线**



 **目标**：代码不再本地跑，而是由火山引擎流水线自动执行。



 \- 在火山引擎创建流水线 + 配置代码源（GitHub）

 \- 配置 Webhook 触发器 + 环境变量（密钥）

 \- 配置执行任务（调用 run_expert_review_pipeline.sh）



 **Phase** **6：飞书按钮对接**



 **目标**：专家点「开始评审」按钮就能触发整个流水线。



 \- 在飞书多维表格配置按钮的 HTTP 请求，连接火山流水线的 Webhook URL

 \- 端到端测试：填数据 → 点按钮 → 自动审核 → 结果回填
