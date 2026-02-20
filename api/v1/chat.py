"""
聊天 API

提供跨设备实时聊天功能：
- REST: 获取历史消息、发送消息
- WebSocket: 实时消息推送
"""

import time
import uuid
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from core.logger import get_logger

_logger = get_logger("api.chat")

router = APIRouter(prefix="/chat", tags=["chat"])

CHAT_FILE = "chat.json"

# WebSocket 连接管理
_ws_connections: list[WebSocket] = []


@router.get("/messages")
async def get_messages(request: Request, limit: int = 100, before: float = 0):
    """获取聊天历史消息"""
    storage = request.app.state.storage
    messages = storage.read(CHAT_FILE, [])

    if before > 0:
        messages = [m for m in messages if m.get("timestamp", 0) < before]

    # 返回最近的 limit 条
    messages = messages[-limit:] if len(messages) > limit else messages

    return {"messages": messages, "total": len(messages)}


@router.post("/messages")
async def send_message(request: Request):
    """发送聊天消息"""
    storage = request.app.state.storage
    node_identity = request.app.state.node_identity

    body = await request.json()
    content = body.get("content", "").strip()

    if not content:
        return {"error": "消息内容不能为空"}

    if len(content) > 2000:
        return {"error": "消息内容不能超过 2000 字符"}

    msg = {
        "id": str(uuid.uuid4()),
        "node_id": node_identity.node_id,
        "node_name": node_identity.name,
        "content": content,
        "timestamp": time.time(),
    }

    def updater(messages):
        if not isinstance(messages, list):
            messages = []
        messages.append(msg)
        # 限制最大消息数
        if len(messages) > 500:
            messages = messages[-500:]
        return messages

    storage.update(CHAT_FILE, updater, default=[])

    # 广播给所有 WebSocket 连接
    await _broadcast(msg)

    return {"ok": True, "message": msg}


@router.websocket("/ws")
async def chat_websocket(websocket: WebSocket):
    """
    聊天 WebSocket 端点。

    连接时验证 cookie 中的 token。
    """
    # 验证认证
    auth_service = websocket.app.state.auth_service
    token = websocket.cookies.get("token", "")
    session = auth_service.validate_token(token)

    if not session:
        await websocket.close(code=4001, reason="未登录或会话已过期")
        return

    await websocket.accept()
    _ws_connections.append(websocket)

    node_identity = websocket.app.state.node_identity
    storage = websocket.app.state.storage

    _logger.info(f"WebSocket 聊天连接已建立 (节点: {node_identity.node_id})")

    try:
        while True:
            data = await websocket.receive_json()

            if data.get("type") == "message":
                content = data.get("content", "").strip()
                if not content or len(content) > 2000:
                    continue

                msg = {
                    "id": str(uuid.uuid4()),
                    "node_id": node_identity.node_id,
                    "node_name": node_identity.name,
                    "content": content,
                    "timestamp": time.time(),
                }

                def updater(messages):
                    if not isinstance(messages, list):
                        messages = []
                    messages.append(msg)
                    if len(messages) > 500:
                        messages = messages[-500:]
                    return messages

                storage.update(CHAT_FILE, updater, default=[])

                # 广播给所有连接
                await _broadcast(msg)

            elif data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        _logger.info("WebSocket 聊天连接已断开")
    except Exception as e:
        _logger.error(f"WebSocket 聊天异常: {e}")
    finally:
        if websocket in _ws_connections:
            _ws_connections.remove(websocket)


async def _broadcast(msg: dict):
    """广播消息给所有 WebSocket 连接"""
    disconnected = []
    for ws in _ws_connections:
        try:
            await ws.send_json({"type": "message", "data": msg})
        except Exception:
            disconnected.append(ws)

    for ws in disconnected:
        if ws in _ws_connections:
            _ws_connections.remove(ws)
