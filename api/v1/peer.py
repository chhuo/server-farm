"""
Peer 通信 API

提供：
- 节点握手（handshake）— 返回公开信息
- 加入网络申请（join-request）— 新节点申请加入
- 加入状态查询（join-status）— 轮询审批状态
- Full ↔ Full 同步（sync）— 签名验证
- Relay → Full 心跳（heartbeat）— 签名验证

节点间通信使用 secp256k1 签名进行身份验证。
"""

import hashlib
import time

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from core.logger import get_logger
from core.node import NodeIdentity
from models.node import TrustStatus

router = APIRouter(prefix="/peer", tags=["peer"])
_logger = get_logger("api.peer")


def _verify_node_signature(request: Request, data: dict, body: bytes) -> tuple[bool, str]:
    """
    验证请求的节点签名。

    验证流程：
    1. 从请求头中提取签名信息
    2. 查找发送方节点的公钥
    3. 验证签名有效性
    4. 检查节点信任状态

    Returns:
        (is_valid, error_message)
    """
    node_identity: NodeIdentity = request.app.state.node_identity
    storage = request.app.state.storage

    # 从 Header 或 body 中获取签名信息
    remote_node_id = (
        request.headers.get("x-node-id", "")
        or data.get("node_id", "")
    )
    timestamp = request.headers.get("x-node-ts", "")
    body_hash = request.headers.get("x-body-hash", "")
    signature = request.headers.get("x-node-sig", "")

    if not remote_node_id:
        return False, "缺少节点 ID"

    if not all([timestamp, body_hash, signature]):
        return False, "缺少签名信息（X-Node-Ts, X-Body-Hash, X-Node-Sig）"

    # 验证 body hash
    actual_hash = hashlib.sha256(body).hexdigest()
    if body_hash != actual_hash:
        return False, "请求体哈希不匹配"

    # 查找发送方的公钥和信任状态
    nodes = storage.read("nodes.json", {})
    remote_node = nodes.get(remote_node_id)

    if not remote_node:
        return False, f"未知节点: {remote_node_id}"

    trust_status = remote_node.get("trust_status", "")
    if trust_status == TrustStatus.KICKED.value:
        return False, f"节点已被踢出: {remote_node_id}"
    if trust_status not in (TrustStatus.TRUSTED.value, TrustStatus.SELF.value):
        return False, f"节点未受信任: {remote_node_id} (status={trust_status})"

    public_key_hex = remote_node.get("public_key", "")
    if not public_key_hex:
        return False, f"节点无公钥: {remote_node_id}"

    # 验证签名
    valid = NodeIdentity.verify_signature(
        node_id=remote_node_id,
        timestamp=timestamp,
        body_hash=body_hash,
        signature_b64=signature,
        public_key_hex=public_key_hex,
    )

    if not valid:
        return False, f"签名验证失败: {remote_node_id}"

    return True, ""


# ──────────────────────────────────────────
# 公开端点（免认证）
# ──────────────────────────────────────────

@router.get("/handshake")
async def handshake(request: Request):
    """
    返回本节点的公开信息（免认证）。
    
    用于新节点在发起加入申请前获取目标节点的基本信息。
    """
    node_identity: NodeIdentity = request.app.state.node_identity
    return node_identity.get_handshake_info()


@router.post("/join-request")
async def join_request(request: Request):
    """
    接收新节点的加入申请（免认证）。

    新节点 C 向网络中的节点 B 发送加入请求，
    B 将 C 保存为 pending 状态，等待管理员审批。
    """
    storage = request.app.state.storage
    node_identity: NodeIdentity = request.app.state.node_identity
    data = await request.json()

    remote_node_id = data.get("node_id", "")
    remote_public_key = data.get("public_key", "")

    if not remote_node_id or not remote_public_key:
        return JSONResponse(
            status_code=400,
            content={"error": "缺少 node_id 或 public_key"}
        )

    # 检查是否已存在该节点
    nodes = storage.read("nodes.json", {})
    existing = nodes.get(remote_node_id)

    if existing:
        existing_status = existing.get("trust_status", "")
        if existing_status == TrustStatus.KICKED.value:
            return JSONResponse(
                status_code=403,
                content={
                    "status": "kicked",
                    "message": "该节点已被踢出网络",
                }
            )
        if existing_status == TrustStatus.TRUSTED.value:
            # 已经是信任节点，返回网络信息
            trusted_nodes = {
                nid: info for nid, info in nodes.items()
                if info.get("trust_status") in (
                    TrustStatus.TRUSTED.value,
                    TrustStatus.SELF.value,
                )
            }
            return {
                "status": "trusted",
                "message": "节点已在网络中",
                "nodes": trusted_nodes,
            }
        if existing_status == TrustStatus.PENDING.value:
            return {
                "status": "pending",
                "message": "加入申请已提交，等待审批",
            }

    # 保存为 pending
    node_entry = {
        "node_id": remote_node_id,
        "name": data.get("name", remote_node_id),
        "mode": data.get("mode", "full"),
        "connectable": data.get("connectable", False),
        "host": data.get("host", ""),
        "port": data.get("port", 8300),
        "public_url": data.get("public_url", ""),
        "registered_at": time.time(),
        "public_key": remote_public_key,
        "trust_status": TrustStatus.PENDING.value,
    }

    def updater(nodes):
        nodes[remote_node_id] = node_entry
        return nodes

    storage.update("nodes.json", updater, default={})

    _logger.info(
        f"收到加入申请: {remote_node_id} ({data.get('name', '?')}), "
        f"公钥指纹: {hashlib.sha256(remote_public_key.encode()).hexdigest()[:16]}"
    )

    return {
        "status": "pending",
        "message": "加入申请已提交，等待管理员审批",
    }


@router.get("/join-status")
async def join_status(request: Request):
    """
    查询加入申请的审批状态（免认证，但验证 node_id + 公钥）。

    新节点 C 定期轮询 B，检查自己的申请是否被批准。
    批准后返回网络中所有 trusted 节点的信息。
    """
    storage = request.app.state.storage

    node_id = request.query_params.get("node_id", "")
    public_key = request.query_params.get("public_key", "")

    if not node_id:
        return JSONResponse(
            status_code=400,
            content={"error": "缺少 node_id 参数"}
        )

    nodes = storage.read("nodes.json", {})
    node_info = nodes.get(node_id)

    if not node_info:
        return {
            "status": "unknown",
            "message": "未找到该节点的申请记录",
        }

    # 验证公钥匹配（防止他人查询）
    if public_key and node_info.get("public_key") != public_key:
        return JSONResponse(
            status_code=403,
            content={"error": "公钥不匹配"}
        )

    trust_status = node_info.get("trust_status", "")

    if trust_status == TrustStatus.TRUSTED.value:
        # 已批准 — 返回所有信任节点信息
        trusted_nodes = {
            nid: info for nid, info in nodes.items()
            if info.get("trust_status") in (
                TrustStatus.TRUSTED.value,
                TrustStatus.SELF.value,
            )
        }
        return {
            "status": "trusted",
            "message": "已批准加入网络",
            "nodes": trusted_nodes,
        }
    elif trust_status == TrustStatus.KICKED.value:
        return {
            "status": "kicked",
            "message": "该节点已被踢出网络",
        }
    else:
        return {
            "status": trust_status,
            "message": "等待管理员审批",
        }


# ──────────────────────────────────────────
# 需要签名认证的端点
# ──────────────────────────────────────────

@router.post("/trigger-sync")
async def trigger_sync(request: Request):
    """
    手动触发一次立即同步/心跳。
    用于前端"主动心跳"按钮，快速同步消息和数据。
    """
    peer_service = request.app.state.peer_service
    _logger.info("收到手动触发同步请求")
    result = await peer_service.trigger_sync_now()
    return result


@router.post("/chat-push")
async def chat_push(request: Request):
    """
    接收远端节点推送的聊天消息（实时推送端点）。
    
    收到消息后：
    1. 验证签名
    2. 去重保存到本地
    3. 广播给本地 WebSocket 连接
    """
    from api.v1.chat import chat_hub, CHAT_FILE

    body = await request.body()
    import json
    data = json.loads(body)

    # 验证签名
    valid, error = _verify_node_signature(request, data, body)
    if not valid:
        _logger.warning(f"聊天推送签名验证失败: {error}")
        return JSONResponse(status_code=403, content={"error": f"签名验证失败: {error}"})

    msg = data.get("message")
    if not msg or not msg.get("id"):
        return {"ok": False, "error": "无效消息"}

    storage = request.app.state.storage

    # 去重保存
    def updater(messages):
        if not isinstance(messages, list):
            messages = []
        # 检查是否已存在
        existing_ids = {m.get("id") for m in messages}
        if msg["id"] not in existing_ids:
            messages.append(msg)
            # 限制最大消息数
            if len(messages) > 500:
                messages = messages[-500:]
        return messages

    storage.update(CHAT_FILE, updater, default=[])

    # 广播给本地 WebSocket 连接
    await chat_hub.broadcast(msg)

    _logger.debug(f"收到聊天推送: {data.get('node_id', '?')} msg_id={msg.get('id', '?')[:8]}")
    return {"ok": True}


@router.post("/sync")
async def peer_sync(request: Request):
    """
    Full ↔ Full Gossip 同步端点。
    接收远端 Full 节点的数据，合并后返回本地数据。
    需要签名验证。
    """
    peer_service = request.app.state.peer_service
    body = await request.body()
    import json
    data = json.loads(body)

    # 验证签名
    valid, error = _verify_node_signature(request, data, body)
    if not valid:
        _logger.warning(f"Gossip 同步签名验证失败: {error}")
        return JSONResponse(status_code=403, content={"error": f"签名验证失败: {error}"})

    _logger.debug(f"收到 Gossip 同步请求: node={data.get('node_id', '?')}")
    result = peer_service.handle_sync(data)
    return result


@router.post("/heartbeat")
async def peer_heartbeat(request: Request):
    """
    Relay → Full 心跳端点。
    接收 Relay 节点的状态上报，返回全局数据。
    需要签名验证。
    """
    peer_service = request.app.state.peer_service
    body = await request.body()
    import json
    data = json.loads(body)

    # 验证签名
    valid, error = _verify_node_signature(request, data, body)
    if not valid:
        _logger.warning(f"心跳签名验证失败: {error}")
        return JSONResponse(status_code=403, content={"error": f"签名验证失败: {error}"})

    _logger.debug(f"收到 Relay 心跳: node={data.get('node_id', '?')}")
    result = peer_service.handle_heartbeat(data)
    return result
