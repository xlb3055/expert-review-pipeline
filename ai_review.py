#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
第二层：AI 评审

在 Daytona 沙箱中执行 Claude Code，对专家考核产物进行 3 维度评分。
改编自 run_daytona.py，使用本地模式（prompt-file + schema-file）。

用法:
  python3 ai_review.py --record-id <record_id>
"""

import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

try:
    from daytona_sdk import (
        CreateSandboxFromSnapshotParams,
        Daytona,
        DaytonaConfig,
        DaytonaError,
        DaytonaNotFoundError,
        Resources,
        SandboxState,
        SessionExecuteRequest,
    )
except ImportError:
    from daytona import (
        CreateSandboxFromSnapshotParams,
        Daytona,
        DaytonaConfig,
        DaytonaError,
        DaytonaNotFoundError,
        Resources,
        SandboxState,
        SessionExecuteRequest,
    )

from feishu_utils import (
    check_required_env,
    extract_link_url,
    get_feishu_token,
    get_record,
    normalize_field_value,
    update_record,
)
from trace_parser import truncate_trace_content

# ---------- 配置 ----------

DAYTONA_API_KEY = os.environ.get("DAYTONA_API_KEY", "")
SNAPSHOT_NAME = os.environ.get("SNAPSHOT_NAME", "daytona-medium")
SANDBOX_NAME_PREFIX = os.environ.get("SANDBOX_NAME_PREFIX", "expert_review")

OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
CLAUDE_MODEL = os.environ.get(
    "ANTHROPIC_MODEL",
    os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "anthropic/claude-sonnet-4-6"),
)

CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "600"))
POLL_INTERVAL = 5

TRACE_INPUT_PATH = os.environ.get("TRACE_OUTPUT_PATH", "/workspace/trace.jsonl")
AI_REVIEW_RESULT_PATH = os.environ.get("AI_REVIEW_RESULT_PATH", "/workspace/ai_review_result.json")

REMOTE_TMP_DIR = "/tmp/expert_review"

# 本脚本所在目录（用于定位 prompt 和 schema 文件）
SCRIPT_DIR = Path(__file__).resolve().parent

# ---------- 沙箱内 JSON 修复脚本 ----------

_SANDBOX_REPAIR_SCRIPT = r"""#!/usr/bin/env python3
import json, re, sys

def fix_unescaped_quotes(text):
    out = []
    i = 0
    n = len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            if ch == '\\' and i + 1 < n:
                out.append(ch)
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                k = i + 1
                while k < n and text[k] in ' \t\r\n':
                    k += 1
                if k >= n or text[k] in ':,}]':
                    in_string = False
                    out.append('"')
                else:
                    out.append('\\"')
                i += 1
                continue
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append('"')
            i += 1
            continue
        out.append(ch)
        i += 1
    return ''.join(out)

def write_json(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)

def try_extract_structured_output(obj):
    if not isinstance(obj, dict):
        return None
    so = obj.get("structured_output")
    if isinstance(so, dict) and so:
        return so
    result = obj.get("result")
    if isinstance(result, dict) and result:
        return result
    if isinstance(result, str) and result.strip():
        try:
            parsed = json.loads(result.strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return None

def try_repair(raw):
    m = re.search(r'```json\s*(.*?)\s*```', raw, re.DOTALL)
    candidate = m.group(1).strip() if m else raw
    if candidate != raw:
        try:
            obj = json.loads(candidate)
            return obj
        except json.JSONDecodeError:
            raw = candidate
    fb = raw.find("{")
    lb = raw.rfind("}")
    if fb != -1 and lb > fb:
        candidate = raw[fb:lb+1]
        try:
            obj = json.loads(candidate)
            return obj
        except json.JSONDecodeError:
            raw = candidate
    try:
        fixed = fix_unescaped_quotes(raw)
        obj = json.loads(fixed)
        return obj
    except Exception:
        pass
    return None

raw_file = sys.argv[1]
out_file = sys.argv[2]
with open(raw_file, "r", encoding="utf-8") as f:
    raw = f.read().strip()
if not raw:
    write_json({}, out_file)
    sys.exit(0)

try:
    obj = json.loads(raw)
    extracted = try_extract_structured_output(obj)
    if extracted:
        write_json(extracted, out_file)
        sys.exit(0)
    write_json(obj, out_file)
    sys.exit(0)
except json.JSONDecodeError:
    pass

repaired = try_repair(raw)
if repaired:
    extracted = try_extract_structured_output(repaired)
    if extracted:
        write_json(extracted, out_file)
        sys.exit(0)
    write_json(repaired, out_file)
    sys.exit(0)

with open(out_file, "w", encoding="utf-8") as f:
    f.write(raw)
"""


# ---------- 辅助函数 ----------

def _build_input_text(fields: dict, trace_content: str) -> str:
    """组装 AI 评审的输入文本。"""
    task_desc = normalize_field_value(fields.get("任务描述", ""))
    expert_name = normalize_field_value(fields.get("专家姓名", ""))
    expert_id = normalize_field_value(fields.get("专家ID", ""))
    position = normalize_field_value(fields.get("岗位方向", ""))
    product_link = extract_link_url(fields.get("最终产物", ""))

    parts = [
        "# 专家考核产物 — AI 评审输入",
        "",
        f"## 专家信息",
        f"- 姓名: {expert_name}",
        f"- ID: {expert_id}",
        f"- 岗位方向: {position}",
        "",
        f"## 任务描述（专家撰写的 Prompt）",
        task_desc,
        "",
    ]

    if product_link:
        parts.extend([
            f"## 最终产物链接",
            product_link,
            "",
        ])

    parts.extend([
        "## Claude Code Trace 日志",
        "以下是专家与 Claude Code 交互的完整 trace 记录（JSONL 格式）：",
        "",
        trace_content,
    ])

    return "\n".join(parts)


def _try_repair_json(raw: str) -> str:
    """从模型输出中提取并修复为合法 JSON。"""
    try:
        obj = json.loads(raw)
        so = obj.get("structured_output")
        if isinstance(so, dict) and so:
            return json.dumps(so, ensure_ascii=False)
        result = obj.get("result")
        if isinstance(result, dict) and result:
            return json.dumps(result, ensure_ascii=False)
        return raw
    except json.JSONDecodeError:
        pass

    m = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            raw = candidate

    first_brace = raw.find("{")
    last_brace = raw.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidate = raw[first_brace:last_brace + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    return raw


# ---------- 主流程 ----------

def run_ai_review(record_id: str) -> int:
    """
    执行 AI 评审流程。

    返回: 0=成功, 1=失败
    """
    check_required_env()

    if not DAYTONA_API_KEY:
        print("错误: DAYTONA_API_KEY 未设置", file=sys.stderr)
        return 1
    if not OPENROUTER_API_KEY:
        print("错误: OPENROUTER_API_KEY 未设置", file=sys.stderr)
        return 1

    t0 = time.time()
    print("===== AI 评审开始 =====")
    print(f"Record ID: {record_id}")
    print(f"模型: {CLAUDE_MODEL}")

    # 1. 获取飞书记录
    print("\n--- 获取飞书记录 ---")
    token = get_feishu_token()
    record = get_record(token, record_id)
    fields = record.get("fields", {})

    # 2. 读取已下载的 trace 文件内容（由 pre_screen.py 下载到 TRACE_INPUT_PATH）
    print("\n--- 读取 Trace 内容 ---")
    trace_content = truncate_trace_content(TRACE_INPUT_PATH, max_rounds=20, max_bytes=100000)
    print(f"Trace 内容长度: {len(trace_content)} 字符")

    # 3. 组装输入文本
    input_text = _build_input_text(fields, trace_content)
    print(f"输入文本总长度: {len(input_text)} 字符")

    # 4. 读取本地 prompt 和 schema
    prompt_file = SCRIPT_DIR / "prompt_expert_review.md"
    schema_file = SCRIPT_DIR / "schema_expert_review.json"

    if not prompt_file.is_file():
        print(f"错误: prompt 文件不存在: {prompt_file}", file=sys.stderr)
        return 1
    if not schema_file.is_file():
        print(f"错误: schema 文件不存在: {schema_file}", file=sys.stderr)
        return 1

    prompt_content = prompt_file.read_text(encoding="utf-8")
    schema_content = schema_file.read_text(encoding="utf-8")

    # 5. 回填 AI 评审状态为"进行中"
    try:
        update_record(token, record_id, {"AI评审状态": "进行中"})
    except Exception as e:
        print(f"回填进行中状态失败（非致命）: {e}")

    # 6. 创建 Daytona Sandbox
    print(f"\n--- 创建 Daytona Sandbox --- [{time.time()-t0:.1f}s]")
    daytona = Daytona(DaytonaConfig(api_key=DAYTONA_API_KEY))
    sandbox_name = f"{SANDBOX_NAME_PREFIX}-{uuid.uuid4().hex[:6]}"
    print(f"沙箱名称: {sandbox_name}")

    sandbox = None
    try:
        try:
            sandbox = daytona.create(
                CreateSandboxFromSnapshotParams(
                    name=sandbox_name,
                    snapshot=SNAPSHOT_NAME,
                    network_block_all=False,
                    auto_stop_interval=0,
                    auto_delete_interval=0,
                    resources=Resources(cpu=2, memory=4, disk=5),
                    env_vars={
                        "ANTHROPIC_BASE_URL": OPENROUTER_BASE_URL,
                        "ANTHROPIC_AUTH_TOKEN": OPENROUTER_API_KEY,
                        "ANTHROPIC_API_KEY": "",
                        "ANTHROPIC_MODEL": CLAUDE_MODEL,
                        "ANTHROPIC_DEFAULT_SONNET_MODEL": CLAUDE_MODEL,
                        "API_TIMEOUT_MS": "300000",
                        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                        "CI": "1",
                    },
                ),
                timeout=0,
            )
        except DaytonaError as e:
            if "already exists" in str(e).lower():
                print(f"沙箱名已存在，删除后重试: {e}")
                try:
                    daytona.delete(daytona.get(sandbox_name))
                    time.sleep(2)
                except Exception:
                    pass
                sandbox = daytona.create(
                    CreateSandboxFromSnapshotParams(
                        name=sandbox_name,
                        snapshot=SNAPSHOT_NAME,
                        network_block_all=False,
                        auto_stop_interval=0,
                        auto_delete_interval=0,
                        resources=Resources(cpu=2, memory=4, disk=5),
                        env_vars={
                            "ANTHROPIC_BASE_URL": OPENROUTER_BASE_URL,
                            "ANTHROPIC_AUTH_TOKEN": OPENROUTER_API_KEY,
                            "ANTHROPIC_API_KEY": "",
                            "ANTHROPIC_MODEL": CLAUDE_MODEL,
                            "ANTHROPIC_DEFAULT_SONNET_MODEL": CLAUDE_MODEL,
                            "API_TIMEOUT_MS": "300000",
                            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                            "CI": "1",
                        },
                    ),
                    timeout=0,
                )
            else:
                raise
        print(f"沙箱已创建: {sandbox.id}")

        # 6.5 安装 Claude Code CLI（若沙箱未预装）
        print(f"\n--- 检查并安装 Claude Code CLI --- [{time.time()-t0:.1f}s]")
        check_claude = sandbox.process.exec("which claude || echo 'NOT_FOUND'")
        if "NOT_FOUND" in (check_claude.result or ""):
            print("Claude Code CLI 未安装，正在安装...")
            install_result = sandbox.process.exec("npm install -g @anthropic-ai/claude-code 2>&1 | tail -3")
            print(f"安装结果: {(install_result.result or '')[:200]}")
            # 验证安装
            verify = sandbox.process.exec("claude --version 2>&1 || echo 'INSTALL_FAILED'")
            if "INSTALL_FAILED" in (verify.result or ""):
                print(f"错误: Claude Code CLI 安装失败", file=sys.stderr)
                _save_error_result("Claude Code CLI 安装失败")
                return 1
            print(f"Claude Code CLI 版本: {(verify.result or '').strip()}")
        else:
            print(f"Claude Code CLI 已存在: {(check_claude.result or '').strip()}")

        # 7. 上传文件到沙箱
        print(f"\n--- 上传文件到沙箱 --- [{time.time()-t0:.1f}s]")
        sandbox.process.exec(f"mkdir -p {REMOTE_TMP_DIR}")

        prompt_remote = f"{REMOTE_TMP_DIR}/prompt_expert_review.md"
        schema_remote = f"{REMOTE_TMP_DIR}/schema_expert_review.json"
        input_remote = f"{REMOTE_TMP_DIR}/input.txt"
        raw_remote = f"{REMOTE_TMP_DIR}/raw_output.txt"
        output_remote = f"{REMOTE_TMP_DIR}/output.json"
        repair_script = f"{REMOTE_TMP_DIR}/repair_json.py"

        sandbox.fs.upload_file(prompt_content.encode("utf-8"), prompt_remote)
        sandbox.fs.upload_file(schema_content.encode("utf-8"), schema_remote)
        sandbox.fs.upload_file(input_text.encode("utf-8"), input_remote)
        sandbox.fs.upload_file(_SANDBOX_REPAIR_SCRIPT.encode("utf-8"), repair_script)
        print("文件上传完成")

        # 8. 执行 Claude Code 命令
        print(f"\n--- 执行 Claude Code --- [{time.time()-t0:.1f}s]")
        claude_cmd = (
            f"cd {REMOTE_TMP_DIR} && "
            f"cat {input_remote} | claude -p "
            f"--system-prompt-file {prompt_remote} "
            f"--output-format json "
            f"--json-schema \"$(cat {schema_remote})\" "
            f"> {raw_remote} 2>{REMOTE_TMP_DIR}/stderr.log; "
            f"CLAUDE_RC=$?; "
            f"echo \"CLAUDE_EXIT_CODE=$CLAUDE_RC\"; "
            f"echo \"RAW_BYTES=$(wc -c < {raw_remote})\"; "
            f"python3 {repair_script} {raw_remote} {output_remote}"
        )
        print(f"命令: {claude_cmd[:200]}...")

        session_id = "expert-review-session"
        try:
            sandbox.process.create_session(session_id)
        except Exception:
            try:
                sandbox.process.delete_session(session_id)
            except Exception:
                pass
            sandbox.process.create_session(session_id)

        start_time = time.time()
        exec_resp = sandbox.process.execute_session_command(
            session_id,
            SessionExecuteRequest(command=claude_cmd, run_async=True),
        )
        cmd_id = exec_resp.cmd_id
        print(f"命令已提交 (cmd_id: {cmd_id})")

        # 9. 轮询等待完成
        stdout = ""
        stderr = ""
        exit_code = None
        last_stdout_len = 0

        while (time.time() - start_time) < CLAUDE_TIMEOUT:
            time.sleep(POLL_INTERVAL)
            try:
                logs = sandbox.process.get_session_command_logs(session_id, cmd_id)
            except Exception:
                continue
            stdout = logs.stdout or ""
            stderr = logs.stderr or ""
            if len(stdout) > last_stdout_len:
                last_stdout_len = len(stdout)
            try:
                cmd_info = sandbox.process.get_session_command(session_id, cmd_id)
                if cmd_info.exit_code is not None:
                    exit_code = cmd_info.exit_code
                    logs = sandbox.process.get_session_command_logs(session_id, cmd_id)
                    stdout = logs.stdout or ""
                    stderr = logs.stderr or ""
                    break
            except Exception:
                pass
            elapsed = time.time() - start_time
            if int(elapsed) % 30 == 0 and int(elapsed) > 0:
                print(f"  ... 运行中 {elapsed:.0f}s, stdout={len(stdout)}B, stderr={len(stderr)}B")
                if stderr and len(stderr) < 500:
                    print(f"  stderr: {stderr.strip()[:300]}")
        else:
            print(f"Claude 超时（{CLAUDE_TIMEOUT}s）")
            try:
                sandbox.process.delete_session(session_id)
            except Exception:
                pass
            exit_code = -1

        try:
            sandbox.process.delete_session(session_id)
        except Exception:
            pass

        elapsed = time.time() - start_time
        print(f"\nClaude 于 {elapsed:.1f}s 内完成, exit_code={exit_code}")

        if stdout.strip():
            print(f"标准输出:\n{stdout.strip()[:500]}")
        if stderr.strip():
            print(f"标准错误:\n{stderr.strip()[:500]}")

        # 10. 下载并处理输出
        print(f"\n--- 下载评审结果 --- [{time.time()-t0:.1f}s]")
        try:
            output_bytes = sandbox.fs.download_file(output_remote)
            claude_output = output_bytes.decode("utf-8").strip()
        except Exception as e:
            print(f"下载 output.json 失败: {e}，尝试下载 raw_output.txt")
            try:
                output_bytes = sandbox.fs.download_file(raw_remote)
                claude_output = output_bytes.decode("utf-8").strip()
            except Exception as e2:
                print(f"下载原始输出也失败: {e2}", file=sys.stderr)
                _save_error_result("下载评审结果失败")
                return 1

        if not claude_output:
            print("错误: Claude 返回空输出", file=sys.stderr)
            _save_error_result("Claude 返回空输出")
            return 1

        print(f"已下载 {len(claude_output)} 字符")

        # 11. 本地 JSON 修复
        claude_output = _try_repair_json(claude_output)

        try:
            result_obj = json.loads(claude_output)
        except json.JSONDecodeError as e:
            print(f"JSON 解析失败: {e}", file=sys.stderr)
            _save_error_result(f"JSON 解析失败: {e}")
            return 1

        # 提取评审结果（处理多层包装）
        # 情况 1: Claude API 包装 {"type":"result", "result": "...json string..."}
        if isinstance(result_obj.get("result"), str):
            inner_text = result_obj["result"]
            # 从 markdown 代码块中提取 JSON
            m = re.search(r"```json\s*(.*?)\s*```", inner_text, re.DOTALL)
            if m:
                inner_text = m.group(1).strip()
            # 从大括号区块提取
            fb = inner_text.find("{")
            lb = inner_text.rfind("}")
            if fb != -1 and lb > fb:
                try:
                    result_obj = json.loads(inner_text[fb:lb + 1])
                except json.JSONDecodeError:
                    pass

        # 情况 2: schema 包装 {"expert_review_result": {...}}
        if "expert_review_result" in result_obj:
            result_obj = result_obj["expert_review_result"]

        # 情况 3: 非标准键名 {"dimensions": {...}} → 转换为标准格式
        if "dimensions" in result_obj and "task_complexity" not in result_obj:
            dims = result_obj["dimensions"]
            result_obj = {
                **dims,
                "total_score": result_obj.get("total_score", 0),
                "overall_assessment": result_obj.get("overall_assessment", ""),
                "trace_highlights": result_obj.get("trace_highlights", []),
            }

        # 12. 保存结果
        result_dir = os.path.dirname(AI_REVIEW_RESULT_PATH)
        if result_dir:
            os.makedirs(result_dir, exist_ok=True)
        with open(AI_REVIEW_RESULT_PATH, "w", encoding="utf-8") as f:
            json.dump(result_obj, f, ensure_ascii=False, indent=2)
        print(f"\nAI 评审结果已保存: {AI_REVIEW_RESULT_PATH}")
        print(f"结果内容:\n{json.dumps(result_obj, ensure_ascii=False, indent=2)[:1000]}")

        return 0

    finally:
        print("\n--- 清理沙箱 ---")
        if sandbox:
            try:
                sandbox.refresh_data()
                if sandbox.state == SandboxState.STARTED:
                    sandbox.stop()
                    print("沙箱已停止")
                else:
                    print(f"沙箱状态: {sandbox.state}")
            except Exception as e:
                print(f"沙箱清理失败: {e}")
                try:
                    daytona.delete(sandbox)
                    print("沙箱已强制删除")
                except Exception:
                    pass


def _save_error_result(error_msg: str):
    """保存错误结果到本地文件。"""
    result = {
        "error": error_msg,
        "task_complexity": {"score": 0, "evidence": "评审失败"},
        "iteration_quality": {"score": 0, "evidence": "评审失败"},
        "professional_judgment": {"score": 0, "evidence": "评审失败"},
        "total_score": 0,
        "overall_assessment": f"AI 评审失败: {error_msg}",
        "trace_highlights": [],
    }
    result_dir = os.path.dirname(AI_REVIEW_RESULT_PATH)
    if result_dir:
        os.makedirs(result_dir, exist_ok=True)
    with open(AI_REVIEW_RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="专家考核产物 AI 评审")
    parser.add_argument("--record-id", required=True, help="飞书多维表格 record_id")
    args = parser.parse_args()

    try:
        exit_code = run_ai_review(args.record_id)
    except Exception as e:
        print(f"系统错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        _save_error_result(str(e))
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
