#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Trace 附件下载与合并工具。"""

import gzip
import os
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
    """通过文件头魔数检测压缩包类型。"""
    try:
        with open(filepath, "rb") as f:
            header = f.read(8)
    except OSError:
        return None

    if not header:
        return None

    if header[:4] == b"PK\x03\x04":
        return "zip"
    if header[:6] == b"Rar!\x1a\x07":
        return "rar"
    if header[:2] == b"\x1f\x8b":
        return "gz"
    if header[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"

    return None


def _is_jsonl_content(filepath: str) -> bool:
    """快速检查文件是否像 JSONL 内容。"""
    try:
        with open(filepath, "rb") as f:
            first_bytes = f.read(32).lstrip()
        return len(first_bytes) > 0 and first_bytes[0:1] in (b"{", b"[")
    except OSError:
        return False


def _extract_to_data_files(filepath: str, archive_type: str) -> list[str] | None:
    """尝试解压，返回文件列表。仅支持 zip/gz（Python 内置库），其他格式返回 None。"""
    extract_dir = filepath + ".extracted"
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
    else:
        # rar/7z 等不支持，返回 None
        return None

    extracted = []
    for root, _dirs, files in os.walk(extract_dir):
        for fname in sorted(files):
            extracted.append(os.path.join(root, fname))
    return extracted


def download_and_merge_trace_attachments(client, trace_field, output_path: str) -> TraceBundle:
    """下载一个或多个 Trace 附件，并按顺序合并成单个 jsonl 文件。

    支持 zip/gz 压缩包自动解压。rar/7z 等格式跳过（需人工审核）。
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

            data_files = [temp_path]

            # 检测是否是压缩包
            archive_type = _detect_archive_type(temp_path)
            if archive_type:
                att_name = entry.get("name", "") or os.path.basename(temp_path)
                extracted = _extract_to_data_files(temp_path, archive_type)
                if extracted:
                    print(f"已解压 {att_name}({archive_type})，得到 {len(extracted)} 个文件")
                    data_files = extracted
                else:
                    print(f"附件 {att_name} 是 {archive_type} 压缩包，当前环境不支持解压，需人工审核")
                    data_files = []

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
