#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Trace 附件下载与合并工具。"""

import gzip
import os
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field

from core.feishu_utils import extract_attachment_entries


@dataclass
class TraceBundle:
    merged_path: str
    attachment_count: int = 0
    attachment_names: list = field(default_factory=list)
    total_bytes: int = 0


def _detect_archive_type(filepath: str) -> str | None:
    """通过文件头魔数检测压缩包类型，不依赖文件名后缀。"""
    try:
        with open(filepath, "rb") as f:
            header = f.read(8)
    except OSError:
        return None

    if not header:
        return None

    # ZIP: PK\x03\x04
    if header[:4] == b"PK\x03\x04":
        return "zip"
    # RAR: Rar!\x1a\x07
    if header[:6] == b"Rar!\x1a\x07":
        return "rar"
    # GZIP: \x1f\x8b
    if header[:2] == b"\x1f\x8b":
        return "gz"
    # 7Z: 7z\xbc\xaf\x27\x1c
    if header[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"

    return None


def _extract_archive(filepath: str, archive_type: str, extract_dir: str) -> list[str]:
    """解压压缩包，返回解压出的文件路径列表。"""
    os.makedirs(extract_dir, exist_ok=True)

    if archive_type == "zip":
        with zipfile.ZipFile(filepath, "r") as zf:
            zf.extractall(extract_dir)

    elif archive_type == "gz":
        out_path = os.path.join(extract_dir, "trace.jsonl")
        with gzip.open(filepath, "rb") as gz_in, open(out_path, "wb") as out:
            while True:
                chunk = gz_in.read(65536)
                if not chunk:
                    break
                out.write(chunk)

    elif archive_type == "rar":
        try:
            import rarfile
            with rarfile.RarFile(filepath) as rf:
                rf.extractall(extract_dir)
        except ImportError:
            # fallback 到系统命令
            if _cmd_exists("unrar"):
                subprocess.run(["unrar", "x", "-o+", filepath, extract_dir], capture_output=True, timeout=60)
            elif _cmd_exists("7z"):
                subprocess.run(["7z", "x", filepath, f"-o{extract_dir}", "-y"], capture_output=True, timeout=60)
            else:
                _auto_install_unrar()
                if _cmd_exists("unrar"):
                    subprocess.run(["unrar", "x", "-o+", filepath, extract_dir], capture_output=True, timeout=60)
                else:
                    raise RuntimeError("无法解压 .rar 文件，请安装 rarfile (pip install rarfile)")

    elif archive_type == "7z":
        try:
            import py7zr
            with py7zr.SevenZipFile(filepath, mode="r") as sz:
                sz.extractall(extract_dir)
        except ImportError:
            if _cmd_exists("7z"):
                subprocess.run(["7z", "x", filepath, f"-o{extract_dir}", "-y"], capture_output=True, timeout=60)
            else:
                raise RuntimeError("无法解压 .7z 文件，请安装 py7zr (pip install py7zr)")

    # 收集解压出的所有文件
    extracted = []
    for root, _dirs, files in os.walk(extract_dir):
        for fname in sorted(files):
            extracted.append(os.path.join(root, fname))
    return extracted


def _cmd_exists(cmd: str) -> bool:
    """检查系统命令是否存在。"""
    from shutil import which
    return which(cmd) is not None


def _auto_install_unrar():
    """运行时尝试 pip install rarfile，再 fallback 到 apt。"""
    print("尝试自动安装 rar 解压支持...")
    try:
        subprocess.run(["pip", "install", "-q", "rarfile"], capture_output=True, timeout=30)
        import importlib
        importlib.import_module("rarfile")
        print("rarfile 安装成功")
        return
    except Exception:
        pass
    # fallback: apt install unrar
    try:
        subprocess.run(["apt-get", "update", "-qq"], capture_output=True, timeout=60)
        subprocess.run(["apt-get", "install", "-y", "-qq", "unrar-free"], capture_output=True, timeout=60)
        if _cmd_exists("unrar"):
            print("unrar 安装成功")
    except Exception as e:
        print(f"自动安装 unrar 失败: {e}")


def _is_jsonl_content(filepath: str) -> bool:
    """快速检查文件是否像 JSONL 内容（首字节是 { 或 [）。"""
    try:
        with open(filepath, "rb") as f:
            first_bytes = f.read(32).lstrip()
        return len(first_bytes) > 0 and first_bytes[0:1] in (b"{", b"[")
    except OSError:
        return False


def download_and_merge_trace_attachments(client, trace_field, output_path: str) -> TraceBundle:
    """下载一个或多个 Trace 附件，并按顺序合并成单个 jsonl 文件。

    支持附件为压缩包（rar/zip/gz/7z）的情况，会自动解压后提取 JSONL 内容。
    """
    attachments = [
        entry for entry in extract_attachment_entries(trace_field)
        if entry.get("file_token")
    ]
    bundle = TraceBundle(
        merged_path=output_path,
        attachment_count=len(attachments),
        attachment_names=[entry.get("name", "") or f"trace_{idx + 1}.jsonl"
                          for idx, entry in enumerate(attachments)],
    )

    if not attachments:
        return bundle

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with open(output_path, "wb") as merged:
        for idx, entry in enumerate(attachments):
            temp_path = f"{output_path}.part{idx}"
            download_url = entry.get("url", "") or entry.get("tmp_url", "")
            client.download_attachment(
                entry["file_token"],
                temp_path,
                download_url=download_url or None,
            )

            data_files = _resolve_to_data_files(temp_path)

            for data_file in data_files:
                with open(data_file, "rb") as src:
                    data = src.read()

                if not data:
                    continue

                merged.write(data)
                bundle.total_bytes += len(data)
                if not data.endswith(b"\n"):
                    merged.write(b"\n")

            # 清理临时文件
            try:
                os.remove(temp_path)
            except OSError:
                pass

    return bundle


def _resolve_to_data_files(filepath: str) -> list[str]:
    """将下载的文件解析为可直接读取的数据文件列表。

    如果是压缩包则解压，否则直接返回原文件。
    """
    archive_type = _detect_archive_type(filepath)

    if archive_type is None:
        # 不是压缩包，直接返回
        return [filepath]

    # 是压缩包，解压到临时目录
    extract_dir = filepath + ".extracted"
    att_name = os.path.basename(filepath)
    print(f"检测到压缩包({archive_type}): {att_name}，正在解压...")

    try:
        extracted_files = _extract_archive(filepath, archive_type, extract_dir)
    except Exception as e:
        print(f"解压失败: {e}，尝试按原始文件处理")
        return [filepath]

    if not extracted_files:
        print("解压后未找到文件，按原始文件处理")
        return [filepath]

    # 筛选出 JSONL 内容的文件
    jsonl_files = [f for f in extracted_files if _is_jsonl_content(f)]

    if jsonl_files:
        print(f"解压得到 {len(jsonl_files)} 个 JSONL 文件: {[os.path.basename(f) for f in jsonl_files]}")
        return jsonl_files

    # 没有明显的 JSONL 文件，返回所有非空文件
    print(f"解压得到 {len(extracted_files)} 个文件（未检测到 JSONL），按顺序合并")
    return extracted_files
