"""
Peer 通信 API

提供 Full ↔ Full 同步 和 Relay → Full 心跳的接收端点。
"""

from fastapi import APIRouter, Request

from core.logger import get_logger

router = APIRouter(prefix="/peer", tags=["peer"])
_logger = get_logger("api.peer")


@router.post("/sync")
async def peer_sync(request: Request):
    """
    Full ↔ Full Gossip 同步端点。
    接收远端 Full 节点的数据，合并后返回本地数据。
    """
    peer_service = request.app.state.peer_service
    data = await request.json()

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

    _logger.debug(f"收到 Relay 心跳: node={data.get('node_id', '?')}")
    result = peer_service.handle_heartbeat(data)
    return result
