"""
节点管理 API

提供：
- 节点列表查询
- 加入网络（向目标节点发送加入申请）
- 审批/拒绝/踢出节点
- 本机加入状态查询
"""

import hashlib
import time

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from core.logger import get_logger
from models.node import TrustStatus

router = APIRouter(prefix="/nodes", tags=["nodes"])
_logger = get_logger("api.nodes")


@router.get("")
async def list_nodes(request: Request):
    """获取所有已知节点列表（含实时状态和信任状态）"""
    peer_service = request.app.state.peer_service
    node_identity = request.app.state.node_identity

    nodes = peer_service.get_all_nodes()
    states = peer_service.get_all_states()

    # 组装完整的节点信息
    result = []
    for node_id, info in nodes.items():
        state = states.get(node_id, {})
        trust_status = info.get("trust_status", "pending")
        public_key = info.get("public_key", "")
        fingerprint = ""
        if public_key:
            fingerprint = hashlib.sha256(public_key.encode()).hexdigest()[:16]

        result.append({
            **info,
            "status": state.get("status", "unknown"),
            "last_seen": state.get("last_seen", 0),
            "system_info": state.get("system_info", {}),
            "is_self": node_id == node_identity.node_id,
            "trust_status": trust_status,
            "public_key_fingerprint": fingerprint,
        })

    # 排序：自身优先，然后按信任状态（trusted > pending > kicked），再按 last_seen 倒序
    trust_order = {"self": 0, "trusted": 1, "pending": 2, "waiting_approval": 3, "kicked": 4}
    result.sort(key=lambda n: (
        not n.get("is_self", False),
        trust_order.get(n.get("trust_status", ""), 5),
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
        "trust_status": TrustStatus.SELF.value,
    }


@router.get("/join-status")
async def get_join_status(request: Request):
    """
    查询本机当前的加入网络状态。
    
    用于前端展示：是否正在等待审批、加入是否成功等。
    """
    peer_service = request.app.state.peer_service
    status = peer_service.get_join_status()
    return status


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
    public_key = info.get("public_key", "")
    fingerprint = ""
    if public_key:
        fingerprint = hashlib.sha256(public_key.encode()).hexdigest()[:16]

    return {
        **info,
        "status": state.get("status", "unknown"),
        "last_seen": state.get("last_seen", 0),
        "system_info": state.get("system_info", {}),
        "public_key_fingerprint": fingerprint,
    }


@router.post("/join")
async def join_network(request: Request):
    """
    申请加入网络。

    本节点 C 向目标节点 B 发送加入申请：
    1. 先调用 B 的 /peer/handshake 获取 B 的公开信息
    2. 再调用 B 的 /peer/join-request 提交加入申请
    3. 将 B 保存到本地节点表（trust_status=waiting_approval）
    4. 启动后台轮询等待审批

    支持以下输入格式：
      - 纯 IP/域名: 192.168.1.100
      - 带端口:     192.168.1.100:9000
      - 完整 URL:   https://servers.example.com
    """
    peer_service = request.app.state.peer_service
    node_identity = request.app.state.node_identity
    storage = request.app.state.storage
    data = await request.json()

    raw_host = data.get("host", "").strip()

    if not raw_host:
        return {"error": "host 不能为空"}

    import httpx
    from urllib.parse import urlparse

    # 解析输入
    port = 8300
    port_specified = False

    if "://" in raw_host:
        parsed = urlparse(raw_host)
        scheme = parsed.scheme or "http"
        host = parsed.hostname or raw_host
        if parsed.port:
            port = parsed.port
            port_specified = True
    else:
        scheme = "http"
        if ":" in raw_host and not raw_host.startswith("["):
            parts = raw_host.rsplit(":", 1)
            try:
                port = int(parts[1])
                host = parts[0]
                port_specified = True
            except ValueError:
                host = raw_host
        else:
            host = raw_host

    target_url = f"{scheme}://{host}:{port}" if port_specified else f"{scheme}://{host}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Step 1: 获取目标节点信息（handshake）
            resp = await client.get(f"{target_url}/api/v1/peer/handshake")
            resp.raise_for_status()
            remote_info = resp.json()

            remote_node_id = remote_info.get("node_id", "")
            if not remote_node_id:
                return {"error": "目标节点返回的信息无效（缺少 node_id）"}

            # 检查是否在连接自己
            if remote_node_id == node_identity.node_id:
                return {"error": "不能加入自己"}

            # Step 2: 发送加入申请
            join_payload = node_identity.get_handshake_info()
            resp = await client.post(
                f"{target_url}/api/v1/peer/join-request",
                json=join_payload,
            )
            resp.raise_for_status()
            join_result = resp.json()

        join_status = join_result.get("status", "")

        if join_status == "kicked":
            return {"error": "该网络已将本节点踢出，无法加入"}

        # Step 3: 保存目标节点到本地
        if join_status == "trusted":
            # 已经被信任（可能之前就批准过）
            trust = TrustStatus.TRUSTED.value
            # 合并返回的网络节点信息
            network_nodes = join_result.get("nodes", {})
            if network_nodes:
                def updater(nodes):
                    for nid, ninfo in network_nodes.items():
                        if nid != node_identity.node_id:
                            nodes[nid] = ninfo
                    return nodes
                storage.update("nodes.json", updater, default={})
        else:
            trust = TrustStatus.WAITING_APPROVAL.value

        # 保存目标节点信息
        remote_entry = {
            "node_id": remote_node_id,
            "name": remote_info.get("name", host),
            "mode": remote_info.get("mode", "full"),
            "connectable": remote_info.get("connectable", False),
            "host": host,
            "port": port,
            "public_url": remote_info.get("public_url") or target_url,
            "registered_at": time.time(),
            "public_key": remote_info.get("public_key", ""),
            "trust_status": TrustStatus.TRUSTED.value if join_status == "trusted" else TrustStatus.WAITING_APPROVAL.value,
        }

        def save_remote(nodes):
            nodes[remote_node_id] = remote_entry
            return nodes
        storage.update("nodes.json", save_remote, default={})

        if join_status == "trusted":
            _logger.info(f"已加入网络: {remote_node_id} ({host}:{port})，直接获得信任")
            # 立即触发一次同步
            peer_service.start_join_polling(remote_node_id, target_url)
            return {
                "success": True,
                "status": "trusted",
                "message": "已成功加入网络",
                "node": remote_entry,
            }
        else:
            _logger.info(f"已向 {remote_node_id} ({host}:{port}) 提交加入申请，等待审批")
            # 启动轮询
            peer_service.start_join_polling(remote_node_id, target_url)
            return {
                "success": True,
                "status": "pending",
                "message": "加入申请已提交，等待管理员审批",
                "node": remote_entry,
            }

    except Exception as e:
        _logger.warning(f"加入网络失败 ({host}:{port}): {e}")
        return {"error": f"无法连接到 {target_url}: {str(e)}"}


@router.post("/{node_id}/approve")
async def approve_node(node_id: str, request: Request):
    """
    批准节点加入网络。
    
    将 pending 状态的节点改为 trusted。
    该信息会通过 Gossip 同步传播到整个网络。
    """
    storage = request.app.state.storage
    node_identity = request.app.state.node_identity

    if node_id == node_identity.node_id:
        return {"error": "不能审批自己"}

    nodes = storage.read("nodes.json", {})
    node_info = nodes.get(node_id)

    if not node_info:
        return JSONResponse(status_code=404, content={"error": "节点未找到"})

    current_status = node_info.get("trust_status", "")
    if current_status == TrustStatus.TRUSTED.value:
        return {"message": "节点已经是信任状态"}
    if current_status == TrustStatus.KICKED.value:
        return {"error": "节点已被踢出，如需重新加入请先移除再重新申请"}

    def updater(nodes):
        if node_id in nodes:
            nodes[node_id]["trust_status"] = TrustStatus.TRUSTED.value
            nodes[node_id]["registered_at"] = time.time()  # 更新时间戳以触发同步
        return nodes

    storage.update("nodes.json", updater, default={})

    _logger.info(f"已批准节点加入: {node_id} ({node_info.get('name', '?')})")
    return {"success": True, "message": f"已批准节点 {node_id} 加入网络"}


@router.post("/{node_id}/reject")
async def reject_node(node_id: str, request: Request):
    """
    拒绝节点加入申请。
    
    从节点表中移除 pending 状态的节点。
    """
    storage = request.app.state.storage
    node_identity = request.app.state.node_identity

    if node_id == node_identity.node_id:
        return {"error": "不能拒绝自己"}

    nodes = storage.read("nodes.json", {})
    node_info = nodes.get(node_id)

    if not node_info:
        return JSONResponse(status_code=404, content={"error": "节点未找到"})

    current_status = node_info.get("trust_status", "")
    if current_status != TrustStatus.PENDING.value:
        return {"error": f"只能拒绝 pending 状态的节点（当前: {current_status}）"}

    def updater(nodes):
        nodes.pop(node_id, None)
        return nodes

    storage.update("nodes.json", updater, default={})

    _logger.info(f"已拒绝节点加入: {node_id} ({node_info.get('name', '?')})")
    return {"success": True, "message": f"已拒绝节点 {node_id} 的加入申请"}


@router.post("/{node_id}/kick")
async def kick_node(node_id: str, request: Request):
    """
    踢出节点。
    
    将 trusted 节点标记为 kicked。
    该信息会通过 Gossip 同步传播到整个网络，
    所有节点都会拒绝该节点的通信请求。
    """
    storage = request.app.state.storage
    node_identity = request.app.state.node_identity

    if node_id == node_identity.node_id:
        return {"error": "不能踢出自己"}

    nodes = storage.read("nodes.json", {})
    node_info = nodes.get(node_id)

    if not node_info:
        return JSONResponse(status_code=404, content={"error": "节点未找到"})

    current_status = node_info.get("trust_status", "")
    if current_status == TrustStatus.KICKED.value:
        return {"message": "节点已经被踢出"}

    def updater(nodes):
        if node_id in nodes:
            nodes[node_id]["trust_status"] = TrustStatus.KICKED.value
            nodes[node_id]["kicked_at"] = time.time()
            nodes[node_id]["registered_at"] = time.time()  # 更新时间戳以触发同步传播
        return nodes

    storage.update("nodes.json", updater, default={})

    _logger.info(f"已踢出节点: {node_id} ({node_info.get('name', '?')})")
    return {"success": True, "message": f"已将节点 {node_id} 踢出网络"}


@router.delete("/{node_id}")
async def remove_node(node_id: str, request: Request):
    """
    从本地节点表中彻底删除节点记录。
    
    注意：这只影响本地，不会通过同步传播。
    如需全网踢出，请使用 kick 端点。
    """
    storage = request.app.state.storage
    node_identity = request.app.state.node_identity

    if node_id == node_identity.node_id:
        return {"error": "不能删除自己"}

    nodes = storage.read("nodes.json", {})
    if node_id not in nodes:
        return JSONResponse(status_code=404, content={"error": "节点未找到"})

    def updater(nodes):
        nodes.pop(node_id, None)
        return nodes

    storage.update("nodes.json", updater, default={})

    # 同时清理状态表
    def state_updater(states):
        states.pop(node_id, None)
        return states
    storage.update("states.json", state_updater, default={})

    _logger.info(f"已删除节点记录: {node_id}")
    return {"success": True, "message": f"已删除节点 {node_id}"}
