# 火山引擎自定义步骤镜像：从构建到上线（含踩坑全记录）

> 本文基于实际踩坑经历编写，覆盖"构建镜像 → 推送仓库 → 编写 step.yaml → 上传自定义步骤 → 流水线调试"全流程。

---

## 一、整体流程概览

```
编写 Dockerfile
    ↓
构建 linux/amd64 镜像    ← 坑 1：架构问题
    ↓
推送到火山引擎镜像仓库
    ↓
编写 step.yaml          ← 坑 3/4/5：YAML 格式
    ↓
上传自定义步骤
    ↓
在流水线中引用
    ↓
调试运行               ← 坑 2/6/7：运行时问题
```

---

## 二、构建镜像

### 2.1 Dockerfile 示例

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 离线安装依赖（见坑 2）
COPY ci-steps/feishu-io/wheels /tmp/wheels
COPY core /app/core
COPY scripts /app/scripts

RUN pip install --no-cache-dir --no-index --find-links=/tmp/wheels requests pyyaml \
    && rm -rf /tmp/wheels

ENV PYTHONPATH=/app
```

### 2.2 构建并推送

```bash
# 1. 登录镜像仓库
docker login meetchances-cn-beijing.cr.volces.com -u <用户名> -p <令牌>

# 2. 构建 amd64 镜像并推送
docker buildx build \
  --platform linux/amd64 \
  -t meetchances-cn-beijing.cr.volces.com/ci/feishu-io-template:1.0.1 \
  -f ci-steps/feishu-io/Dockerfile \
  --push \
  .
```

---

## 三、编写 step.yaml

### 3.1 完整示例

```yaml
step: feishu-read                    # 步骤唯一标识
version: 1.0.1                       # 版本号
displayName: 飞书读取                 # 显示名称，可以用中文
description: 通用飞书读取模板         # 描述
category: Command                    # 类别，必须英文
image: meetchances-cn-beijing.cr.volces.com/ci/feishu-io-template:1.0.1
inputs:
  - name: app-id                     # 参数名：英文+数字+中横线
    displayName: 飞书AppID
    type: string
    required: true
  - name: app-secret
    displayName: 飞书AppSecret
    type: string
    required: true
  # ... 更多参数
script: |
  #!/bin/sh
  set -e

  APP_ID='$(inputs.app-id)'
  APP_SECRET='$(inputs.app-secret)'

  python3 /app/scripts/feishu_read_template.py \
    --app-id "$APP_ID" \
    --app-secret "$APP_SECRET"
```

### 3.2 上传自定义步骤

1. 打开火山引擎控制台 → 持续交付 → 流水线 → 自定义步骤
2. 点击"新建步骤" → 选择"YAML 导入"
3. 粘贴 step.yaml 内容
4. 保存

### 3.3 在流水线中引用

在流水线编辑器中添加步骤 → 搜索你的自定义步骤名称 → 填写参数。

---

## 四、踩坑大全（血泪经验）

### 坑 1：`exec format error` — 镜像 CPU 架构不匹配

**现象：**

```
Error executing command: fork/exec /tekton/scripts/script-0-xxx: exec format error
```

**原因：**

M 系列 Mac（Apple Silicon）默认构建的是 `linux/arm64` 镜像，但火山引擎流水线运行环境是 `linux/amd64`。ARM 二进制文件无法在 x86 机器上执行。

**解决：**

构建时必须指定 `--platform linux/amd64`：

```bash
docker buildx build --platform linux/amd64 -t <镜像地址>:<tag> --push .
```

**确认方法：**

```bash
docker buildx imagetools inspect <镜像地址>:<tag>
# 看 Platform 是否为 linux/amd64
```

> 这是 M 系列 Mac 用户 100% 会踩的坑，没有例外。

---

### 坑 2：QEMU 模拟环境中 pip install 网络不通

**现象：**

跨架构构建时，`RUN pip install` 卡住几分钟后报错：

```
WARNING: Retrying ... Failed to establish a new connection: [Errno 101] Network is unreachable
```

**原因：**

Docker Desktop 使用 QEMU 模拟 amd64 环境时，网络栈可能不正常，导致容器内无法联网。

**解决：**

在宿主机上预先下载对应架构的 wheel 包，COPY 进镜像后离线安装：

```bash
# 第一步：在宿主机下载 amd64 的 wheel
pip download requests pyyaml \
  --platform manylinux2014_x86_64 \
  --python-version 3.11 \
  --only-binary=:all: \
  -d ci-steps/feishu-io/wheels

# 第二步：Dockerfile 里离线安装
COPY ci-steps/feishu-io/wheels /tmp/wheels
RUN pip install --no-cache-dir --no-index --find-links=/tmp/wheels requests pyyaml \
    && rm -rf /tmp/wheels
```

> 这个方法的好处：构建速度从 5 分钟变成 2 秒，且 100% 可复现。

---

### 坑 3：推送新镜像后流水线仍用旧镜像（tag 缓存）

**现象：**

明明已经推送了修复后的镜像，但流水线报错信息里的 `sha256` digest 仍然是旧的。

```
image: "xxx@sha256:ba1281390c..."   ← 旧的 digest，不是你刚推的
```

**原因：**

用相同 tag（如 `1.0.0`）覆盖推送后，火山引擎/Tekton 可能缓存了旧的镜像 digest，不会自动拉取新的。

**解决：**

**每次修复后递增 tag 版本号**，不要覆盖同一个 tag：

```bash
# 不要反复推 1.0.0，改用 1.0.1、1.0.2 ...
docker buildx build --platform linux/amd64 \
  -t xxx/feishu-io-template:1.0.1 --push .
```

同时更新 step.yaml 中的 image 字段，重新上传自定义步骤。

> 这个坑非常隐蔽——你以为推上去了，实际上平台根本没拉到新的。

---

### 坑 4：`ModuleNotFoundError` — 隐式依赖链

**现象：**

```
File "/app/core/__init__.py", line 12, in <module>
    from core.config_loader import load_project_config, get_field_name
File "/app/core/config_loader.py", line 14, in <module>
    import yaml
ModuleNotFoundError: No module named 'yaml'
```

**原因：**

你的脚本只用到了 `core.feishu_nodes`，但 Python 导入 `core.feishu_nodes` 时会先执行 `core/__init__.py`，而 `__init__.py` 里导入了 `config_loader`，它依赖 `pyyaml`。

**这是一条隐式依赖链：**

```
feishu_read_template.py
  → from core.feishu_nodes import ...
    → Python 自动执行 core/__init__.py
      → from core.config_loader import ...
        → import yaml  ← 爆了
```

**解决：**

把镜像里你代码依赖的**整条导入链**涉及的所有第三方包都装上。不要只看脚本直接 import 了什么，要看 `__init__.py` 会间接拉起什么。

排查方法：

```bash
# 看 core 包里所有的 import
grep -rn "^import \|^from " core/ --include="*.py"
```

> 教训：Python 包的 `__init__.py` 是个隐形炸弹。如果你的 `__init__.py` 做了很多 re-export，轻量级镜像很容易缺依赖。

---

### 坑 5：step.yaml 格式约束

火山引擎对 step.yaml 有一些不明显的约束：

| 字段 | 约束 | 错误示范 | 正确示范 |
|------|------|---------|---------|
| `category` | 必须英文 | `类别: 命令` | `Command` |
| `inputs[].name` | 英文/数字/中横线 | `app_id` | `app-id` |
| `script` 第一行 | 建议写 shebang | 直接写命令 | `#!/bin/sh` |
| `step` | 唯一标识，中横线命名 | `feishu_read` | `feishu-read` |
| `version` | 必须存在 | 省略 | `1.0.1` |

> `inputs[].name` 不能用下划线是最容易忽略的，因为 Python 参数习惯用下划线。

---

### 坑 6：script 里没写 shebang 导致 exec format error

**现象：**

和坑 1 一样的 `exec format error`，但镜像架构是对的。

**原因：**

step.yaml 的 `script` 字段没写 `#!/bin/sh` 或 `#!/bin/bash`，Tekton 生成的脚本文件没有正确的解释器声明。

**解决：**

script 第一行永远写 shebang：

```yaml
script: |
  #!/bin/sh
  set -e
  ...
```

---

### 坑 7：步骤间数据共享

**现象：**

步骤 A 写出了文件，步骤 B 读不到。

**原因：**

火山引擎流水线基于 Tekton，同一个 Task 内的步骤共享 `/workspace` 目录。但不同 Task 之间不共享。

**解决：**

- 同一 Task 内的步骤：统一用 `/workspace/` 目录传递文件
- 跨 Task：使用火山引擎的 Artifact 机制或环境变量传递

```yaml
# 步骤 A 输出
--output-file /workspace/input.json

# 步骤 B 读取
--data-file /workspace/input.json
```

---

## 五、调试技巧

### 5.1 本地验证镜像

不要每次都推到远程才发现问题，先在本地验证：

```bash
# 用 amd64 镜像本地跑一下（QEMU 模拟）
docker run --platform linux/amd64 --rm \
  meetchances-cn-beijing.cr.volces.com/ci/feishu-io-template:1.0.1 \
  python3 -c "from core.feishu_nodes import fetch_record_to_data; print('OK')"
```

### 5.2 检查镜像架构

```bash
docker inspect <镜像ID> | grep Architecture
# 应该输出 "Architecture": "amd64"
```

### 5.3 查看镜像内容

```bash
docker run --platform linux/amd64 --rm -it <镜像地址>:<tag> /bin/sh
# 进去后检查文件是否都在
ls /app/core/
ls /app/scripts/
python3 -c "import requests; import yaml; print('deps OK')"
```

### 5.4 完整的更新流程 Checklist

每次修改代码后重新发布的完整步骤：

```
1. 如有新依赖 → pip download 到 wheels 目录
2. 递增 tag 版本号（如 1.0.1 → 1.0.2）
3. docker buildx build --platform linux/amd64 ... --push
4. 更新 step.yaml 中的 image tag
5. 重新上传自定义步骤到火山引擎
6. 重新运行流水线
```

> 第 4、5 步容易忘，一定要做。不然平台还是拉旧镜像。

---

## 六、项目文件结构参考

```
ci-steps/
├── feishu-io/
│   ├── Dockerfile              # 镜像定义
│   └── wheels/                 # 离线 wheel 包（不要提交到 git）
│       ├── requests-*.whl
│       ├── pyyaml-*.whl
│       ├── charset_normalizer-*.whl
│       ├── idna-*.whl
│       ├── urllib3-*.whl
│       └── certifi-*.whl
├── feishu-read/
│   └── step.yaml               # 读步骤定义
└── feishu-write/
    └── step.yaml               # 写步骤定义
```

建议在 `.gitignore` 中添加：

```
ci-steps/*/wheels/
```

---

## 七、快速参考卡片

| 操作 | 命令 |
|------|------|
| 下载 amd64 wheel | `pip download <pkg> --platform manylinux2014_x86_64 --python-version 3.11 --only-binary=:all: -d wheels/` |
| 构建 amd64 并推送 | `docker buildx build --platform linux/amd64 -t <addr>:<tag> -f <Dockerfile> --push .` |
| 检查镜像架构 | `docker buildx imagetools inspect <addr>:<tag>` |
| 本地验证 | `docker run --platform linux/amd64 --rm <addr>:<tag> python3 -c "import ..."` |
| 登录仓库 | `docker login <registry> -u <user> -p <token>` |
