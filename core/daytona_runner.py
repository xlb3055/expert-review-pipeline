#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Daytona 沙箱 + Claude Code 执行器

封装沙箱生命周期（创建→上传→执行→轮询→下载→清理）和 JSON 修复逻辑，
供业务脚本通过一次 run_claude_in_sandbox() 调用完成全部操作。
"""

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

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


# ========== 数据类 ==========

@dataclass
class DaytonaRunConfig:
    """Daytona 沙箱 + Claude 执行配置。"""
    api_key: str = ""
    snapshot: str = "daytona-medium"
    cpu: int = 2
    memory: int = 4
    disk: int = 5
    openrouter_base_url: str = "https://openrouter.ai/api"
    openrouter_api_key: str = ""
    model: str = "anthropic/claude-sonnet-4-6"
    timeout: int = 600
    poll_interval: int = 5
    sandbox_name_prefix: str = "expert_review"


@dataclass
class ClaudeRunResult:
    """Claude 沙箱执行结果。"""
    success: bool = False
    result_json: Optional[dict] = None
    raw_output: str = ""
    error: str = ""
    elapsed_seconds: float = 0.0


# ========== 沙箱内 JSON 修复脚本 ==========

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


# ========== 本地 JSON 修复 ==========

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


# ========== 主执行函数 ==========

def run_claude_in_sandbox(
    config: DaytonaRunConfig,
    prompt: str,
    schema: str,
    input_text: str,
) -> ClaudeRunResult:
    """
    在 Daytona 沙箱中执行 Claude Code，返回结构化结果。

    参数:
        config: 沙箱和 Claude 配置
        prompt: 系统提示词内容
        schema: JSON Schema 内容
        input_text: 用户输入文本
    """
    result = ClaudeRunResult()
    t0 = time.time()

    if not config.api_key:
        result.error = "DAYTONA_API_KEY 未设置"
        return result
    if not config.openrouter_api_key:
        result.error = "OPENROUTER_API_KEY 未设置"
        return result

    remote_tmp = "/tmp/claude_run"
    daytona = Daytona(DaytonaConfig(api_key=config.api_key))
    sandbox_name = f"{config.sandbox_name_prefix}-{uuid.uuid4().hex[:6]}"
    sandbox = None

    try:
        # 1. 创建沙箱
        print(f"创建 Daytona 沙箱: {sandbox_name}")
        create_params = CreateSandboxFromSnapshotParams(
            name=sandbox_name,
            snapshot=config.snapshot,
            network_block_all=False,
            auto_stop_interval=0,
            auto_delete_interval=0,
            resources=Resources(cpu=config.cpu, memory=config.memory, disk=config.disk),
            env_vars={
                "ANTHROPIC_BASE_URL": config.openrouter_base_url,
                "ANTHROPIC_AUTH_TOKEN": config.openrouter_api_key,
                "ANTHROPIC_API_KEY": "",
                "ANTHROPIC_MODEL": config.model,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": config.model,
                "API_TIMEOUT_MS": "300000",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "CI": "1",
            },
        )

        try:
            sandbox = daytona.create(create_params, timeout=0)
        except DaytonaError as e:
            err_msg = str(e).lower()
            if "already exists" in err_msg:
                print(f"沙箱名已存在，删除后重试: {e}")
                try:
                    daytona.delete(daytona.get(sandbox_name))
                    time.sleep(2)
                except Exception:
                    pass
                sandbox = daytona.create(create_params, timeout=0)
            elif "memory limit" in err_msg or "resource" in err_msg:
                # 内存/资源超限：清理所有同前缀的残留沙箱后重试
                print(f"资源超限，尝试清理残留沙箱: {e}")
                try:
                    paginated = daytona.list()
                    all_sandboxes = getattr(paginated, "items", paginated) or []
                    for sb in all_sandboxes:
                        sb_name = getattr(sb, "name", "") or ""
                        if sb_name.startswith(config.sandbox_name_prefix):
                            print(f"  清理残留沙箱: {sb_name}")
                            try:
                                daytona.delete(sb)
                            except Exception:
                                pass
                    time.sleep(3)
                    sandbox = daytona.create(create_params, timeout=0)
                except Exception as retry_err:
                    raise DaytonaError(f"清理后重试仍失败: {retry_err}") from e
            else:
                raise

        print(f"沙箱已创建: {sandbox.id}")

        # 2. 检查并安装 Claude Code CLI
        print(f"检查 Claude Code CLI... [{time.time()-t0:.1f}s]")
        check_claude = sandbox.process.exec("which claude || echo 'NOT_FOUND'")
        if "NOT_FOUND" in (check_claude.result or ""):
            print("Claude Code CLI 未安装，正在安装...")
            install_result = sandbox.process.exec(
                "npm install -g @anthropic-ai/claude-code 2>&1 | tail -3"
            )
            print(f"安装结果: {(install_result.result or '')[:200]}")
            verify = sandbox.process.exec("claude --version 2>&1 || echo 'INSTALL_FAILED'")
            if "INSTALL_FAILED" in (verify.result or ""):
                result.error = "Claude Code CLI 安装失败"
                return result
            print(f"Claude Code CLI 版本: {(verify.result or '').strip()}")
        else:
            print(f"Claude Code CLI 已存在: {(check_claude.result or '').strip()}")

        # 3. 上传文件
        print(f"上传文件到沙箱... [{time.time()-t0:.1f}s]")
        sandbox.process.exec(f"mkdir -p {remote_tmp}")

        prompt_remote = f"{remote_tmp}/prompt.md"
        schema_remote = f"{remote_tmp}/schema.json"
        input_remote = f"{remote_tmp}/input.txt"
        raw_remote = f"{remote_tmp}/raw_output.txt"
        output_remote = f"{remote_tmp}/output.json"
        repair_script = f"{remote_tmp}/repair_json.py"

        sandbox.fs.upload_file(prompt.encode("utf-8"), prompt_remote)
        sandbox.fs.upload_file(schema.encode("utf-8"), schema_remote)
        sandbox.fs.upload_file(input_text.encode("utf-8"), input_remote)
        sandbox.fs.upload_file(_SANDBOX_REPAIR_SCRIPT.encode("utf-8"), repair_script)
        print("文件上传完成")

        # 4. 执行 Claude Code
        print(f"执行 Claude Code... [{time.time()-t0:.1f}s]")
        claude_cmd = (
            f"cd {remote_tmp} && "
            f"cat {input_remote} | claude -p "
            f"--system-prompt-file {prompt_remote} "
            f"--output-format json "
            f"--json-schema \"$(cat {schema_remote})\" "
            f"> {raw_remote} 2>{remote_tmp}/stderr.log; "
            f"CLAUDE_RC=$?; "
            f"echo \"CLAUDE_EXIT_CODE=$CLAUDE_RC\"; "
            f"echo \"RAW_BYTES=$(wc -c < {raw_remote})\"; "
            f"python3 {repair_script} {raw_remote} {output_remote}"
        )

        session_id = f"claude-run-{uuid.uuid4().hex[:6]}"
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

        # 5. 轮询等待
        stdout = ""
        stderr = ""
        exit_code = None
        last_stdout_len = 0

        while (time.time() - start_time) < config.timeout:
            time.sleep(config.poll_interval)
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
            print(f"Claude 超时（{config.timeout}s）")
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
        result.elapsed_seconds = elapsed
        print(f"Claude 于 {elapsed:.1f}s 内完成, exit_code={exit_code}")

        if stdout.strip():
            print(f"标准输出:\n{stdout.strip()[:500]}")
        if stderr.strip():
            print(f"标准错误:\n{stderr.strip()[:500]}")

        # 6. 下载结果
        print(f"下载评审结果... [{time.time()-t0:.1f}s]")
        try:
            output_bytes = sandbox.fs.download_file(output_remote)
            claude_output = output_bytes.decode("utf-8").strip()
        except Exception as e:
            print(f"下载 output.json 失败: {e}，尝试下载 raw_output.txt")
            try:
                output_bytes = sandbox.fs.download_file(raw_remote)
                claude_output = output_bytes.decode("utf-8").strip()
            except Exception as e2:
                result.error = f"下载评审结果失败: {e2}"
                return result

        if not claude_output:
            result.error = "Claude 返回空输出"
            return result

        result.raw_output = claude_output
        print(f"已下载 {len(claude_output)} 字符")

        # 7. 本地 JSON 修复
        claude_output = _try_repair_json(claude_output)

        try:
            result_obj = json.loads(claude_output)
        except json.JSONDecodeError as e:
            result.error = f"JSON 解析失败: {e}"
            return result

        # 处理多层包装
        if isinstance(result_obj.get("result"), str):
            inner_text = result_obj["result"]
            m = re.search(r"```json\s*(.*?)\s*```", inner_text, re.DOTALL)
            if m:
                inner_text = m.group(1).strip()
            fb = inner_text.find("{")
            lb = inner_text.rfind("}")
            if fb != -1 and lb > fb:
                try:
                    result_obj = json.loads(inner_text[fb:lb + 1])
                except json.JSONDecodeError:
                    pass

        result.success = True
        result.result_json = result_obj
        result.elapsed_seconds = time.time() - t0
        return result

    finally:
        print("清理沙箱...")
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
