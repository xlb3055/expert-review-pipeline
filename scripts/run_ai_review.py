#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
对评审表中粗筛通过/待复核的记录执行 AI 评审，并回填飞书。

流程:
1. 读取评审表所有记录
2. 筛选粗筛状态为"通过"或"待人工复核"的记录
3. 对每条记录: 下载Trace → 调用 Claude API 评审 → 提取分数 → 回填飞书
"""

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import anthropic
import requests

from core.feishu_utils import (
    normalize_field_value,
    extract_attachment_file_token,
    extract_link_url,
)
from core.trace_parser import parse_trace_file, truncate_trace_content
from core.trace_extractor import extract_user_focused_content

# ---------- 配置 ----------

APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")

REVIEW_APP_TOKEN = "INiPbaSwsaKCffszIDwc1dYPnph"
REVIEW_TABLE_ID = "tblzyPQ33dOle6lY"

PROMPT_FILE = os.path.join(os.path.dirname(__file__), "..", "projects", "expert_review", "prompt.md")
SCHEMA_FILE = os.path.join(os.path.dirname(__file__), "..", "projects", "expert_review", "schema.json")

# OpenRouter via anthropic SDK
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api"
MODEL = "anthropic/claude-sonnet-4-6"


# ---------- 飞书 API ----------

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


# ---------- AI 评审 ----------

def build_input_text(fields: dict, trace_content: str) -> str:
    """组装 AI 评审输入文本。"""
    task_desc = normalize_field_value(fields.get("任务描述", ""))
    expert_name = normalize_field_value(fields.get("专家姓名", ""))
    expert_id = normalize_field_value(fields.get("专家ID", ""))
    position = normalize_field_value(fields.get("岗位方向", ""))
    product_link = extract_link_url(fields.get("最终产物", ""))

    parts = [
        "# 专家考核产物 — AI 评审输入",
        "",
        "## 专家信息",
        f"- 姓名: {expert_name}",
        f"- ID: {expert_id}",
        f"- 岗位方向: {position}",
        "",
        "## 任务描述（专家撰写的 Prompt）",
        task_desc,
        "",
    ]

    if product_link:
        parts.extend([
            "## 最终产物链接",
            product_link,
            "",
        ])

    parts.extend([
        "## Claude Code Trace 精简摘要",
        "以下是从 Trace 中提取的精简内容，仅包含：",
        "- 用户（专家）的完整消息文本",
        "- AI 使用的工具名称和输入摘要",
        "- AI 文本回复的前 150 字摘要",
        "",
        "注意：AI 的完整回复和工具返回的详细内容已省略，请聚焦于评估**专家的行为**（任务定义、迭代引导、纠偏、验证等）。",
        "",
        trace_content,
    ])

    return "\n".join(parts)


def call_claude_review(prompt_content: str, schema_content: str, input_text: str) -> dict:
    """调用 Claude API 进行评审，返回结构化 JSON 结果。"""
    client = anthropic.Anthropic(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
    )

    schema_obj = json.loads(schema_content)

    system_msg = prompt_content + "\n\n请按照以下 JSON Schema 输出结果：\n```json\n" + schema_content + "\n```"

    user_msg = input_text + "\n\n请基于以上材料完成评审，仅输出一个符合 JSON Schema 的 JSON 对象，不要任何额外文字。"

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=system_msg,
        messages=[{"role": "user", "content": user_msg}],
    )

    # 提取回复文本
    reply = ""
    for block in response.content:
        if block.type == "text":
            reply += block.text

    # 从回复中解析 JSON
    reply = reply.strip()
    # 去掉可能的 markdown 代码块包裹
    if reply.startswith("```"):
        lines = reply.split("\n")
        # 去掉第一行 ```json 和最后一行 ```
        start = 1
        end = len(lines)
        if lines[-1].strip() == "```":
            end = -1
        reply = "\n".join(lines[start:end])

    result = json.loads(reply)

    # 如果被 schema name 包裹了，解包
    if "expert_review_result" in result and "expert_ability" not in result:
        result = result["expert_review_result"]

    return result


def extract_scores(ai_result: dict, module_key: str, dimensions: list) -> dict:
    """从 AI 评审结果中提取指定模块的维度分数。"""
    module_data = ai_result.get(module_key, {})
    scores = {}
    total = 0

    for dim in dimensions:
        key = dim["key"]
        max_score = dim["max_score"]
        val = module_data.get(key, {})
        if isinstance(val, dict):
            score = val.get("score", 0)
        elif isinstance(val, (int, float)):
            score = val
        else:
            score = 0
        score = max(0, min(int(score), max_score))
        scores[key] = score
        total += score

    scores["total"] = total
    return scores


def determine_conclusion(expert_total: int, trace_total: int, pre_screen_status: str) -> str:
    """判定最终结论。"""
    if pre_screen_status == "拒绝":
        return "拒绝"

    labels = []
    if expert_total >= 7:
        labels.append("可储备专家")
    if trace_total >= 9:
        labels.append("高价值trace")

    if labels:
        return " + ".join(labels)
    elif expert_total >= 5 or trace_total >= 6:
        return "待人工复核"
    else:
        return "拒绝"


# ---------- 维度配置 ----------

EXPERT_DIMS = [
    {"key": "task_complexity", "max_score": 3},
    {"key": "iteration_quality", "max_score": 3},
    {"key": "professional_judgment", "max_score": 4},
]

TRACE_DIMS = [
    {"key": "authenticity", "max_score": 2},
    {"key": "info_density", "max_score": 2},
    {"key": "tool_loop", "max_score": 2},
    {"key": "correction_value", "max_score": 2},
    {"key": "verification_loop", "max_score": 2},
    {"key": "compliance", "max_score": 2},
]

# 字段映射
FIELD_MAP = {
    "ai_review_status": "AI评审状态",
    "ai_review_result": "AI评审结果",
    "final_conclusion": "最终结论",
    "expert_ability_total": "总分",
    "trace_asset_total": "Trace资产总分",
    "task_complexity_score": "任务复杂度",
    "iteration_quality_score": "迭代质量",
    "professional_judgment_score": "专业判断",
    "authenticity_score": "真实性",
    "info_density_score": "信息密度",
    "tool_loop_score": "工具闭环",
    "correction_value_score": "纠偏价值",
    "verification_loop_score": "验证闭环",
    "compliance_score": "合规可用性",
}


# ---------- 主流程 ----------

def review_single_record(api, rec, prompt_content, schema_content):
    """对单条记录执行完整 AI 评审 + 回填。"""
    review_rid = rec["record_id"]
    fields = rec["fields"]
    name = normalize_field_value(fields.get("专家姓名", ""))
    pre_screen_status = normalize_field_value(fields.get("粗筛状态", ""))

    # 1. 下载 Trace
    trace_field = fields.get("Trace文件")
    trace_content = ""
    trace_tmp = None

    if isinstance(trace_field, list) and trace_field:
        first = trace_field[0]
        if isinstance(first, dict) and first.get("file_token"):
            trace_tmp = os.path.join(tempfile.gettempdir(), f"review_trace_{review_rid}.jsonl")
            try:
                dl_url = first.get("url", "")
                api.download_file(first["file_token"], trace_tmp, download_url=dl_url)
                # 提取用户消息 + 工具摘要（不含 AI 大段回复）
                trace_content = extract_user_focused_content(trace_tmp, max_bytes=200000)
                print(f"  Trace 提取成功, 精简后长度: {len(trace_content)} 字符")
            except Exception as e:
                print(f"  Trace 下载失败: {e}")
            finally:
                if trace_tmp and os.path.exists(trace_tmp):
                    os.unlink(trace_tmp)

    if not trace_content:
        print(f"  无 Trace 内容，跳过 AI 评审")
        return None

    # 2. 组装输入
    input_text = build_input_text(fields, trace_content)
    print(f"  输入文本长度: {len(input_text)} 字符")

    # 3. 调用 Claude API
    print(f"  调用 Claude API ({MODEL})...")
    t0 = time.time()
    try:
        ai_result = call_claude_review(prompt_content, schema_content, input_text)
    except Exception as e:
        print(f"  AI 评审调用失败: {e}")
        return None
    elapsed = time.time() - t0
    print(f"  AI 评审完成, 耗时: {elapsed:.1f}s")

    # 4. 提取分数
    expert_scores = extract_scores(ai_result, "expert_ability", EXPERT_DIMS)
    trace_scores = extract_scores(ai_result, "trace_asset", TRACE_DIMS)

    print(f"  专家能力: {expert_scores['total']}/10 (复杂度={expert_scores['task_complexity']}, "
          f"迭代={expert_scores['iteration_quality']}, 判断={expert_scores['professional_judgment']})")
    print(f"  Trace资产: {trace_scores['total']}/12 (真实={trace_scores['authenticity']}, "
          f"密度={trace_scores['info_density']}, 工具={trace_scores['tool_loop']}, "
          f"纠偏={trace_scores['correction_value']}, 验证={trace_scores['verification_loop']}, "
          f"合规={trace_scores['compliance']})")

    # 5. 判定结论
    conclusion = determine_conclusion(expert_scores["total"], trace_scores["total"], pre_screen_status)
    print(f"  最终结论: {conclusion}")

    # 6. 确定 AI 评审状态
    if conclusion == "拒绝":
        ai_status = "拒绝"
    elif "待人工复核" in conclusion:
        ai_status = "待人工复核"
    else:
        ai_status = "通过"

    # 7. 回填飞书
    update_fields = {
        FIELD_MAP["ai_review_status"]: ai_status,
        FIELD_MAP["ai_review_result"]: json.dumps(ai_result, ensure_ascii=False, indent=2),
        FIELD_MAP["final_conclusion"]: conclusion,
        FIELD_MAP["expert_ability_total"]: expert_scores["total"],
        FIELD_MAP["trace_asset_total"]: trace_scores["total"],
        FIELD_MAP["task_complexity_score"]: expert_scores["task_complexity"],
        FIELD_MAP["iteration_quality_score"]: expert_scores["iteration_quality"],
        FIELD_MAP["professional_judgment_score"]: expert_scores["professional_judgment"],
        FIELD_MAP["authenticity_score"]: trace_scores["authenticity"],
        FIELD_MAP["info_density_score"]: trace_scores["info_density"],
        FIELD_MAP["tool_loop_score"]: trace_scores["tool_loop"],
        FIELD_MAP["correction_value_score"]: trace_scores["correction_value"],
        FIELD_MAP["verification_loop_score"]: trace_scores["verification_loop"],
        FIELD_MAP["compliance_score"]: trace_scores["compliance"],
    }

    try:
        api.update_record(REVIEW_APP_TOKEN, REVIEW_TABLE_ID, review_rid, update_fields)
        print(f"  飞书回填成功")
    except Exception as e:
        print(f"  飞书回填失败: {e}")

    return {
        "record_id": review_rid,
        "name": name,
        "expert_total": expert_scores["total"],
        "trace_total": trace_scores["total"],
        "conclusion": conclusion,
    }


def main():
    if not APP_ID or not APP_SECRET:
        print("错误: 请设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET", file=sys.stderr)
        sys.exit(1)
    if not OPENROUTER_API_KEY:
        print("错误: 请设置 OPENROUTER_API_KEY", file=sys.stderr)
        sys.exit(1)

    # 读取 prompt 和 schema
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        prompt_content = f.read()
    with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
        schema_content = f.read()

    api = FeishuAPI()

    print("=" * 70)
    print("AI 评审流程")
    print(f"模型: {MODEL}")
    print("=" * 70)

    # 读取评审表
    print("\n--- 读取评审表 ---")
    review_records = api.get_all_records(REVIEW_APP_TOKEN, REVIEW_TABLE_ID)
    print(f"评审表共 {len(review_records)} 条记录")

    # 筛选需要 AI 评审的记录（粗筛通过 或 待人工复核）
    to_review = []
    for rec in review_records:
        fields = rec["fields"]
        status = normalize_field_value(fields.get("粗筛状态", ""))
        if status in ("通过", "待人工复核"):
            to_review.append(rec)

    print(f"需要 AI 评审的记录: {len(to_review)} 条")
    print()

    # 对被拒绝的记录也回填最终结论
    for rec in review_records:
        fields = rec["fields"]
        status = normalize_field_value(fields.get("粗筛状态", ""))
        if status == "拒绝":
            rid = rec["record_id"]
            name = normalize_field_value(fields.get("专家姓名", ""))
            try:
                api.update_record(REVIEW_APP_TOKEN, REVIEW_TABLE_ID, rid, {
                    FIELD_MAP["final_conclusion"]: "拒绝",
                    FIELD_MAP["ai_review_status"]: "跳过（粗筛拒绝）",
                })
                print(f"  [拒绝] {name} ({rid}) — 已回填最终结论=拒绝")
            except Exception as e:
                print(f"  [拒绝] {name} ({rid}) — 回填失败: {e}")

    # 逐条执行 AI 评审
    results = []
    for i, rec in enumerate(to_review):
        fields = rec["fields"]
        name = normalize_field_value(fields.get("专家姓名", ""))
        rid = rec["record_id"]
        status = normalize_field_value(fields.get("粗筛状态", ""))

        print(f"\n--- [{i+1}/{len(to_review)}] {name} ({rid}) | 粗筛: {status} ---")

        result = review_single_record(api, rec, prompt_content, schema_content)
        if result:
            results.append(result)

    # 汇总
    print(f"\n{'=' * 70}")
    print("AI 评审汇总")
    print("=" * 70)
    print(f"{'姓名':10s} {'专家能力':8s} {'Trace资产':8s} {'最终结论'}")
    print("-" * 50)
    for r in results:
        print(f"{r['name']:10s} {r['expert_total']:>5d}/10  {r['trace_total']:>5d}/12  {r['conclusion']}")

    print(f"\n共评审 {len(results)} 条记录")
    print("=" * 70)


if __name__ == "__main__":
    main()
