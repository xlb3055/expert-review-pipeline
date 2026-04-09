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

## Phase 2：粗筛测试

### 这一阶段在干什么

验证 `pre_screen.py` 的 6 项硬性检查能正常运行，并将结果自动回填到飞书表格。粗筛是整个流水线的第一道关卡——快速过滤掉明显不合格的提交，避免浪费 AI 评审资源。

### 前置条件

- Phase 1 全部完成（飞书 API 读写正常）
- 表格中有一行填好的测试数据（含 Trace 附件）
- 本地安装了 `requests` 包

### 6 项检查说明

| # | 检查项 | 通过条件 | 不通过处理 |
|---|--------|---------|----------|
| 1 | Trace 附件存在 | Trace 文件字段不为空 | 直接拒绝 |
| 2 | 对话轮次 | trace 中用户消息 ≥ 5 轮 | 直接拒绝 |
| 3 | SOTA 模型 | 使用 claude-opus 系列模型 | 无模型信息→待复核；非 opus→拒绝 |
| 4 | 最终产物存在 | 链接或附件至少有一项 | 直接拒绝 |
| 5 | 任务描述长度 | ≥ 100 字 | 直接拒绝 |
| 6 | Trace 真实性 | 包含工具调用记录 | 标记待人工复核 |

### 执行步骤

```bash
cd ~/Desktop/expert-review-pipeline

# 设置环境变量（同 Phase 1）
export FEISHU_APP_ID="你的App ID"
export FEISHU_APP_SECRET="你的App Secret"
export BITABLE_APP_TOKEN="你的APP_TOKEN"
export BITABLE_TABLE_ID="你的TABLE_ID"

# 指定临时文件路径（本地测试用，生产环境用 /workspace/）
export TRACE_OUTPUT_PATH="/tmp/expert_review_test/trace.jsonl"
export PRE_SCREEN_RESULT_PATH="/tmp/expert_review_test/pre_screen_result.json"

# 运行粗筛
python3 pre_screen.py --record-id <你的record_id>
```

### 退出码含义

| 退出码 | 含义 | 流水线行为 |
|--------|------|-----------|
| 0 | 通过 | 继续到 AI 评审 |
| 1 | 拒绝 | 流水线结束 |
| 2 | 待人工复核 | 继续到 AI 评审 |
| 3 | 系统错误 | 流水线报错 |

### 验证

1. 检查终端输出：每项检查会打印通过/不通过及详情
2. 检查飞书表格：「粗筛状态」和「粗筛详情」字段应已更新
3. 检查本地文件：`/tmp/expert_review_test/pre_screen_result.json` 包含完整检查结果

### 测试失败场景

建议额外测试以下情况，确认拒绝逻辑正确：

- 清空任务描述 → 应拒绝（检查 5）
- 删除 Trace 附件 → 应拒绝（检查 1）
- 使用对话轮次不足 5 轮的 trace → 应拒绝（检查 2）

### 踩坑记录

**Trace 格式兼容**：Claude Code 的 transcript 日志中，用户消息的 type 可能是 `user`（而非 `human`），代码已做兼容。如果你的 trace 使用其他字段名，需要在 `trace_parser.py` 中适配。

**模型信息缺失**：Claude Code transcript 不一定记录模型名，此时检查 3 会标记"待人工复核"而非直接拒绝。

---

## Phase 3：AI 评审测试

### 这一阶段在干什么

在 Daytona 云沙箱中启动 Claude Code，让 AI 从 3 个维度对专家的考核产物进行评分。这是整个流水线的核心环节——用 AI 替代人工做专业判断。

### 为什么需要 Daytona 沙箱

Claude Code CLI 需要访问 OpenRouter API，而国内网络环境可能无法直接访问。Daytona 沙箱位于海外，可以正常调用 API。流程是：

```
本地脚本 → 创建海外沙箱 → 上传文件到沙箱 → 在沙箱中运行 Claude → 下载结果 → 销毁沙箱
```

### 前置条件

- Phase 2 通过（trace 文件已下载到本地临时路径）
- 两个额外的 API Key：
  - `DAYTONA_API_KEY` — Daytona 平台的 API 密钥（在 [app.daytona.io](https://app.daytona.io) 获取）
  - `OPENROUTER_API_KEY` — OpenRouter 的 API 密钥（在 [openrouter.ai](https://openrouter.ai) 获取）
- 安装 Python 依赖：`pip install daytona-sdk requests`

### 3 个评分维度

| 维度 | 分值 | 评什么 |
|------|------|--------|
| 任务复杂度 | 0-3 | 专家选的任务本身难不难 |
| 迭代质量 | 0-3 | 专家有没有主动引导/纠正 AI |
| 专业判断 | 0-4 | 最终产出是否体现岗位专业性 |
| **总分** | **0-10** | 三个维度之和 |

### 执行步骤

```bash
cd ~/Desktop/expert-review-pipeline

# Phase 1 的环境变量 + 新增两个 Key
export FEISHU_APP_ID="你的App ID"
export FEISHU_APP_SECRET="你的App Secret"
export BITABLE_APP_TOKEN="你的APP_TOKEN"
export BITABLE_TABLE_ID="你的TABLE_ID"
export DAYTONA_API_KEY="你的Daytona Key"
export OPENROUTER_API_KEY="你的OpenRouter Key"

# 临时文件路径
export TRACE_OUTPUT_PATH="/tmp/expert_review_test/trace.jsonl"
export AI_REVIEW_RESULT_PATH="/tmp/expert_review_test/ai_review_result.json"
export CLAUDE_TIMEOUT="600"

# 使用 venv（daytona-sdk 装在 venv 里）
.venv/bin/python3 ai_review.py --record-id <你的record_id>
```

### 执行过程说明

脚本会依次完成以下步骤（总耗时约 1-3 分钟）：

1. **获取飞书记录** — 读取任务描述、专家信息等
2. **读取 Trace 内容** — 从本地临时路径读取（由 pre_screen.py 已下载）
3. **组装输入文本** — 将专家信息 + 任务描述 + trace 内容拼成一份供 Claude 阅读的文本
4. **创建 Daytona 沙箱** — 在海外创建一个临时计算环境
5. **检查/安装 Claude CLI** — 若沙箱未预装 Claude Code，自动 `npm install`
6. **上传文件** — 将 prompt、schema、输入文本上传到沙箱
7. **执行 Claude Code** — 在沙箱中运行 `claude -p` 命令
8. **轮询等待** — 每 5 秒检查一次是否完成
9. **下载结果** — 取回评审 JSON
10. **清理沙箱** — 停止并删除沙箱

### 验证

1. 检查终端输出：应看到 `沙箱已创建`、`文件上传完成`、`exit_code=0`、`已下载 xxx 字符`
2. 检查结果文件：`/tmp/expert_review_test/ai_review_result.json` 应包含 3 个维度的评分
3. 检查评分合理性：分数应与 trace 内容匹配（简单操作低分，复杂操作高分）

### 踩坑记录

**代理干扰**：本地若有 SOCKS 代理（`ALL_PROXY`、`HTTP_PROXY`），会导致 Daytona SDK 的文件上传失败。运行前先 `unset HTTP_PROXY HTTPS_PROXY ALL_PROXY`。

**Snapshot 名称**：默认使用 `daytona-medium`（2 CPU / 4 GB）。如果你的 Daytona 账户有自定义 snapshot（如公司的 `claude-code-snapshot`），可通过 `SNAPSHOT_NAME` 环境变量指定。

**daytona-sdk 包名变更**：新版 SDK 的 import 路径是 `daytona_sdk`（不是 `daytona`），代码已做兼容。

**输出格式提取**：Claude 返回的 JSON 可能有多层包装（API 包装 → markdown 代码块 → 非标准键名），代码会自动处理这些情况。

---

## Phase 4：回填 + 端到端串联

### 这一阶段在干什么

将粗筛和 AI 评审的结果汇总，计算最终结论，一次性回填到飞书表格的所有结果字段。然后用入口脚本串联三个阶段跑一次完整流程。

### 结论判定规则

| 总分 | 最终结论 |
|------|---------|
| ≥ 7 分 | 通过 |
| 5-6 分 | 待人工复核 |
| < 5 分 | 拒绝 |

如果粗筛已拒绝，则最终结论直接为"拒绝"，不看 AI 评分。

### Step 1：单独测试 writeback.py

```bash
cd ~/Desktop/expert-review-pipeline

export FEISHU_APP_ID="你的App ID"
export FEISHU_APP_SECRET="你的App Secret"
export BITABLE_APP_TOKEN="你的APP_TOKEN"
export BITABLE_TABLE_ID="你的TABLE_ID"

python3 writeback.py \
  --record-id <你的record_id> \
  --pre-screen-result /tmp/expert_review_test/pre_screen_result.json \
  --ai-review-result /tmp/expert_review_test/ai_review_result.json
```

验证：飞书表格中以下字段应已更新：
- `AI评审状态` — 通过/拒绝/待人工复核
- `AI评审结果` — 完整 JSON
- `任务复杂度` / `迭代质量` / `专业判断` — 各维度分数
- `总分` — 公式自动计算
- `最终结论` — 通过/拒绝/待人工复核

### Step 2：用入口脚本跑完整流程

入口脚本 `run_expert_review_pipeline.sh` 按顺序调用三个阶段：

```bash
cd ~/Desktop/expert-review-pipeline

# 设置全部环境变量
export FEISHU_APP_ID="你的App ID"
export FEISHU_APP_SECRET="你的App Secret"
export BITABLE_APP_TOKEN="你的APP_TOKEN"
export BITABLE_TABLE_ID="你的TABLE_ID"
export DAYTONA_API_KEY="你的Daytona Key"
export OPENROUTER_API_KEY="你的OpenRouter Key"
export RECORD_ID="<你的record_id>"

bash run_expert_review_pipeline.sh
```

脚本会自动执行：
1. **阶段 1 粗筛** → 退出码 0/2 继续，退出码 1 结束
2. **阶段 2 AI 评审** → 即使失败也继续到回填
3. **阶段 3 回填** → 将所有结果写入飞书

### Step 3：测试不同场景

| 场景 | 预期结果 |
|------|---------|
| 正常提交（高质量 trace） | 粗筛通过 → AI 评高分 → 最终通过 |
| 缺少 Trace | 粗筛拒绝 → 流水线结束 |
| 任务描述太短 | 粗筛拒绝 → 流水线结束 |
| 简单操作的 trace | 粗筛通过 → AI 评低分 → 最终拒绝 |
| 中等质量 | 粗筛通过 → AI 评 5-6 分 → 待人工复核 |

### 回填的完整字段列表

writeback.py 一次性回填以下 6 个字段：

```
AI评审状态     ← 通过/拒绝/待人工复核
AI评审结果     ← 完整的 AI 评审 JSON
任务复杂度     ← 0-3 分
迭代质量       ← 0-3 分
专业判断       ← 0-4 分
最终结论       ← 通过/拒绝/待人工复核
```

`总分` 是公式字段（`任务复杂度 + 迭代质量 + 专业判断`），飞书自动计算，不需要回填。

---

---

## Phase 5：火山引擎流水线上线

### 这一阶段在干什么

将本地手动执行的流程部署到火山引擎持续交付流水线上，实现通过 Webhook 触发自动执行。完成后，整个链路变成：

```
HTTP POST(record_id) → 火山 Webhook → 拉取 GitHub 代码 → 执行粗筛 → AI 评审 → 回填飞书
```

### 前置条件

- Phase 1-4 全部完成（本地流水线跑通）
- 代码已推送到 GitHub
- 拥有火山引擎账号（持续交付服务已开通）
- 本地已安装 Docker

---

### Step 1：创建流水线

1. 打开 [火山引擎持续交付控制台](https://console.volcengine.com/pipeline/)
2. 进入「流水线管理」→ 点击 **「新建流水线」**
3. 选择 **「空白流水线」**
4. 名称填：`专家考核评审流水线`

### Step 2：配置代码源

1. 在流水线编辑页，配置 **代码源**
2. 代码平台选择 **GitHub**（需先完成 OAuth 授权关联 GitHub 账号）
3. 仓库地址必须用 **HTTPS 格式**：`https://github.com/你的用户名/expert-review-pipeline.git`
4. 分支选择：`main`

> **踩坑记录**：仓库必须是 **Public** 的，否则火山引擎拉取时会报 404。如果不想公开，需要在代码源配置中提供 GitHub Personal Access Token。

代码拉取后会放在 `/workspace/` 目录下。

### Step 3：构建专用 Docker 镜像

#### 为什么需要自定义镜像

火山引擎的命令执行步骤需要指定 Docker 镜像作为执行环境。如果不在镜像中预装依赖，每次运行都要 `pip install`，下载速度慢（20-30 KB/s），白白浪费 5-10 分钟。

把 `requests` 和 `daytona-sdk` 预装到镜像中，启动后直接执行脚本，零等待。

#### Dockerfile

在项目根目录创建 `Dockerfile`：

```dockerfile
# 专家考核评审流水线 — 执行环境镜像
# 基于公司基础镜像，预装流水线所需的 Python 依赖

FROM meetchances-cn-beijing.cr.volces.com/ci/common:1.0.5

# 预装流水线依赖，避免每次运行都下载
RUN pip install --no-cache-dir requests daytona-sdk
```

#### 构建 & 推送

```bash
# 1. 登录火山引擎镜像仓库
docker login meetchances-cn-beijing.cr.volces.com -u <用户名> -p <密码>

# 2. 构建镜像
cd ~/Desktop/expert-review-pipeline
docker build -t meetchances-cn-beijing.cr.volces.com/ci/expert-review:1.0.0 .

# 3. 推送镜像
docker push meetchances-cn-beijing.cr.volces.com/ci/expert-review:1.0.0
```

镜像仓库的用户名和密码在火山引擎「镜像仓库」控制台中获取。

> **提示**：后续如果需要新增 Python 依赖，修改 Dockerfile 的 `RUN pip install` 行，重新 build 并推送新版本（如 `1.0.1`），然后在流水线中更新镜像地址。

### Step 4：配置 Webhook 触发器

1. 在流水线的 **「触发配置」** 中，选择 **Webhook 触发**
2. 创建后会生成一个 **Webhook URL**（形如 `https://cp.volces.com/v2/webhook/xxx`）
3. **记下这个 URL**，Phase 6 飞书按钮要用

### Step 5：配置环境变量

在流水线的 **「变量管理」** 中添加以下 7 个变量：

| 变量名 | 值 | 是否密钥 |
|--------|-----|---------|
| `FEISHU_APP_ID` | 飞书应用 ID | 是 |
| `FEISHU_APP_SECRET` | 飞书应用密钥 | 是 |
| `BITABLE_APP_TOKEN` | 多维表格 app token | 是 |
| `BITABLE_TABLE_ID` | 数据表 ID | 是 |
| `DAYTONA_API_KEY` | Daytona API 密钥 | 是 |
| `OPENROUTER_API_KEY` | OpenRouter API 密钥 | 是 |
| `RECORD_ID` | 运行时传入（默认值可留空） | 否 |

#### 关键：打开「环境变量」开关

火山引擎的变量管理中定义的变量，**默认不会注入到 shell 环境中**。必须对每个变量：

1. 点击变量右边的 **编辑图标**
2. 找到 **「环境变量」** 开关
3. **打开它**
4. 点确定

> **踩坑记录**：不打开这个开关，脚本中 `$RECORD_ID` 等变量全部为空，导致流水线报错"RECORD_ID 未设置"。这一步非常容易遗漏。

### Step 6：配置命令执行步骤

1. 在流水线编排中，添加一个 **「自定义环境命令执行」** 任务节点
2. **镜像地址**填：

```
meetchances-cn-beijing.cr.volces.com/ci/expert-review:1.0.0
```

3. **命令**填：

```bash
cd /workspace && bash run_expert_review_pipeline.sh
```

就这么简单。所有环境变量通过 Step 5 注入，入口脚本 `run_expert_review_pipeline.sh` 负责检查依赖、执行粗筛、AI 评审、回填。

### Step 7：手动运行测试

1. 保存流水线
2. 点 **「手动运行」**
3. 在参数填写框中，`RECORD_ID` 填入测试行的 record_id（如 `recvgglWDZxZHZ`）
4. 观察执行日志，确认各阶段正常完成

预期日志输出：

```
===== 专家考核评审流水线 =====
Record ID: recvgglWDZxZHZ

===== 阶段0: 环境准备 =====
Python 3.10.12
requests OK
daytona-sdk OK

===== 阶段1: 脚本粗筛 =====
[检查1] trace_exists: 通过
[检查2] conversation_rounds: 通过
...
粗筛退出码: 0/2

===== 阶段2: AI 评审 =====
沙箱已创建: xxx
Claude 于 xx.xs 内完成, exit_code=0

===== 阶段3: 结果回填 =====
飞书回填成功

===== 流水线完成 =====
```

### Step 8：测试 Webhook 触发

手动运行通过后，用 curl 模拟 Webhook 请求：

```bash
curl -X POST "你的Webhook URL" \
  -H "Content-Type: application/json" \
  -d '{"record_id": "recvgglWDZxZHZ"}'
```

确认流水线被成功触发。

> **注意**：Webhook 触发时需要把请求体中的 `record_id` 映射到流水线变量 `RECORD_ID`。在触发器配置中设置「运行时变量」映射。

### 踩坑记录汇总

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| `Invalid CodeRepo Scheme: must be http or https` | 代码源用了 SSH 格式 | 改用 HTTPS 格式仓库地址 |
| `404 Not Found` 拉取仓库 | 仓库是 Private | 改成 Public，或配置 Token |
| `Python 3.5.3` 无法运行 | 默认镜像太旧 | 使用自定义镜像 |
| `pip install` 每次耗时 5-10 分钟 | 依赖未预装到镜像 | 构建预装依赖的镜像 |
| `RECORD_ID 未设置` | 变量未注入到 shell | 变量管理中打开「环境变量」开关 |
| 自定义镜像拉取卡住 | 镜像地址错误或网络问题 | 确认镜像已推送到正确的仓库 |

### 耗时参考

使用预装依赖的镜像后：

| 阶段 | 耗时 |
|------|------|
| 代码拉取 | ~3 秒 |
| 环境准备（依赖检查） | ~2 秒 |
| 粗筛 | ~5 秒 |
| AI 评审（Daytona 沙箱 + Claude） | ~2-4 分钟 |
| 回填 | ~3 秒 |
| **总计** | **~3-5 分钟** |

---

## 下一步

Phase 5 完成后，流水线已部署到火山引擎并可通过 Webhook 触发。接下来是 **Phase 6（飞书按钮对接）**——在飞书多维表格中配置「开始评审」按钮，点击后自动发送 HTTP 请求触发流水线，实现从"专家点按钮"到"结果自动回填"的全自动流程。
