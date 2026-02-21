"""
Peer 通信 API

提供 Full ↔ Full 同步 和 Relay → Full 心跳的接收端点。
节点间通信通过 node_key 进行身份验证。
"""

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from core.logger import get_logger

router = APIRouter(prefix="/peer", tags=["peer"])
_logger = get_logger("api.peer")


def _verify_node_key(request: Request, data: dict) -> bool:
    """
    验证请求中的 node_key 是否与本节点配置一致。

    验证策略：
    - 如果本节点未配置 node_key（空字符串），则跳过验证（开放模式）
    - 如果配置了 node_key，则要求请求中的 node_key 必须匹配
    """
    local_key = request.app.state.node_identity.node_key
    if not local_key:
        return True  # 未配置密钥，开放模式
    remote_key = data.get("node_key", "")
    return remote_key == local_key


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


@router.post("/sync")
async def peer_sync(request: Request):
    """
    Full ↔ Full Gossip 同步端点。
    接收远端 Full 节点的数据，合并后返回本地数据。
    """
    peer_service = request.app.state.peer_service
    data = await request.json()

    # 验证节点密钥
    if not _verify_node_key(request, data):
        _logger.warning(f"Gossip 同步密钥验证失败: node={data.get('node_id', '?')}")
        return JSONResponse(status_code=403, content={"error": "节点密钥验证失败"})

    _logger.debug(f"收到 Gossip 同步请求: node={data.get('node_id', '?')}")
    result = peer_service.handle_sync(data)
    return result


@router.post("/heartbeat")
async def peer_heartbeat(request: Request):
    """
    Relay → Full 心跳端点。
    接收 Relay 节点的状态上报，返回全局数据。
    """
    peer_service = request.app.state.peer_service
    data = await request.json()

    # 验证节点密钥
    if not _verify_node_key(request, data):
        _logger.warning(f"心跳密钥验证失败: node={data.get('node_id', '?')}")
        return JSONResponse(status_code=403, content={"error": "节点密钥验证失败"})

    _logger.debug(f"收到 Relay 心跳: node={data.get('node_id', '?')}")
    result = peer_service.handle_heartbeat(data)
    return result
