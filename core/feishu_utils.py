#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
飞书多维表格 API 工具

FeishuClient 类：支持双表操作（主表 + 评审表）。
无状态工具函数：normalize_field_value / extract_attachment_file_token / extract_link_url。
"""

import json
import os

import requests


# ========== FeishuClient 类 ==========

class FeishuClient:
    """飞书多维表格客户端，封装 token 获取和记录读写，支持双表操作。"""

    def __init__(self, app_id: str, app_secret: str, app_token: str, table_id: str,
                 main_app_token: str = "", main_table_id: str = ""):
        self.app_id = app_id
        self.app_secret = app_secret
        # 评审表（留痕）
        self.app_token = app_token
        self.table_id = table_id
        # 主表（数据源 + 回填）
        self.main_app_token = main_app_token or app_token
        self.main_table_id = main_table_id or table_id
        self._token = ""

    @classmethod
    def from_config(cls, config: dict) -> "FeishuClient":
        """从项目配置（config.yaml 已合并环境变量）构造客户端。"""
        feishu = config.get("feishu", {})
        return cls(
            app_id=feishu["app_id"],
            app_secret=feishu["app_secret"],
            app_token=feishu.get("app_token") or feishu.get("review_app_token", ""),
            table_id=feishu.get("table_id") or feishu.get("review_table_id", ""),
            main_app_token=feishu.get("main_app_token", ""),
            main_table_id=feishu.get("main_table_id", ""),
        )

    def get_token(self) -> str:
        """获取飞书 tenant_access_token（带缓存）。"""
        if self._token:
            return self._token
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        resp = requests.post(
            url,
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取飞书 tenant_access_token 失败: {data}")
        self._token = data["tenant_access_token"]
        return self._token

    # ========== 通用记录操作 ==========

    def _get_record(self, app_token: str, table_id: str, record_id: str) -> dict:
        """获取多维表格单条记录（通用）。"""
        token = self.get_token()
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}"
            f"/tables/{table_id}/records/{record_id}"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取飞书记录失败: {data}")
        return data.get("data", {}).get("record", {})

    def _update_record(self, app_token: str, table_id: str,
                       record_id: str, fields: dict) -> dict:
        """更新多维表格指定记录的字段（通用）。"""
        token = self.get_token()
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}"
            f"/tables/{table_id}/records/{record_id}"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {"fields": fields}
        resp = requests.put(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"更新飞书记录失败: {data}")
        return data

    def _create_record(self, app_token: str, table_id: str, fields: dict) -> dict:
        """在多维表格中创建一条记录（通用）。"""
        token = self.get_token()
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}"
            f"/tables/{table_id}/records"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {"fields": fields}
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"创建飞书记录失败: {data}")
        return data.get("data", {}).get("record", {})

    # ========== 评审表操作（留痕，向后兼容） ==========

    def get_record(self, record_id: str) -> dict:
        """获取评审表单条记录（向后兼容）。"""
        return self._get_record(self.app_token, self.table_id, record_id)

    def update_record(self, record_id: str, fields: dict) -> dict:
        """更新评审表指定记录的字段（向后兼容）。"""
        return self._update_record(self.app_token, self.table_id, record_id, fields)

    def create_review_record(self, fields: dict) -> dict:
        """在评审表创建一条留痕记录，返回新记录。"""
        return self._create_record(self.app_token, self.table_id, fields)

    def update_review_record(self, record_id: str, fields: dict) -> dict:
        """更新评审表记录。"""
        return self._update_record(self.app_token, self.table_id, record_id, fields)

    # ========== 主表操作（数据源 + 回填） ==========

    def get_main_record(self, record_id: str) -> dict:
        """获取主表单条记录。"""
        return self._get_record(self.main_app_token, self.main_table_id, record_id)

    def update_main_record(self, record_id: str, fields: dict) -> dict:
        """更新主表指定记录的字段（回填审核状态等）。"""
        return self._update_record(self.main_app_token, self.main_table_id, record_id, fields)

    # ========== 附件下载 ==========

    def download_attachment(self, file_token: str, output_path: str,
                            download_url: str = None) -> None:
        """
        下载飞书附件到本地文件。

        优先使用 download_url（bitable 附件自带的 url 字段），
        回退到 drive media 接口（需要额外权限）。
        """
        token = self.get_token()
        headers = {"Authorization": f"Bearer {token}"}

        if download_url:
            # bitable 附件的 url 字段直接下载
            resp = requests.get(download_url, headers=headers, timeout=120, stream=True)
        else:
            # 回退到 drive media 接口
            url = f"https://open.feishu.cn/open-apis/drive/v1/medias/{file_token}/download"
            resp = requests.get(url, headers=headers, timeout=120, stream=True)

        resp.raise_for_status()

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        print(f"附件已下载: {output_path} ({os.path.getsize(output_path)} 字节)")


# ========== 无状态工具函数 ==========

def normalize_field_value(value) -> str:
    """将飞书多维表格字段值标准化为字符串。"""
    if value is None:
        return ""

    if isinstance(value, (str, int, float, bool)):
        return str(value)

    if isinstance(value, dict):
        if "text" in value and value["text"] is not None:
            return str(value["text"])
        return json.dumps(value, ensure_ascii=False)

    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, (str, int, float, bool)):
                parts.append(str(item))
            elif isinstance(item, dict):
                if "text" in item and item["text"] is not None:
                    parts.append(str(item["text"]))
                elif "name" in item and item["name"] is not None:
                    parts.append(str(item["name"]))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return ", ".join([p for p in parts if p])

    return str(value)


def extract_attachment_file_token(field_value):
    """从飞书附件字段值中提取第一个 file_token。"""
    if isinstance(field_value, list) and field_value:
        first = field_value[0]
        if isinstance(first, dict):
            return first.get("file_token")
    return None


def extract_attachment_url(field_value) -> str:
    """从飞书附件字段值中提取第一个附件的 url（bitable 附件自带下载地址）。"""
    if isinstance(field_value, list) and field_value:
        first = field_value[0]
        if isinstance(first, dict):
            return first.get("url", "") or first.get("tmp_url", "")
    return ""


def extract_link_url(field_value) -> str:
    """从飞书超链接字段值中提取 URL。"""
    if isinstance(field_value, dict):
        return field_value.get("link", "") or field_value.get("text", "")
    if isinstance(field_value, str):
        return field_value
    return ""
