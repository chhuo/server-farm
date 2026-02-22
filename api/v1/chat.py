"""
聊天 API — Telegram 风格实时聊天

核心改进：
- ChatHub：全局 WebSocket 连接管理 + 广播中心
- 发送消息时立即推送给所有 Peer 节点（fire-and-forget）
- Peer 同步合并后自动通知本地 WebSocket
- 乐观更新 + 消息状态（sending → sent → delivered）
"""

import asyncio
import time
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from core.logger import get_logger

_logger = get_logger("api.chat")

router = APIRouter(prefix="/chat", tags=["chat"])

CHAT_FILE = "chat.json"


# ══════════════════════════════════════════
# ChatHub — 全局广播中心
# ══════════════════════════════════════════

class ChatHub:
    """
    聊天消息广播中心。
    
    - 管理所有本地 WebSocket 连接
    - 提供广播接口，供 PeerService 同步后调用
    - 发送消息时异步推送给远端 Peer 节点
    """

    def __init__(self):
        self._connections: list[WebSocket] = []
        self._app = None  # 延迟绑定

    def bind_app(self, app):
        """绑定 FastAPI app（用于访问 peer_service 等）"""
        self._app = app

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    def add(self, ws: WebSocket):
        self._connections.append(ws)

    def remove(self, ws: WebSocket):
        if ws in self._connections:
            self._connections.remove(ws)

    async def broadcast(self, msg: dict):
        """广播消息给所有本地 WebSocket 连接"""
        if not self._connections:
            return

        payload = {"type": "message", "data": msg}
        disconnected = []

        for ws in self._connections:
            try:
                await ws.send_json(payload)
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            self.remove(ws)

    async def broadcast_messages(self, messages: list[dict]):
        """批量广播多条消息（用于 sync 后通知）"""
        if not self._connections or not messages:
            return

        payload = {"type": "messages_batch", "data": messages}
        disconnected = []

        for ws in self._connections:
            try:
                await ws.send_json(payload)
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            self.remove(ws)

    async def push_to_peers(self, msg: dict):
        """
        Fire-and-forget：将消息异步推送给所有可连接的信任 Peer 节点。
        失败不阻塞，常规 sync 会兜底。
        """
        if not self._app:
            return

        try:
            peer_service = self._app.state.peer_service
            node_identity = self._app.state.node_identity

            peers = peer_service._discover_trusted_connectable_peers()
            if not peers:
                return

            payload = {
                "node_id": node_identity.node_id,
                "message": msg,
            }

            body, headers = peer_service._make_signed_request_args(payload)
            timeout = peer_service._config.get("peer.timeout", 10)

            async def _push_one(peer):
                peer_url = peer_service._get_peer_url(peer)
                try:
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        resp = await client.post(
                            f"{peer_url}/api/v1/peer/chat-push",
                            content=body,
                            headers=headers,
                        )
                        if resp.status_code == 200:
                            _logger.debug(f"消息推送成功: {peer.get('node_id', '?')}")
                        else:
                            _logger.debug(f"消息推送失败: {peer.get('node_id', '?')} status={resp.status_code}")
                except Exception as e:
                    _logger.debug(f"消息推送异常: {peer.get('node_id', '?')}: {e}")

            # 并发推送给所有 peer
            tasks = [_push_one(p) for p in peers]
            await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            _logger.debug(f"push_to_peers 异常: {e}")


# 全局单例
chat_hub = ChatHub()


# ══════════════════════════════════════════
# REST 端点
# ══════════════════════════════════════════

@router.get("/messages")
async def get_messages(request: Request, limit: int = 100, after: float = 0):
    """
    获取聊天历史消息。
    
    参数:
        limit: 最多返回条数
        after: 仅返回 timestamp > after 的消息（增量拉取）
    """
    storage = request.app.state.storage
    messages = storage.read(CHAT_FILE, [])

    if after > 0:
        messages = [m for m in messages if m.get("timestamp", 0) > after]

    # 返回最近的 limit 条
    messages = messages[-limit:] if len(messages) > limit else messages

    return {"messages": messages, "total": len(messages)}


@router.post("/messages")
async def send_message(request: Request):
    """发送聊天消息（REST 方式）"""
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
        "status": "sent",
    }

    _save_message(storage, msg)

    # 广播给本地 WebSocket 连接
    await chat_hub.broadcast(msg)

    # 异步推送给所有 Peer 节点
    asyncio.create_task(chat_hub.push_to_peers(msg))

    return {"ok": True, "message": msg}


# ══════════════════════════════════════════
# WebSocket 端点
# ══════════════════════════════════════════

@router.websocket("/ws")
async def chat_websocket(websocket: WebSocket):
    """
    聊天 WebSocket 端点。
    
    连接时验证 cookie 中的 token。
    支持消息类型:
    - message: 发送聊天消息
    - ping: 心跳保活
    """
    # 验证认证
    auth_service = websocket.app.state.auth_service
    token = websocket.cookies.get("token", "")
    session = auth_service.validate_token(token)

    if not session:
        await websocket.close(code=4001, reason="未登录或会话已过期")
        return

    await websocket.accept()
    chat_hub.add(websocket)

    node_identity = websocket.app.state.node_identity
    storage = websocket.app.state.storage

    _logger.info(f"WebSocket 聊天连接已建立 (节点: {node_identity.node_id}), 当前连接数: {chat_hub.connection_count}")

    try:
        while True:
            data = await websocket.receive_json()

            if data.get("type") == "message":
                content = data.get("content", "").strip()
                if not content or len(content) > 2000:
                    continue

                # client_id 用于乐观更新去重
                client_id = data.get("client_id", "")

                msg = {
                    "id": str(uuid.uuid4()),
                    "client_id": client_id,
                    "node_id": node_identity.node_id,
                    "node_name": node_identity.name,
                    "content": content,
                    "timestamp": time.time(),
                    "status": "sent",
                }

                _save_message(storage, msg)

                # 广播给所有本地连接（包括发送者，用于确认）
                await chat_hub.broadcast(msg)

                # 异步推送给远端 Peer
                asyncio.create_task(chat_hub.push_to_peers(msg))

            elif data.get("type") == "ping":
                await websocket.send_json({"type": "pong", "ts": time.time()})

    except WebSocketDisconnect:
        _logger.info("WebSocket 聊天连接已断开")
    except Exception as e:
        _logger.error(f"WebSocket 聊天异常: {e}")
    finally:
        chat_hub.remove(websocket)
        _logger.debug(f"当前聊天连接数: {chat_hub.connection_count}")


# ══════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════

def _save_message(storage, msg: dict):
    """保存消息到存储"""
    def updater(messages):
        if not isinstance(messages, list):
            messages = []
        messages.append(msg)
        # 限制最大消息数
        if len(messages) > 500:
            messages = messages[-500:]
        return messages

    storage.update(CHAT_FILE, updater, default=[])
