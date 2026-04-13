#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
从主表同步数据到评审表，并对每条记录执行粗筛。

流程:
1. 读取主表所有记录
2. 跳过评审表已有的记录
3. 对有附件的字段: 从主表下载 → 上传到评审表 → 用新 file_token 创建记录
4. 对每条评审表记录执行 pre_screen 7 项硬门槛
"""

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from core.feishu_utils import (
    normalize_field_value,
    extract_attachment_file_token,
    extract_link_url,
)
from core.trace_parser import parse_trace_file
from projects.expert_review.pre_screen import (
    check_task_authenticity,
    check_trace_integrity,
    check_tool_loop_exists,
    check_final_product_exists,
    check_verification_exists,
    check_trace_product_consistent,
    check_compliance,
)

APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")

MAIN_APP_TOKEN = "TdiNb881Ja89b9s6FwNcOH1Vn4c"
MAIN_TABLE_ID = "tblE2Qdot3No2kUC"

REVIEW_APP_TOKEN = "INiPbaSwsaKCffszIDwc1dYPnph"
REVIEW_TABLE_ID = "tblzyPQ33dOle6lY"


class FeishuAPI:
    def __init__(self):
        self._token = ""
        self._token_time = 0

    def token(self):
        if self._token and time.time() - self._token_time < 6000:
            return self._token
        resp = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": APP_ID, "app_secret": APP_SECRET}, timeout=30)
        self._token = resp.json()["tenant_access_token"]
        self._token_time = time.time()
        return self._token

    def headers(self):
        return {"Authorization": f"Bearer {self.token()}"}

    def get_all_records(self, app_token, table_id):
        all_records = []
        page_token = None
        while True:
            url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size=100"
            if page_token:
                url += f"&page_token={page_token}"
            resp = requests.get(url, headers=self.headers(), timeout=30)
            data = resp.json().get("data", {})
            all_records.extend(data.get("items", []))
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
        return all_records

    def get_record(self, app_token, table_id, record_id):
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
        resp = requests.get(url, headers=self.headers(), timeout=30)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取记录失败: {data}")
        return data["data"]["record"]

    def create_record(self, app_token, table_id, fields):
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        h = self.headers()
        h["Content-Type"] = "application/json"
        resp = requests.post(url, headers=h, json={"fields": fields}, timeout=30)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"创建记录失败: {data}")
        return data["data"]["record"]

    def update_record(self, app_token, table_id, record_id, fields):
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
        h = self.headers()
        h["Content-Type"] = "application/json"
        resp = requests.put(url, headers=h, json={"fields": fields}, timeout=30)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"更新记录失败: {data}")
        return data

    def download_file(self, file_token, output_path, download_url=None):
        """下载文件。如果提供 download_url 则直接使用（bitable附件需要带extra参数）。"""
        if download_url:
            url = download_url
        else:
            url = f"https://open.feishu.cn/open-apis/drive/v1/medias/{file_token}/download"
        resp = requests.get(url, headers=self.headers(), timeout=120, stream=True)
        resp.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return os.path.getsize(output_path)

    def upload_file(self, app_token, table_id, local_path, file_name):
        """上传文件到多维表格，获得新的 file_token。"""
        url = "https://open.feishu.cn/open-apis/drive/v1/medias/upload_all"
        size = os.path.getsize(local_path)
        h = {"Authorization": f"Bearer {self.token()}"}
        with open(local_path, "rb") as f:
            resp = requests.post(url, headers=h, data={
                "file_name": file_name,
                "parent_type": "bitable_file",
                "parent_node": app_token,
                "size": str(size),
            }, files={"file": (file_name, f)}, timeout=120)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"上传文件失败: {data}")
        return data["data"]["file_token"]

    def transfer_attachment(self, src_file_token, src_file_name, src_download_url,
                             dest_app_token, dest_table_id):
        """从一个 bitable 下载附件，再上传到另一个 bitable，返回新 file_token。"""
        tmp_path = os.path.join(tempfile.gettempdir(), f"transfer_{src_file_token}_{src_file_name}")
        try:
            self.download_file(src_file_token, tmp_path, download_url=src_download_url)
            new_token = self.upload_file(dest_app_token, dest_table_id, tmp_path, src_file_name)
            return new_token
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


def extract_person_name(field_value):
    if isinstance(field_value, dict):
        return field_value.get("name", field_value.get("text", ""))
    if isinstance(field_value, list) and field_value:
        first = field_value[0]
        if isinstance(first, dict):
            return first.get("name", first.get("text", ""))
    return str(field_value)[:30] if field_value else ""


def extract_select_value(field_value):
    if isinstance(field_value, str):
        return field_value
    if isinstance(field_value, list) and field_value:
        return field_value[0] if isinstance(field_value[0], str) else str(field_value[0])
    if isinstance(field_value, dict):
        return field_value.get("text", field_value.get("value", str(field_value)))
    return str(field_value) if field_value else ""


def map_main_to_review(api, main_fields, main_record_id):
    """将主表字段映射到评审表字段，附件做跨表迁移。"""
    review_fields = {}
    review_fields["record_id"] = main_record_id

    name = extract_person_name(main_fields.get("提交人", ""))
    if name:
        review_fields["专家姓名"] = name

    talent_id = main_fields.get("talent_id", "")
    if talent_id:
        review_fields["专家ID"] = str(int(talent_id)) if isinstance(talent_id, float) else str(talent_id)

    position = extract_select_value(main_fields.get("岗位方向", ""))
    if position:
        review_fields["岗位方向"] = position

    task_desc = normalize_field_value(main_fields.get("任务说明", ""))
    if task_desc:
        review_fields["任务描述"] = task_desc

    # Trace文件 — 跨表迁移附件
    trace_file = main_fields.get("Trace 文件")
    if isinstance(trace_file, list) and trace_file:
        first = trace_file[0]
        if isinstance(first, dict) and first.get("file_token"):
            try:
                fname = first.get("name", "trace.jsonl")
                dl_url = first.get("url", "")
                new_token = api.transfer_attachment(
                    first["file_token"], fname, dl_url,
                    REVIEW_APP_TOKEN, REVIEW_TABLE_ID,
                )
                review_fields["Trace文件"] = [{"file_token": new_token}]
                print(f"    Trace文件迁移成功: {fname}")
            except Exception as e:
                print(f"    Trace文件迁移失败: {e}")

    # 最终产物 — 跨表迁移附件
    final_product = main_fields.get("最终产物")
    if isinstance(final_product, list) and final_product:
        new_attachments = []
        for att in final_product:
            if isinstance(att, dict) and att.get("file_token"):
                try:
                    fname = att.get("name", "product")
                    dl_url = att.get("url", "")
                    new_token = api.transfer_attachment(
                        att["file_token"], fname, dl_url,
                        REVIEW_APP_TOKEN, REVIEW_TABLE_ID,
                    )
                    new_attachments.append({"file_token": new_token})
                    print(f"    最终产物迁移成功: {fname}")
                except Exception as e:
                    print(f"    最终产物迁移失败: {e}")
        if new_attachments:
            review_fields["最终附件"] = new_attachments

    return review_fields


def run_pre_screen_for_record(api, review_record_id):
    """对评审表单条记录执行 7 项硬门槛检查。"""
    rec = api.get_record(REVIEW_APP_TOKEN, REVIEW_TABLE_ID, review_record_id)
    fields = rec.get("fields", {})

    results = []
    c1 = check_task_authenticity(fields, "任务描述", 50)
    results.append(c1)

    trace_field = fields.get("Trace文件")
    file_token = extract_attachment_file_token(trace_field)
    trace_content = ""

    # 获取附件的下载 URL（bitable 附件需要带 extra 参数）
    trace_download_url = ""
    if isinstance(trace_field, list) and trace_field:
        first = trace_field[0]
        if isinstance(first, dict):
            trace_download_url = first.get("url", "")

    from core.trace_parser import TraceAnalysis
    trace = TraceAnalysis()

    if file_token:
        tmp_path = os.path.join(tempfile.gettempdir(), f"trace_{review_record_id}.jsonl")
        try:
            api.download_file(file_token, tmp_path, download_url=trace_download_url)
            trace = parse_trace_file(tmp_path)
            with open(tmp_path, "r", encoding="utf-8") as f:
                trace_content = f.read()
        except Exception as e:
            results.append({"check": "trace_download", "passed": False, "detail": f"下载失败: {e}", "action": "reject"})
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    c2 = check_trace_integrity(fields, "Trace文件", trace, 3)
    results.append(c2)

    c3 = check_tool_loop_exists(trace)
    results.append(c3)

    c4 = check_final_product_exists(fields, "最终产物", "最终附件")
    results.append(c4)

    c5 = check_verification_exists(trace_content)
    results.append(c5)

    has_product = bool(
        extract_link_url(fields.get("最终产物", ""))
        or extract_attachment_file_token(fields.get("最终附件"))
    )
    c6 = check_trace_product_consistent(trace, has_product)
    results.append(c6)

    c7 = check_compliance(trace_content)
    results.append(c7)

    rejected = [r for r in results if not r["passed"] and r.get("action") == "reject"]
    manual_review = [r for r in results if not r["passed"] and r.get("action") == "manual_review"]

    if rejected:
        status = "拒绝"
    elif manual_review:
        status = "待人工复核"
    else:
        status = "通过"

    return {
        "粗筛状态": status,
        "checks": results,
        "passed_count": sum(1 for r in results if r["passed"]),
        "total_count": len(results),
    }


def main():
    if not APP_ID or not APP_SECRET:
        print("错误: 请设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET", file=sys.stderr)
        sys.exit(1)

    api = FeishuAPI()

    print("=" * 70)
    print("步骤 1: 从主表同步数据到评审表")
    print("=" * 70)

    # 读取主表
    print("\n--- 读取主表记录 ---")
    main_records = get_main_records(api)

    # 读取评审表已有记录
    print("\n--- 读取评审表已有记录 ---")
    review_records = api.get_all_records(REVIEW_APP_TOKEN, REVIEW_TABLE_ID)
    existing_rids = set()
    for rec in review_records:
        rid = normalize_field_value(rec["fields"].get("record_id", ""))
        if rid:
            existing_rids.add(rid)
    print(f"评审表已有 {len(review_records)} 条记录")

    # 同步
    print("\n--- 开始同步 ---")
    synced = 0
    skipped = 0
    new_review_rids = []  # 新创建的评审表 record_id

    for main_rec in main_records:
        main_rid = main_rec["record_id"]
        main_fields = main_rec["fields"]
        name = extract_person_name(main_fields.get("提交人", ""))

        if main_rid in existing_rids:
            print(f"  [跳过] {main_rid} ({name}) — 评审表已存在")
            skipped += 1
            continue

        print(f"  [同步] {main_rid} ({name})...")
        try:
            review_fields = map_main_to_review(api, main_fields, main_rid)
            new_rec = api.create_record(REVIEW_APP_TOKEN, REVIEW_TABLE_ID, review_fields)
            new_rid = new_rec["record_id"]
            print(f"    创建成功 → {new_rid}")
            synced += 1
            new_review_rids.append(new_rid)
        except Exception as e:
            print(f"    创建失败: {e}")

    print(f"\n同步完成: 新建 {synced}, 跳过 {skipped}")

    # ============================================================
    print(f"\n{'=' * 70}")
    print("步骤 2: 对每条记录执行 7 项硬门槛初筛")
    print("=" * 70)

    # 重新读取评审表
    review_records = api.get_all_records(REVIEW_APP_TOKEN, REVIEW_TABLE_ID)
    print(f"评审表共 {len(review_records)} 条记录\n")

    for i, rec in enumerate(review_records):
        review_rid = rec["record_id"]
        fields = rec["fields"]
        name = normalize_field_value(fields.get("专家姓名", ""))
        rid = normalize_field_value(fields.get("record_id", ""))

        print(f"--- [{i+1}/{len(review_records)}] {name or '(空)'} ({rid or review_rid}) ---")

        t0 = time.time()
        result = run_pre_screen_for_record(api, review_rid)
        elapsed = time.time() - t0

        status = result["粗筛状态"]
        passed = result["passed_count"]
        total = result["total_count"]
        print(f"  结果: {status} ({passed}/{total} 通过, {elapsed:.1f}s)")

        failed = [c for c in result.get("checks", []) if not c["passed"]]
        for c in failed:
            print(f"    ✗ {c['check']}: {c['detail'][:70]}")

        # 回填
        try:
            api.update_record(REVIEW_APP_TOKEN, REVIEW_TABLE_ID, review_rid, {
                "粗筛状态": status,
                "粗筛详情": json.dumps(result, ensure_ascii=False, indent=2),
            })
        except Exception as e:
            print(f"  回填失败: {e}")
        print()

    print("=" * 70)
    print("全部完成!")
    print("=" * 70)


def get_main_records(api):
    records = api.get_all_records(MAIN_APP_TOKEN, MAIN_TABLE_ID)
    print(f"主表共 {len(records)} 条记录")
    return records


if __name__ == "__main__":
    main()
