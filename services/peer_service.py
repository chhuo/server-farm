"""
Peer 同步服务

实现：
- Hub Full（connectable=true）：Gossip 协议，仅与其他可直连 Full 节点
- 内网 Full（connectable=false）：自动从本地节点表发现可连接节点，主动双向同步
- Relay → 自动发现可连接 Full 节点发送心跳
- 所有模式：定期更新自身状态（CPU/内存/last_seen）
- 自动故障转移：Temp-Full 升降级
- 跨节点同步：聊天记录 + 信息片段
- 增量同步：仅传输上次同步后变更的数据
- 信任管理：仅与 trusted 节点通信，签名认证
- 加入轮询：等待审批时定期轮询状态
"""

import asyncio
import json
import math
import random
import time
from typing import Any, Optional

import httpx

from core.logger import get_logger
from models.node import NodeMode, TrustStatus

_logger = get_logger("services.peer")

# ──────────────────────────────────────────
# 常量
# ──────────────────────────────────────────
NODES_FILE = "nodes.json"
STATES_FILE = "states.json"
CHAT_FILE = "chat.json"
SNIPPETS_FILE = "snippets.json"
SYNC_META_FILE = "sync_meta.json"


class PeerService:
    """
    Peer 通信与同步服务。

    根据节点模式和可达性执行不同的同步策略：
    - Hub Full（connectable）：运行 Gossip 同步循环
    - 内网 Full（not connectable）：自动发现可连接节点，主动双向同步
    - Relay 模式：自动发现可连接 Full 节点，运行心跳循环
    - 所有模式：运行自身状态更新循环

    安全模型：
    - 仅与 trust_status=trusted 的节点通信
    - 请求使用 secp256k1 签名，接收方验签
    - kicked 状态通过同步传播到整个网络
    """

    def __init__(self, node_identity, storage, config, task_service=None):
        self._node = node_identity
        self._storage = storage
        self._config = config
        self._task_service = task_service

        # 版本号（每次数据变更递增）
        self._version: int = 0

        # 心跳失败计数（按节点 URL 计数）
        self._heartbeat_failures: int = 0

        # 后台任务引用
        self._sync_task: Optional[asyncio.Task] = None
        self._state_task: Optional[asyncio.Task] = None
        self._join_poll_task: Optional[asyncio.Task] = None
        self._running = False

        # 加入网络状态
        self._join_target_id: str = ""
        self._join_target_url: str = ""
        self._join_status: str = ""  # "", "polling", "trusted", "kicked", "failed"

    # ──────────────────────────────────────────
    # 增量同步：per-peer 时间戳管理
    # ──────────────────────────────────────────

    def _get_peer_sync_time(self, peer_id: str) -> float:
        """获取上次与某个 peer 成功同步的时间戳"""
        meta = self._storage.read(SYNC_META_FILE, {})
        return meta.get(peer_id, {}).get("last_sync_time", 0)

    def _set_peer_sync_time(self, peer_id: str, ts: float):
        """记录与某个 peer 成功同步的时间戳"""
        def updater(meta):
            if peer_id not in meta:
                meta[peer_id] = {}
            meta[peer_id]["last_sync_time"] = ts
            return meta
        self._storage.update(SYNC_META_FILE, updater, default={})

    def _filter_nodes_since(self, nodes: dict, since: float) -> dict:
        """过滤出 since 之后有变更的节点"""
        if since <= 0:
            return nodes
        return {
            nid: info for nid, info in nodes.items()
            if info.get("registered_at", 0) > since
        }

    def _filter_states_since(self, states: dict, since: float) -> dict:
        """过滤出 since 之后有变更的状态"""
        if since <= 0:
            return states
        return {
            nid: state for nid, state in states.items()
            if state.get("last_seen", 0) > since
        }

    def _filter_chat_since(self, chat: list, since: float) -> list:
        """过滤出 since 之后的聊天消息"""
        if since <= 0:
            return chat
        return [msg for msg in chat if msg.get("timestamp", 0) > since]

    def _filter_snippets_since(self, snippets: list, since: float) -> list:
        """过滤出 since 之后有变更的片段"""
        if since <= 0:
            return snippets
        return [s for s in snippets if s.get("updated_at", 0) > since]

    # ──────────────────────────────────────────
    # 签名辅助
    # ──────────────────────────────────────────

    def _make_signed_request_args(self, payload: dict) -> tuple[bytes, dict]:
        """
        构造带签名的请求参数。

        Returns:
            (body_bytes, headers_dict)
        """
        body = json.dumps(payload).encode()
        sig_headers = self._node.sign_request(body)
        headers = {"Content-Type": "application/json"}
        headers.update(sig_headers)
        return body, headers

    # ──────────────────────────────────────────
    # 生命周期
    # ──────────────────────────────────────────

    async def start(self):
        """启动后台同步循环"""
        self._running = True

        # 确保 sync_meta.json 存在
        if not self._storage.exists(SYNC_META_FILE):
            self._storage.write(SYNC_META_FILE, {})

        # 所有模式：启动自身状态更新循环
        self._state_task = asyncio.create_task(self._self_state_loop())
        _logger.info("已启动自身状态更新循环")

        # 检查是否有 waiting_approval 的节点需要轮询
        self._check_pending_joins()

        if self._node.is_full and self._node.connectable:
            _logger.info("启动 Hub Full 模式 Gossip 同步循环")
            self._sync_task = asyncio.create_task(self._gossip_loop())

        elif self._node.is_full and not self._node.connectable:
            _logger.info("启动内网 Full 模式主动同步循环（自动发现可连接节点）")
            self._sync_task = asyncio.create_task(self._active_sync_loop())

        elif self._node.is_relay:
            _logger.info("启动 Relay 模式心跳循环（自动发现可连接节点）")
            self._sync_task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self):
        """停止后台同步"""
        self._running = False
        for task in [self._sync_task, self._state_task, self._join_poll_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._sync_task = None
        self._state_task = None
        self._join_poll_task = None
        _logger.info("同步服务已停止")

    def _check_pending_joins(self):
        """启动时检查是否有待审批的加入申请需要恢复轮询"""
        nodes = self._storage.read(NODES_FILE, {})
        for nid, info in nodes.items():
            if info.get("trust_status") == TrustStatus.WAITING_APPROVAL.value:
                url = info.get("public_url") or f"http://{info.get('host', '')}:{info.get('port', 8300)}"
                _logger.info(f"恢复加入轮询: {nid} → {url}")
                self.start_join_polling(nid, url)
                break  # 一次只轮询一个

    async def trigger_sync_now(self) -> dict:
        """手动触发一次立即同步/心跳"""
        timeout = self._config.get("peer.timeout", 10)
        peers = self._discover_trusted_connectable_peers()

        if not peers:
            return {
                "success": False,
                "mode": self._node.mode.value,
                "message": "未发现可连接的信任节点",
                "synced_peers": 0,
                "total_peers": 0,
            }

        synced = 0
        failed = 0
        sync_start = time.time()

        if self._node.is_full:
            for peer in peers:
                try:
                    if self._node.connectable:
                        await self._sync_with_peer(peer, timeout)
                    else:
                        result = await self._do_active_sync(peer, timeout)
                        if not result:
                            raise Exception("sync returned False")
                    synced += 1
                except Exception as e:
                    _logger.debug(f"手动同步失败 [{peer.get('node_id', '?')}]: {e}")
                    failed += 1
        elif self._node.is_relay or self._node.is_temp_full:
            for peer in peers:
                success = await self._send_heartbeat(peer, timeout)
                if success:
                    synced += 1
                    break
                else:
                    failed += 1

        elapsed = round(time.time() - sync_start, 2)
        await self._update_self_state()

        return {
            "success": synced > 0,
            "mode": self._node.mode.value,
            "synced_peers": synced,
            "failed_peers": failed,
            "total_peers": len(peers),
            "elapsed": elapsed,
            "message": f"同步完成: {synced} 个节点成功" if synced > 0 else "所有节点同步失败",
        }

    async def restart_sync(self):
        """重启同步循环（配置变更后调用）"""
        _logger.info("正在重启同步循环...")
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None

        self._heartbeat_failures = 0

        if self._node.is_full and self._node.connectable:
            self._sync_task = asyncio.create_task(self._gossip_loop())
        elif self._node.is_full and not self._node.connectable:
            self._sync_task = asyncio.create_task(self._active_sync_loop())
        elif self._node.is_relay:
            self._sync_task = asyncio.create_task(self._heartbeat_loop())

    # ──────────────────────────────────────────
    # 加入网络轮询
    # ──────────────────────────────────────────

    def start_join_polling(self, target_id: str, target_url: str):
        """启动加入审批轮询"""
        self._join_target_id = target_id
        self._join_target_url = target_url
        self._join_status = "polling"

        if self._join_poll_task:
            self._join_poll_task.cancel()

        self._join_poll_task = asyncio.create_task(self._join_poll_loop())
        _logger.info(f"已启动加入审批轮询: {target_id} → {target_url}")

    def _clear_join_state(self):
        """清除加入状态（在终态延迟后调用）"""
        self._join_target_id = ""
        self._join_target_url = ""
        self._join_status = ""
        _logger.debug("加入网络状态已清除")

    def get_join_status(self) -> dict:
        """获取当前加入网络的状态"""
        if not self._join_target_id:
            return {"status": "none", "message": "未发起加入申请"}

        return {
            "status": self._join_status,
            "target_id": self._join_target_id,
            "target_url": self._join_target_url,
            "message": {
                "polling": "等待管理员审批...",
                "trusted": "已成功加入网络",
                "kicked": "已被踢出网络",
                "failed": "加入失败",
                "": "未知状态",
            }.get(self._join_status, ""),
        }

    async def _join_poll_loop(self):
        """轮询目标节点查询加入审批状态"""
        interval = self._config.get("peer.heartbeat_interval", 10)

        while self._running and self._join_status == "polling":
            try:
                await asyncio.sleep(interval)

                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(
                        f"{self._join_target_url}/api/v1/peer/join-status",
                        params={
                            "node_id": self._node.node_id,
                            "public_key": self._node.public_key_hex,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()

                status = data.get("status", "")

                if status == "trusted":
                    _logger.info("🎉 加入申请已被批准！")
                    self._join_status = "trusted"

                    # 合并网络节点信息
                    network_nodes = data.get("nodes", {})
                    if network_nodes:
                        local_nodes = self._storage.read(NODES_FILE, {})
                        for nid, ninfo in network_nodes.items():
                            if nid != self._node.node_id:
                                # 保留远端信任状态
                                if nid not in local_nodes or local_nodes[nid].get("trust_status") == TrustStatus.WAITING_APPROVAL.value:
                                    local_nodes[nid] = ninfo
                                    local_nodes[nid]["trust_status"] = TrustStatus.TRUSTED.value
                        self._storage.write(NODES_FILE, local_nodes)

                    # 更新目标节点状态为 trusted
                    def updater(nodes):
                        if self._join_target_id in nodes:
                            nodes[self._join_target_id]["trust_status"] = TrustStatus.TRUSTED.value
                        return nodes
                    self._storage.update(NODES_FILE, updater, default={})

                    # 立即触发一次同步
                    await self.trigger_sync_now()

                    # 延迟清除加入状态，避免前端无限轮询
                    await asyncio.sleep(30)
                    self._clear_join_state()
                    break

                elif status == "kicked":
                    _logger.warning("加入申请被拒绝：节点已被踢出")
                    self._join_status = "kicked"
                    # 延迟清除加入状态
                    await asyncio.sleep(60)
                    self._clear_join_state()
                    break

                else:
                    _logger.debug(f"加入状态: {status}, 继续轮询...")

            except asyncio.CancelledError:
                break
            except Exception as e:
                _logger.debug(f"加入轮询异常: {e}")
                await asyncio.sleep(interval)

    # ──────────────────────────────────────────
    # 所有模式：自身状态更新循环
    # ──────────────────────────────────────────

    async def _self_state_loop(self):
        """定期更新自身状态到状态表"""
        interval = self._config.get("peer.heartbeat_interval", 10)

        while self._running:
            try:
                await self._update_self_state()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                _logger.error(f"自身状态更新异常: {e}")
                await asyncio.sleep(interval)

    # ──────────────────────────────────────────
    # 自动发现可连接的信任节点
    # ──────────────────────────────────────────

    def _discover_trusted_connectable_peers(self) -> list[dict]:
        """
        从本地节点表中发现所有可连接且受信任的 Full/Temp-Full 节点。
        
        排除自身，排除非 trusted 节点。
        """
        nodes = self._storage.read(NODES_FILE, {})
        peers = []
        for n in nodes.values():
            if n.get("node_id") == self._node.node_id:
                continue
            if n.get("mode") not in ("full", "temp_full"):
                continue
            if not n.get("connectable", False):
                continue
            # 只与 trusted 节点通信
            if n.get("trust_status") != TrustStatus.TRUSTED.value:
                continue
            url = n.get("public_url") or (
                f"http://{n['host']}:{n['port']}" if n.get("host") else ""
            )
            if url:
                peers.append(n)
        return peers

    def _get_peer_url(self, peer: dict) -> str:
        """获取节点的可访问 URL"""
        url = peer.get("public_url") or f"http://{peer['host']}:{peer['port']}"
        return url.rstrip("/")

    # ──────────────────────────────────────────
    # Hub Full 模式：Gossip 同步
    # ──────────────────────────────────────────

    async def _gossip_loop(self):
        """Gossip 同步主循环（仅 Hub Full 节点运行）"""
        base_interval = self._config.get("peer.sync_interval", 30)
        max_fanout = self._config.get("peer.max_fanout", 3)
        timeout = self._config.get("peer.timeout", 10)

        while self._running:
            try:
                peers = self._discover_trusted_connectable_peers()
                full_count = len(peers)
                interval = base_interval + math.log2(max(full_count, 1)) * 5

                if peers:
                    k = min(max_fanout, len(peers))
                    selected = random.sample(peers, k)
                    _logger.debug(
                        f"Gossip 同步轮次: {len(selected)} 个 Peer, "
                        f"间隔 {interval:.0f}s, 可直连信任节点 {full_count}"
                    )
                    tasks = [self._sync_with_peer(peer, timeout) for peer in selected]
                    await asyncio.gather(*tasks, return_exceptions=True)

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                _logger.error(f"Gossip 同步异常: {e}")
                await asyncio.sleep(10)

    async def _sync_with_peer(self, peer: dict, timeout: float):
        """与单个 Full Peer 执行增量同步（带签名）"""
        peer_url = self._get_peer_url(peer)
        peer_id = peer.get("node_id", "unknown")

        try:
            last_sync = self._get_peer_sync_time(peer_id)
            sync_start = time.time()

            local_nodes = self._storage.read(NODES_FILE, {})
            local_states = self._storage.read(STATES_FILE, {})
            local_chat = self._storage.read(CHAT_FILE, [])
            local_snippets = self._storage.read(SNIPPETS_FILE, [])

            # 增量过滤
            delta_nodes = self._filter_nodes_since(local_nodes, last_sync)
            delta_states = self._filter_states_since(local_states, last_sync)
            delta_chat = self._filter_chat_since(local_chat, last_sync)
            delta_snippets = self._filter_snippets_since(local_snippets, last_sync)

            payload = {
                "node_id": self._node.node_id,
                "since": last_sync,
                "nodes": delta_nodes,
                "states": delta_states,
                "chat": delta_chat,
                "snippets": delta_snippets,
            }

            body, headers = self._make_signed_request_args(payload)

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{peer_url}/api/v1/peer/sync",
                    content=body,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

            # 合并对方返回的增量数据
            remote_nodes = data.get("nodes", {})
            remote_states = data.get("states", {})
            remote_chat = data.get("chat", [])
            remote_snippets = data.get("snippets", [])
            remote_version = data.get("current_version", 0)

            merged_nodes = self._merge_nodes(local_nodes, remote_nodes)
            merged_states = self._merge_states(local_states, remote_states)
            merged_chat = self._merge_chat(local_chat, remote_chat)
            merged_snippets = self._merge_snippets(local_snippets, remote_snippets)

            self._storage.write(NODES_FILE, merged_nodes)
            self._storage.write(STATES_FILE, merged_states)
            self._storage.write(CHAT_FILE, merged_chat)
            self._storage.write(SNIPPETS_FILE, merged_snippets)

            # 通知本地 WebSocket 新消息
            new_chat = self._find_new_messages(local_chat, merged_chat)
            if new_chat:
                await self._notify_chat_hub(new_chat)

            if remote_version > self._version:
                self._version = remote_version

            self._set_peer_sync_time(peer_id, sync_start)

            _logger.debug(
                f"Gossip 增量同步完成: {peer_id} (v{remote_version}), "
                f"发送 nodes={len(delta_nodes)} states={len(delta_states)} "
                f"chat={len(delta_chat)} snippets={len(delta_snippets)}"
            )

        except Exception as e:
            _logger.warning(f"Gossip 同步失败 [{peer_id}]: {e}")
            self._mark_node_offline(peer_id)

    # ──────────────────────────────────────────
    # 内网 Full 模式：主动双向同步
    # ──────────────────────────────────────────

    async def _active_sync_loop(self):
        """内网 Full 节点主动同步循环"""
        interval = self._config.get("peer.sync_interval", 30)
        max_failures = self._config.get("peer.max_heartbeat_failures", 3)
        timeout = self._config.get("peer.timeout", 10)

        while self._running:
            try:
                peers = self._discover_trusted_connectable_peers()

                if not peers:
                    _logger.debug("未发现可连接的信任节点，等待节点加入...")
                    await asyncio.sleep(interval)
                    continue

                any_success = False
                for peer in peers:
                    success = await self._do_active_sync(peer, timeout)
                    if success:
                        any_success = True

                if any_success:
                    self._heartbeat_failures = 0
                else:
                    self._heartbeat_failures += 1
                    _logger.warning(
                        f"主动同步全部失败 ({self._heartbeat_failures}/{max_failures})"
                    )
                    if self._heartbeat_failures >= max_failures:
                        await self._handle_all_peers_failure()

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                _logger.error(f"主动同步循环异常: {e}")
                await asyncio.sleep(interval)

    async def _do_active_sync(self, peer: dict, timeout: float) -> bool:
        """向一个 Hub 节点执行一次双向增量数据同步（带签名）"""
        peer_url = self._get_peer_url(peer)
        peer_id = peer.get("node_id", "unknown")

        try:
            last_sync = self._get_peer_sync_time(peer_id)
            sync_start = time.time()

            local_nodes = self._storage.read(NODES_FILE, {})
            local_states = self._storage.read(STATES_FILE, {})
            local_chat = self._storage.read(CHAT_FILE, [])
            local_snippets = self._storage.read(SNIPPETS_FILE, [])

            delta_nodes = self._filter_nodes_since(local_nodes, last_sync)
            delta_states = self._filter_states_since(local_states, last_sync)
            delta_chat = self._filter_chat_since(local_chat, last_sync)
            delta_snippets = self._filter_snippets_since(local_snippets, last_sync)

            from services.collector import collect_system_info
            system_info = collect_system_info()

            payload = {
                "node_id": self._node.node_id,
                "since": last_sync,
                "nodes": delta_nodes,
                "states": delta_states,
                "chat": delta_chat,
                "snippets": delta_snippets,
                "system_info": system_info,
            }

            body, headers = self._make_signed_request_args(payload)

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{peer_url}/api/v1/peer/sync",
                    content=body,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

            remote_nodes = data.get("nodes", {})
            remote_states = data.get("states", {})
            remote_chat = data.get("chat", [])
            remote_snippets = data.get("snippets", [])

            merged_nodes = self._merge_nodes(local_nodes, remote_nodes)
            merged_states = self._merge_states(local_states, remote_states)
            merged_chat = self._merge_chat(local_chat, remote_chat)
            merged_snippets = self._merge_snippets(local_snippets, remote_snippets)

            self._storage.write(NODES_FILE, merged_nodes)
            self._storage.write(STATES_FILE, merged_states)
            self._storage.write(CHAT_FILE, merged_chat)
            self._storage.write(SNIPPETS_FILE, merged_snippets)

            # 通知本地 WebSocket 新消息
            new_chat = self._find_new_messages(local_chat, merged_chat)
            if new_chat:
                await self._notify_chat_hub(new_chat)

            remote_version = data.get("current_version", 0)
            if remote_version > self._version:
                self._version = remote_version

            self._set_peer_sync_time(peer_id, sync_start)

            _logger.debug(
                f"内网 Full 增量同步完成: {peer_id} (v{remote_version})"
            )
            return True

        except Exception as e:
            _logger.debug(f"主动同步失败 [{peer_id}]: {e}")
            return False

    # ──────────────────────────────────────────
    # Relay 模式：心跳
    # ──────────────────────────────────────────

    async def _heartbeat_loop(self):
        """Relay 心跳主循环：自动发现可连接信任节点"""
        interval = self._config.get("peer.heartbeat_interval", 10)
        max_failures = self._config.get("peer.max_heartbeat_failures", 3)
        timeout = self._config.get("peer.timeout", 10)

        while self._running:
            try:
                peers = self._discover_trusted_connectable_peers()

                if not peers:
                    _logger.debug("未发现可连接的信任节点，等待节点加入...")
                    await asyncio.sleep(interval)
                    continue

                any_success = False
                for peer in peers:
                    success = await self._send_heartbeat(peer, timeout)
                    if success:
                        any_success = True
                        break

                if any_success:
                    self._heartbeat_failures = 0
                else:
                    self._heartbeat_failures += 1
                    _logger.warning(
                        f"心跳全部失败 ({self._heartbeat_failures}/{max_failures})"
                    )
                    if self._heartbeat_failures >= max_failures:
                        await self._handle_all_peers_failure()

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                _logger.error(f"心跳循环异常: {e}")
                await asyncio.sleep(interval)

    async def _send_heartbeat(self, peer: dict, timeout: float) -> bool:
        """发送心跳到指定 Hub 节点（带签名）"""
        from services.collector import collect_system_info

        peer_url = self._get_peer_url(peer)
        peer_id = peer.get("node_id", "unknown")

        try:
            last_sync = self._get_peer_sync_time(peer_id)
            sync_start = time.time()

            system_info = collect_system_info()
            task_results = self._collect_completed_task_results()

            payload = {
                "node_id": self._node.node_id,
                "mode": self._node.mode.value,
                "since": last_sync,
                "system_info": system_info,
                "task_results": task_results,
            }

            body, headers = self._make_signed_request_args(payload)

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{peer_url}/api/v1/peer/heartbeat",
                    content=body,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

            # 处理响应：合并增量数据
            if data.get("nodes"):
                local_nodes = self._storage.read(NODES_FILE, {})
                merged_nodes = self._merge_nodes(local_nodes, data["nodes"])
                self._storage.write(NODES_FILE, merged_nodes)
            if data.get("states"):
                local_states = self._storage.read(STATES_FILE, {})
                merged_states = self._merge_states(local_states, data["states"])
                self._storage.write(STATES_FILE, merged_states)
            if data.get("chat"):
                local_chat = self._storage.read(CHAT_FILE, [])
                merged_chat = self._merge_chat(local_chat, data["chat"])
                self._storage.write(CHAT_FILE, merged_chat)
                # 通知本地 WebSocket 新消息
                new_chat = self._find_new_messages(local_chat, merged_chat)
                if new_chat:
                    await self._notify_chat_hub(new_chat)
            if data.get("snippets"):
                local_snippets = self._storage.read(SNIPPETS_FILE, [])
                merged_snippets = self._merge_snippets(local_snippets, data["snippets"])
                self._storage.write(SNIPPETS_FILE, merged_snippets)

            # 处理 Hub 下发的任务
            pending_tasks = data.get("tasks", [])
            if pending_tasks and self._task_service:
                for task_data in pending_tasks:
                    _logger.info(f"收到 Hub 下发的任务: {task_data.get('task_id')}")
                    asyncio.create_task(self._execute_relay_task(task_data))

            self._set_peer_sync_time(peer_id, sync_start)

            _logger.debug(f"心跳成功: {peer_id} (增量 since={last_sync:.0f})")
            return data.get("accepted", True)

        except Exception as e:
            _logger.debug(f"心跳发送失败 [{peer_id}]: {e}")
            return False

    # ──────────────────────────────────────────
    # 故障转移
    # ──────────────────────────────────────────

    async def _handle_all_peers_failure(self):
        """处理所有可连接节点不可达"""
        _logger.warning("所有已知可连接信任节点均不可达")
        self._heartbeat_failures = 0

        if self._node.is_relay:
            self._node.promote_to_temp_full()

            if self._sync_task:
                self._sync_task.cancel()
                try:
                    await self._sync_task
                except asyncio.CancelledError:
                    pass

            if self._node.connectable:
                self._sync_task = asyncio.create_task(self._gossip_loop())
            else:
                _logger.warning("已升级为 Temp-Full 但无公网 IP，独立运行中")
                self._sync_task = asyncio.create_task(self._active_sync_loop())

            asyncio.create_task(self._watch_full_recovery())
        else:
            _logger.warning("内网 Full 节点无法连接任何 Hub，将在下轮重试")

    async def _watch_full_recovery(self):
        """监控可直连 Full 节点是否恢复"""
        interval = self._config.get("peer.heartbeat_interval", 10)
        timeout = self._config.get("peer.timeout", 5)

        while self._running and self._node.is_temp_full:
            try:
                await asyncio.sleep(interval)

                peers = self._discover_trusted_connectable_peers()

                for peer in peers:
                    peer_url = self._get_peer_url(peer)
                    try:
                        async with httpx.AsyncClient(timeout=timeout) as client:
                            resp = await client.get(f"{peer_url}/api/v1/peer/handshake")
                            if resp.status_code == 200:
                                _logger.info(f"检测到可连接 Full 节点恢复: {peer_url}")
                                self._node.demote_from_temp_full()

                                if self._sync_task:
                                    self._sync_task.cancel()

                                if self._node.is_relay:
                                    self._sync_task = asyncio.create_task(self._heartbeat_loop())
                                else:
                                    self._sync_task = asyncio.create_task(self._active_sync_loop())
                                return
                    except Exception:
                        continue

            except asyncio.CancelledError:
                break
            except Exception as e:
                _logger.error(f"Full 节点恢复检测异常: {e}")

    # ──────────────────────────────────────────
    # 数据合并
    # ──────────────────────────────────────────

    def _merge_nodes(self, local: dict, remote: dict) -> dict:
        """
        合并节点注册表。
        
        信任状态合并规则：
        - kicked 状态优先（任何一方标记 kicked，结果就是 kicked）
        - 远端 trusted + 本地没有 → 保存为 trusted
        - 远端 trusted + 本地 pending → 升级为 trusted（信任传播）
        - 不改变 self 状态
        - 以最新的 registered_at 为准
        """
        merged = dict(local)
        for node_id, remote_info in remote.items():
            remote_trust = remote_info.get("trust_status", "")

            if node_id not in merged:
                # 新节点：直接采用远端数据
                # 但不接受 self 状态（那是对方自己的 self）
                if remote_trust == TrustStatus.SELF.value:
                    remote_info = dict(remote_info)
                    remote_info["trust_status"] = TrustStatus.TRUSTED.value
                merged[node_id] = remote_info
            else:
                local_info = merged[node_id]
                local_trust = local_info.get("trust_status", "")

                # 不更新自己的 self 状态
                if local_trust == TrustStatus.SELF.value:
                    continue

                # kicked 优先：任何一方标记 kicked，结果就是 kicked
                if remote_trust == TrustStatus.KICKED.value:
                    if local_trust != TrustStatus.KICKED.value:
                        merged[node_id] = remote_info
                    elif remote_info.get("kicked_at", 0) > local_info.get("kicked_at", 0):
                        merged[node_id] = remote_info
                    continue

                if local_trust == TrustStatus.KICKED.value:
                    # 本地已是 kicked，保持不变
                    continue

                # 信任传播：远端 trusted + 本地 pending → trusted
                if remote_trust == TrustStatus.TRUSTED.value and local_trust == TrustStatus.PENDING.value:
                    merged[node_id] = remote_info
                    continue

                # 信任传播：远端 trusted + 本地 waiting → trusted
                if remote_trust == TrustStatus.TRUSTED.value and local_trust == TrustStatus.WAITING_APPROVAL.value:
                    merged[node_id] = remote_info
                    continue

                # 对于远端 self 状态，在合并时视为 trusted
                if remote_trust == TrustStatus.SELF.value:
                    remote_info = dict(remote_info)
                    remote_info["trust_status"] = TrustStatus.TRUSTED.value

                # 时间戳更新：以最新的 registered_at 为准
                if remote_info.get("registered_at", 0) > local_info.get("registered_at", 0):
                    # 保持本地的信任状态（除非已在上面处理过）
                    old_trust = merged[node_id].get("trust_status")
                    merged[node_id] = remote_info
                    if old_trust and remote_trust not in (TrustStatus.KICKED.value, TrustStatus.TRUSTED.value):
                        merged[node_id]["trust_status"] = old_trust

        return merged

    def _merge_states(self, local: dict, remote: dict) -> dict:
        """合并节点状态表（以最新的 last_seen 为准）"""
        merged = dict(local)
        for node_id, state in remote.items():
            if node_id not in merged:
                merged[node_id] = state
            else:
                if state.get("last_seen", 0) > merged[node_id].get("last_seen", 0):
                    merged[node_id] = state
        return merged

    def _merge_chat(self, local: list, remote: list) -> list:
        """合并聊天记录（按 id 去重，按 timestamp 排序）"""
        seen_ids = set()
        merged = []

        for msg in local + remote:
            msg_id = msg.get("id", "")
            if msg_id and msg_id not in seen_ids:
                seen_ids.add(msg_id)
                merged.append(msg)

        merged.sort(key=lambda m: m.get("timestamp", 0))

        max_messages = 500
        if len(merged) > max_messages:
            merged = merged[-max_messages:]

        return merged

    def _merge_snippets(self, local: list, remote: list) -> list:
        """合并信息片段（按 id 去重，以 updated_at 最新的为准）"""
        snippets_map = {}

        for snippet in local:
            sid = snippet.get("id", "")
            if sid:
                snippets_map[sid] = snippet

        for snippet in remote:
            sid = snippet.get("id", "")
            if not sid:
                continue
            if sid not in snippets_map:
                snippets_map[sid] = snippet
            else:
                if snippet.get("updated_at", 0) > snippets_map[sid].get("updated_at", 0):
                    snippets_map[sid] = snippet

        result = list(snippets_map.values())
        result.sort(key=lambda s: s.get("created_at", 0))
        return result

    def _mark_node_offline(self, node_id: str):
        """标记节点为离线"""
        def updater(states):
            if node_id in states:
                states[node_id]["status"] = "offline"
            return states
        self._storage.update(STATES_FILE, updater, default={})

    async def _update_self_state(self):
        """更新自身状态到状态表"""
        from services.collector import collect_system_info

        system_info = collect_system_info()
        self._version += 1

        state = {
            "node_id": self._node.node_id,
            "status": "online",
            "last_seen": time.time(),
            "system_info": system_info,
            "version": self._version,
        }

        def updater(states):
            states[self._node.node_id] = state
            return states

        self._storage.update(STATES_FILE, updater, default={})

    # ──────────────────────────────────────────
    # API 接口调用的处理方法
    # ──────────────────────────────────────────

    def handle_sync(self, request_data: dict) -> dict:
        """处理来自其他节点的同步请求"""
        since = request_data.get("since", 0)
        remote_nodes = request_data.get("nodes", {})
        remote_states = request_data.get("states", {})
        remote_chat = request_data.get("chat", [])
        remote_snippets = request_data.get("snippets", [])

        local_nodes = self._storage.read(NODES_FILE, {})
        local_states = self._storage.read(STATES_FILE, {})
        local_chat = self._storage.read(CHAT_FILE, [])
        local_snippets = self._storage.read(SNIPPETS_FILE, [])

        merged_nodes = self._merge_nodes(local_nodes, remote_nodes)
        merged_states = self._merge_states(local_states, remote_states)
        merged_chat = self._merge_chat(local_chat, remote_chat)
        merged_snippets = self._merge_snippets(local_snippets, remote_snippets)

        self._storage.write(NODES_FILE, merged_nodes)
        self._storage.write(STATES_FILE, merged_states)
        self._storage.write(CHAT_FILE, merged_chat)
        self._storage.write(SNIPPETS_FILE, merged_snippets)

        # 检测新增的聊天消息，通知本地 WebSocket
        new_chat = self._find_new_messages(local_chat, merged_chat)
        if new_chat:
            asyncio.create_task(self._notify_chat_hub(new_chat))

        resp_nodes = self._filter_nodes_since(merged_nodes, since)
        resp_states = self._filter_states_since(merged_states, since)
        resp_chat = self._filter_chat_since(merged_chat, since)
        resp_snippets = self._filter_snippets_since(merged_snippets, since)

        return {
            "node_id": self._node.node_id,
            "current_version": self._version,
            "nodes": resp_nodes,
            "states": resp_states,
            "chat": resp_chat,
            "snippets": resp_snippets,
        }

    def _find_new_messages(self, old_chat: list, merged_chat: list) -> list:
        """找出合并后新增的聊天消息"""
        old_ids = {m.get("id") for m in old_chat if m.get("id")}
        return [m for m in merged_chat if m.get("id") and m["id"] not in old_ids]

    async def _notify_chat_hub(self, new_messages: list):
        """通知本地 ChatHub 广播新消息"""
        try:
            from api.v1.chat import chat_hub
            if new_messages:
                await chat_hub.broadcast_messages(new_messages)
                _logger.debug(f"通知 ChatHub 广播 {len(new_messages)} 条新消息")
        except Exception as e:
            _logger.debug(f"通知 ChatHub 异常: {e}")

    def handle_heartbeat(self, request_data: dict) -> dict:
        """处理来自 Relay 节点的心跳请求"""
        relay_id = request_data.get("node_id", "")
        system_info = request_data.get("system_info", {})
        since = request_data.get("since", 0)

        state = {
            "node_id": relay_id,
            "status": "online",
            "last_seen": time.time(),
            "system_info": system_info,
            "version": self._version,
        }

        def updater(states):
            states[relay_id] = state
            return states
        self._storage.update(STATES_FILE, updater, default={})

        # 确保 Relay 在节点表中
        nodes = self._storage.read(NODES_FILE, {})
        if relay_id not in nodes:
            nodes[relay_id] = {
                "node_id": relay_id,
                "name": relay_id,
                "mode": request_data.get("mode", "relay"),
                "connectable": False,
                "host": "",
                "port": 8300,
                "registered_at": time.time(),
                "public_key": "",
                "trust_status": TrustStatus.TRUSTED.value,
            }
            self._storage.write(NODES_FILE, nodes)

        all_nodes = self._storage.read(NODES_FILE, {})
        all_states = self._storage.read(STATES_FILE, {})
        all_chat = self._storage.read(CHAT_FILE, [])
        all_snippets = self._storage.read(SNIPPETS_FILE, [])

        resp_nodes = self._filter_nodes_since(all_nodes, since)
        resp_states = self._filter_states_since(all_states, since)
        resp_chat = self._filter_chat_since(all_chat, since)
        resp_snippets = self._filter_snippets_since(all_snippets, since)

        pending_tasks = []
        if self._task_service:
            pending_tasks = self._task_service.get_pending_tasks_for_relay(relay_id)

        task_results = request_data.get("task_results", [])
        if task_results and self._task_service:
            self._task_service.report_task_results(task_results)

        return {
            "accepted": True,
            "nodes": resp_nodes,
            "states": resp_states,
            "chat": resp_chat,
            "snippets": resp_snippets,
            "current_version": self._version,
            "tasks": pending_tasks,
        }

    def get_all_nodes(self) -> dict:
        """获取所有已知节点"""
        return self._storage.read(NODES_FILE, {})

    def get_all_states(self) -> dict:
        """获取所有节点状态"""
        return self._storage.read(STATES_FILE, {})

    def get_node_state(self, node_id: str) -> Optional[dict]:
        """获取指定节点的状态"""
        states = self._storage.read(STATES_FILE, {})
        return states.get(node_id)

    # ──────────────────────────────────────────
    # Relay 任务处理
    # ──────────────────────────────────────────

    def _collect_completed_task_results(self) -> list[dict]:
        """收集已完成的任务结果"""
        if not self._task_service:
            return []

        results = []
        tasks = self._task_service.list_tasks(limit=20)
        for task in tasks:
            if task.get("status") in ("completed", "failed", "timeout"):
                if not task.get("_reported", False):
                    results.append(task)
                    task["_reported"] = True
                    self._task_service._save_task(task)

        return results

    async def _execute_relay_task(self, task_data: dict):
        """在 Relay 端执行从 Hub 收到的任务"""
        if not self._task_service:
            return

        task_id = task_data.get("task_id", "")
        command = task_data.get("command", "")

        _logger.info(f"Relay 执行任务: {task_id}: {command[:60]}")

        self._task_service._save_task(task_data)
        await self._task_service.execute_task(task_id)
