"""
信息片段 API（复制中心）

提供常用信息的存储和管理：
- 账号密码（Google 账号等）
- 服务器登录凭据
- 常用命令
- 碎片化笔记

支持敏感字段遮罩、一键复制、隐藏/显示控制。
"""

import time
import uuid

from fastapi import APIRouter, Request

from core.logger import get_logger

_logger = get_logger("api.snippets")

router = APIRouter(prefix="/snippets", tags=["snippets"])

SNIPPETS_FILE = "snippets.json"


@router.get("/")
async def list_snippets(request: Request, category: str = ""):
    """
    获取所有信息片段。

    可选按分类过滤：account / server / command / note
    """
    storage = request.app.state.storage
    snippets = storage.read(SNIPPETS_FILE, [])

    # 过滤已删除的
    snippets = [s for s in snippets if not s.get("_deleted", False)]

    if category:
        snippets = [s for s in snippets if s.get("category") == category]

    # 按创建时间倒序
    snippets.sort(key=lambda s: s.get("created_at", 0), reverse=True)

    return {"snippets": snippets, "total": len(snippets)}


@router.post("/")
async def create_snippet(request: Request):
    """
    创建信息片段。

    请求体示例：
    {
        "category": "account",
        "title": "Google 账号 #1",
        "fields": [
            {"key": "账号", "value": "user@gmail.com", "sensitive": false},
            {"key": "密码", "value": "xxxxx", "sensitive": true}
        ],
        "hidden": false
    }
    """
    storage = request.app.state.storage
    body = await request.json()

    title = body.get("title", "").strip()
    if not title:
        return {"error": "标题不能为空"}

    category = body.get("category", "note")
    if category not in ("account", "server", "command", "note"):
        category = "note"

    fields = body.get("fields", [])
    if not isinstance(fields, list):
        fields = []

    # 验证字段格式
    validated_fields = []
    for f in fields:
        if isinstance(f, dict) and f.get("key"):
            validated_fields.append({
                "key": str(f["key"]).strip(),
                "value": str(f.get("value", "")),
                "sensitive": bool(f.get("sensitive", False)),
            })

    now = time.time()
    snippet = {
        "id": str(uuid.uuid4()),
        "category": category,
        "title": title,
        "fields": validated_fields,
        "hidden": bool(body.get("hidden", False)),
        "created_at": now,
        "updated_at": now,
    }

    def updater(snippets):
        if not isinstance(snippets, list):
            snippets = []
        snippets.append(snippet)
        return snippets

    storage.update(SNIPPETS_FILE, updater, default=[])

    _logger.info(f"创建信息片段: {title} [{category}]")
    return {"ok": True, "snippet": snippet}


@router.put("/{snippet_id}")
async def update_snippet(snippet_id: str, request: Request):
    """更新信息片段"""
    storage = request.app.state.storage
    body = await request.json()

    found = False
    updated_snippet = None

    def updater(snippets):
        nonlocal found, updated_snippet
        if not isinstance(snippets, list):
            snippets = []

        for i, s in enumerate(snippets):
            if s.get("id") == snippet_id and not s.get("_deleted", False):
                # 更新字段
                if "title" in body:
                    title = body["title"].strip()
                    if not title:
                        return snippets
                    s["title"] = title

                if "category" in body:
                    cat = body["category"]
                    if cat in ("account", "server", "command", "note"):
                        s["category"] = cat

                if "fields" in body:
                    fields = body["fields"]
                    if isinstance(fields, list):
                        validated = []
                        for f in fields:
                            if isinstance(f, dict) and f.get("key"):
                                validated.append({
                                    "key": str(f["key"]).strip(),
                                    "value": str(f.get("value", "")),
                                    "sensitive": bool(f.get("sensitive", False)),
                                })
                        s["fields"] = validated

                if "hidden" in body:
                    s["hidden"] = bool(body["hidden"])

                s["updated_at"] = time.time()
                snippets[i] = s
                found = True
                updated_snippet = s
                break

        return snippets

    storage.update(SNIPPETS_FILE, updater, default=[])

    if not found:
        return {"error": "片段不存在"}

    _logger.info(f"更新信息片段: {snippet_id}")
    return {"ok": True, "snippet": updated_snippet}


@router.delete("/{snippet_id}")
async def delete_snippet(snippet_id: str, request: Request):
    """删除信息片段（软删除，支持跨节点同步）"""
    storage = request.app.state.storage

    found = False

    def updater(snippets):
        nonlocal found
        if not isinstance(snippets, list):
            snippets = []

        for i, s in enumerate(snippets):
            if s.get("id") == snippet_id:
                s["_deleted"] = True
                s["updated_at"] = time.time()
                snippets[i] = s
                found = True
                break

        return snippets

    storage.update(SNIPPETS_FILE, updater, default=[])

    if not found:
        return {"error": "片段不存在"}

    _logger.info(f"删除信息片段: {snippet_id}")
    return {"ok": True}
