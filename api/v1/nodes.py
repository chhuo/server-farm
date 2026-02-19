"""
节点管理 API

提供节点列表查询、节点添加、状态查看等功能。
"""

from fastapi import APIRouter, Request

from core.logger import get_logger

router = APIRouter(prefix="/nodes", tags=["nodes"])
_logger = get_logger("api.nodes")


@router.get("")
async def list_nodes(request: Request):
    """获取所有已知节点列表（含实时状态）"""
    peer_service = request.app.state.peer_service
    node_identity = request.app.state.node_identity

    nodes = peer_service.get_all_nodes()
    states = peer_service.get_all_states()

    # 组装完整的节点信息
    result = []
    for node_id, info in nodes.items():
        state = states.get(node_id, {})
        result.append({
            **info,
            "status": state.get("status", "unknown"),
            "last_seen": state.get("last_seen", 0),
            "system_info": state.get("system_info", {}),
            "is_self": node_id == node_identity.node_id,
        })

    # 排序：自身优先，然后按 last_seen 倒序
    result.sort(key=lambda n: (
        not n.get("is_self", False),
        -n.get("last_seen", 0),
    ))

    return {"nodes": result, "total": len(result)}


@router.get("/self")
async def get_self_node(request: Request):
    """获取本机节点信息"""
    node_identity = request.app.state.node_identity
    peer_service = request.app.state.peer_service

    self_state = peer_service.get_node_state(node_identity.node_id)

    return {
        **node_identity.to_dict(),
        "status": "online",
        "system_info": self_state.get("system_info", {}) if self_state else {},
    }


@router.get("/{node_id}")
async def get_node(node_id: str, request: Request):
    """获取指定节点的详细信息"""
    peer_service = request.app.state.peer_service

    nodes = peer_service.get_all_nodes()
    states = peer_service.get_all_states()

    info = nodes.get(node_id)
    if not info:
        return {"error": "节点未找到", "node_id": node_id}

    state = states.get(node_id, {})
    return {
        **info,
        "status": state.get("status", "unknown"),
        "last_seen": state.get("last_seen", 0),
        "system_info": state.get("system_info", {}),
    }


@router.post("/add")
async def add_node(request: Request):
    """
    手动添加节点。

    向目标地址发送握手请求，成功后加入本地节点表。
    支持以下输入格式：
      - 纯 IP/域名: 192.168.1.100
      - 带端口:     192.168.1.100:9000
      - 完整 URL:   https://servers.example.com
      - 完整 URL:   http://servers.example.com:9000
    """
    peer_service = request.app.state.peer_service
    data = await request.json()

    raw_host = data.get("host", "").strip()
    port = data.get("port", 8300)

    if not raw_host:
        return {"error": "host 不能为空"}

    import httpx
    from urllib.parse import urlparse

    # 解析输入，支持完整 URL 或纯域名/IP
    if "://" in raw_host:
        parsed = urlparse(raw_host)
        scheme = parsed.scheme or "http"
        host = parsed.hostname or raw_host
        if parsed.port:
            port = parsed.port
    else:
        scheme = "http"
        # 处理 host:port 格式
        if ":" in raw_host and not raw_host.startswith("["):
            parts = raw_host.rsplit(":", 1)
            try:
                port = int(parts[1])
                host = parts[0]
            except ValueError:
                host = raw_host
        else:
            host = raw_host

    target_url = f"{scheme}://{host}:{port}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{target_url}/api/v1/nodes/self")
            resp.raise_for_status()
            remote_info = resp.json()

        # 将远端节点信息写入本地节点表
        node_id = remote_info.get("node_id", f"{host}:{port}")
        node_entry = {
            "node_id": node_id,
            "name": remote_info.get("name", host),
            "mode": remote_info.get("mode", "full"),
            "host": host,
            "port": port,
            "registered_at": remote_info.get("registered_at", __import__("time").time()),
        }

        from services.storage import FileStore
        storage: FileStore = request.app.state.storage

        def updater(nodes):
            nodes[node_id] = node_entry
            return nodes

        storage.update("nodes.json", updater, default={})

        _logger.info(f"手动添加节点成功: {node_id} ({host}:{port})")
        return {"success": True, "node": node_entry}

    except Exception as e:
        _logger.warning(f"添加节点失败 ({host}:{port}): {e}")
        return {"error": f"无法连接到 {target_url}: {str(e)}"}
