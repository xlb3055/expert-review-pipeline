#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Trace 附件下载与合并工具。"""

import os
from dataclasses import dataclass, field

from core.feishu_utils import extract_attachment_entries


@dataclass
class TraceBundle:
    merged_path: str
    attachment_count: int = 0
    attachment_names: list = field(default_factory=list)
    total_bytes: int = 0


def download_and_merge_trace_attachments(client, trace_field, output_path: str) -> TraceBundle:
    """下载一个或多个 Trace 附件，并按顺序合并成单个 jsonl 文件。"""
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

            with open(temp_path, "rb") as src:
                data = src.read()

            try:
                os.remove(temp_path)
            except OSError:
                pass

            if not data:
                continue

            merged.write(data)
            bundle.total_bytes += len(data)
            if not data.endswith(b"\n"):
                merged.write(b"\n")

    return bundle
