# 专家考核评审流水线 — 执行环境镜像
# 基于 ci/common:1.0.5，预装流水线所需的 Python 依赖
#
# 构建: docker build -t meetchances-cn-beijing.cr.volces.com/ci/expert-review:1.0.0 .
# 推送: docker push meetchances-cn-beijing.cr.volces.com/ci/expert-review:1.0.0

FROM meetchances-cn-beijing.cr.volces.com/ci/common:1.0.5

# 安装 unrar（rarfile 库的后端依赖，用于解压 rar 格式的 trace 附件）
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends unrar-free && rm -rf /var/lib/apt/lists/*

# 预装流水线依赖，避免每次运行都下载
RUN pip install --no-cache-dir requests daytona-sdk pyyaml anthropic openai jsonschema rarfile py7zr
