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
"""

import asyncio
import math
import random
import time
from typing import Any, Optional

import httpx

from core.logger import get_logger
from models.node import NodeMode

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

    增量同步机制：
    - 每个 peer 记录上次成功同步的时间戳 (sync_meta.json)
    - 发送端只发送 last_sync_time 之后变更的数据
    - 接收端也可按请求中的 since 参数过滤返回数据
    - last_sync_time=0 时为全量同步（首次连接）
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
        self._running = False

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

        if self._node.is_full and self._node.connectable:
            # Hub Full：Gossip 同步（仅与其他可直连 Full 节点）
            _logger.info("启动 Hub Full 模式 Gossip 同步循环")
            self._sync_task = asyncio.create_task(self._gossip_loop())

        elif self._node.is_full and not self._node.connectable:
            # 内网 Full：自动发现可连接节点，主动双向同步
            _logger.info("启动内网 Full 模式主动同步循环（自动发现可连接节点）")
            self._sync_task = asyncio.create_task(self._active_sync_loop())

        elif self._node.is_relay:
            # Relay：自动发现可连接 Full 节点心跳
            _logger.info("启动 Relay 模式心跳循环（自动发现可连接节点）")
            self._sync_task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self):
        """停止后台同步"""
        self._running = False
        for task in [self._sync_task, self._state_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._sync_task = None
        self._state_task = None
        _logger.info("同步服务已停止")

    async def trigger_sync_now(self) -> dict:
        """
        手动触发一次立即同步/心跳。
        
        根据当前节点模式执行相应的同步操作：
        - Hub Full：向所有可连接节点执行一轮 Gossip 同步
        - 内网 Full：向所有可连接节点执行一次双向同步
        - Relay：向可连接 Full 节点发送一次心跳
        
        返回同步结果摘要。
        """
        timeout = self._config.get("peer.timeout", 10)
        peers = self._discover_connectable_peers()

        if not peers:
            return {
                "success": False,
                "mode": self._node.mode.value,
                "message": "未发现可连接的节点",
                "synced_peers": 0,
                "total_peers": 0,
            }

        synced = 0
        failed = 0
        sync_start = time.time()

        if self._node.is_full:
            # Full 模式（Hub 或内网）：与所有可连接节点同步
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
            # Relay / Temp-Full 模式：向可连接节点发心跳
            for peer in peers:
                success = await self._send_heartbeat(peer, timeout)
                if success:
                    synced += 1
                    break  # 心跳只需成功一个
                else:
                    failed += 1

        elapsed = round(time.time() - sync_start, 2)

        # 同时更新自身状态
        await self._update_self_state()

        _logger.info(
            f"手动同步完成: 成功 {synced}/{len(peers)}, 耗时 {elapsed}s"
        )

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
        # 停止旧的同步任务（不停 state_task）
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None

        self._heartbeat_failures = 0

        # 根据新配置重新启动
        if self._node.is_full and self._node.connectable:
            _logger.info("重启为 Hub Full 模式 Gossip 同步")
            self._sync_task = asyncio.create_task(self._gossip_loop())
        elif self._node.is_full and not self._node.connectable:
            _logger.info("重启为内网 Full 模式主动同步")
            self._sync_task = asyncio.create_task(self._active_sync_loop())
        elif self._node.is_relay:
            _logger.info("重启为 Relay 模式心跳")
            self._sync_task = asyncio.create_task(self._heartbeat_loop())

    # ──────────────────────────────────────────
    # 所有模式：自身状态更新循环
    # ──────────────────────────────────────────

    async def _self_state_loop(self):
        """
        定期更新自身状态到状态表。
        
        所有模式下都运行，确保本机的 CPU/内存/last_seen 始终是最新的。
        """
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
    # 自动发现可连接节点
    # ──────────────────────────────────────────

    def _discover_connectable_peers(self) -> list[dict]:
        """
        从本地节点表中自动发现所有可连接的 Full/Temp-Full 节点。
        
        排除自身，返回有 public_url 或可访问地址的节点列表。
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
            # 必须有可访问的地址
            url = n.get("public_url") or (
                f"http://{n['host']}:{n['port']}" if n.get("host") else ""
            )
            if url:
                peers.append(n)
        return peers

    def _get_peer_url(self, peer: dict) -> str:
        """获取节点的可访问 URL"""
        return peer.get("public_url") or f"http://{peer['host']}:{peer['port']}"

    # ──────────────────────────────────────────
    # Hub Full 模式：Gossip 同步
    # ──────────────────────────────────────────

    async def _gossip_loop(self):
        """
        Gossip 同步主循环（仅 Hub Full 节点运行）。

        每轮从已知可直连 Full 节点中随机选取 max_fanout 个 Peer 进行增量同步。
        """
        base_interval = self._config.get("peer.sync_interval", 30)
        max_fanout = self._config.get("peer.max_fanout", 3)
        timeout = self._config.get("peer.timeout", 10)

        while self._running:
            try:
                peers = self._discover_connectable_peers()
                full_count = len(peers)
                interval = base_interval + math.log2(max(full_count, 1)) * 5

                if peers:
                    k = min(max_fanout, len(peers))
                    selected = random.sample(peers, k)
                    _logger.debug(
                        f"Gossip 同步轮次: {len(selected)} 个 Peer, "
                        f"间隔 {interval:.0f}s, 可直连 Full 节点 {full_count}"
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
        """与单个 Full Peer 执行增量同步（含聊天和片段）"""
        peer_url = self._get_peer_url(peer)
        peer_id = peer.get("node_id", "unknown")

        try:
            # 获取上次同步时间，实现增量
            last_sync = self._get_peer_sync_time(peer_id)
            sync_start = time.time()

            local_nodes = self._storage.read(NODES_FILE, {})
            local_states = self._storage.read(STATES_FILE, {})
            local_chat = self._storage.read(CHAT_FILE, [])
            local_snippets = self._storage.read(SNIPPETS_FILE, [])

            # 增量过滤：只发送上次同步后变更的数据
            delta_nodes = self._filter_nodes_since(local_nodes, last_sync)
            delta_states = self._filter_states_since(local_states, last_sync)
            delta_chat = self._filter_chat_since(local_chat, last_sync)
            delta_snippets = self._filter_snippets_since(local_snippets, last_sync)

            payload = {
                "node_id": self._node.node_id,
                "node_key": self._node.node_key,
                "since": last_sync,
                "nodes": delta_nodes,
                "states": delta_states,
                "chat": delta_chat,
                "snippets": delta_snippets,
            }

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{peer_url}/api/v1/peer/sync", json=payload)
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

            if remote_version > self._version:
                self._version = remote_version

            # 记录本次同步时间
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
        """
        内网 Full 节点主动同步循环。

        自动从本地节点表中发现可连接的 Hub 节点，
        定期向它们发起双向增量数据同步。
        """
        interval = self._config.get("peer.sync_interval", 30)
        max_failures = self._config.get("peer.max_heartbeat_failures", 3)
        timeout = self._config.get("peer.timeout", 10)

        while self._running:
            try:
                peers = self._discover_connectable_peers()

                if not peers:
                    _logger.debug("未发现可连接的 Full 节点，等待节点加入...")
                    await asyncio.sleep(interval)
                    continue

                # 尝试与所有可连接节点同步
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
        """向一个 Hub 节点执行一次双向增量数据同步"""
        peer_url = self._get_peer_url(peer)
        peer_id = peer.get("node_id", "unknown")

        try:
            # 获取上次同步时间
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

            from services.collector import collect_system_info
            system_info = collect_system_info()

            payload = {
                "node_id": self._node.node_id,
                "node_key": self._node.node_key,
                "since": last_sync,
                "nodes": delta_nodes,
                "states": delta_states,
                "chat": delta_chat,
                "snippets": delta_snippets,
                "system_info": system_info,
            }

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{peer_url}/api/v1/peer/sync",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

            # 合并远端增量数据
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

            remote_version = data.get("current_version", 0)
            if remote_version > self._version:
                self._version = remote_version

            # 记录本次同步时间
            self._set_peer_sync_time(peer_id, sync_start)

            _logger.debug(
                f"内网 Full 增量同步完成: {peer_id} (v{remote_version}), "
                f"发送 nodes={len(delta_nodes)} states={len(delta_states)} "
                f"chat={len(delta_chat)} snippets={len(delta_snippets)}"
            )
            return True

        except Exception as e:
            _logger.debug(f"主动同步失败 [{peer_id}]: {e}")
            return False

    # ──────────────────────────────────────────
    # Relay 模式：心跳
    # ──────────────────────────────────────────

    async def _heartbeat_loop(self):
        """Relay 心跳主循环：自动发现可连接 Full 节点"""
        interval = self._config.get("peer.heartbeat_interval", 10)
        max_failures = self._config.get("peer.max_heartbeat_failures", 3)
        timeout = self._config.get("peer.timeout", 10)

        while self._running:
            try:
                peers = self._discover_connectable_peers()

                if not peers:
                    _logger.debug("未发现可连接的 Full 节点，等待节点加入...")
                    await asyncio.sleep(interval)
                    continue

                # 向第一个可用的 Hub 节点发心跳
                any_success = False
                for peer in peers:
                    success = await self._send_heartbeat(peer, timeout)
                    if success:
                        any_success = True
                        break  # 心跳只需要成功一个

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
        """发送心跳到指定 Hub 节点（增量同步）"""
        from services.collector import collect_system_info

        peer_url = self._get_peer_url(peer)
        peer_id = peer.get("node_id", "unknown")

        try:
            # 增量：获取上次同步时间
            last_sync = self._get_peer_sync_time(peer_id)
            sync_start = time.time()

            system_info = collect_system_info()
            task_results = self._collect_completed_task_results()

            payload = {
                "node_id": self._node.node_id,
                "node_key": self._node.node_key,
                "mode": self._node.mode.value,
                "since": last_sync,
                "system_info": system_info,
                "task_results": task_results,
            }

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{peer_url}/api/v1/peer/heartbeat",
                    json=payload,
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

            # 同步聊天和片段数据（增量合并）
            if data.get("chat"):
                local_chat = self._storage.read(CHAT_FILE, [])
                merged_chat = self._merge_chat(local_chat, data["chat"])
                self._storage.write(CHAT_FILE, merged_chat)
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

            # 记录本次同步时间
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
        """
        处理所有可连接节点不可达。

        Relay 节点 → 升级为 Temp-Full
        内网 Full → 记录警告，继续独立运行
        """
        _logger.warning("所有已知可连接 Full 节点均不可达")
        self._heartbeat_failures = 0

        if self._node.is_relay:
            self._node.promote_to_temp_full()

            if self._sync_task:
                self._sync_task.cancel()
                try:
                    await self._sync_task
                except asyncio.CancelledError:
                    pass

            # Temp-Full 但不可直连 → 独立运行
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

                peers = self._discover_connectable_peers()

                for peer in peers:
                    peer_url = self._get_peer_url(peer)
                    try:
                        # 携带 node_key 用于远端认证
                        params = {"node_key": self._node.node_key} if self._node.node_key else {}
                        async with httpx.AsyncClient(timeout=timeout) as client:
                            resp = await client.get(f"{peer_url}/api/v1/system/info", params=params)
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
        """合并节点注册表（以最新的 registered_at 为准）"""
        merged = dict(local)
        for node_id, info in remote.items():
            if node_id not in merged:
                merged[node_id] = info
            else:
                if info.get("registered_at", 0) > merged[node_id].get("registered_at", 0):
                    merged[node_id] = info
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

        # 限制最大消息数量，防止无限增长
        max_messages = 500
        if len(merged) > max_messages:
            merged = merged[-max_messages:]

        return merged

    def _merge_snippets(self, local: list, remote: list) -> list:
        """
        合并信息片段（按 id 去重，以 updated_at 最新的为准）。

        注意：保留 _deleted 标记的记录参与合并，防止已删除的片段
        因另一端尚未同步删除操作而"复活"。只在最终读取时过滤。
        """
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
                # 保留最新版本（包括 _deleted 标记）
                if snippet.get("updated_at", 0) > snippets_map[sid].get("updated_at", 0):
                    snippets_map[sid] = snippet

        # 保留所有记录（含 _deleted），由读取端过滤
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
        """
        处理来自其他节点的同步请求（Gossip 或内网 Full 主动同步）。

        支持增量同步：
        - 请求中带 since 参数时，只返回该时间之后变更的数据
        - since=0 或不存在时，返回全量数据（兼容旧版本）
        """
        since = request_data.get("since", 0)
        remote_nodes = request_data.get("nodes", {})
        remote_states = request_data.get("states", {})
        remote_chat = request_data.get("chat", [])
        remote_snippets = request_data.get("snippets", [])

        local_nodes = self._storage.read(NODES_FILE, {})
        local_states = self._storage.read(STATES_FILE, {})
        local_chat = self._storage.read(CHAT_FILE, [])
        local_snippets = self._storage.read(SNIPPETS_FILE, [])

        # 合并远端增量数据到本地
        merged_nodes = self._merge_nodes(local_nodes, remote_nodes)
        merged_states = self._merge_states(local_states, remote_states)
        merged_chat = self._merge_chat(local_chat, remote_chat)
        merged_snippets = self._merge_snippets(local_snippets, remote_snippets)

        self._storage.write(NODES_FILE, merged_nodes)
        self._storage.write(STATES_FILE, merged_states)
        self._storage.write(CHAT_FILE, merged_chat)
        self._storage.write(SNIPPETS_FILE, merged_snippets)

        # 返回增量数据给请求方
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

    def handle_heartbeat(self, request_data: dict) -> dict:
        """
        处理来自 Relay 节点的心跳请求。
        
        支持增量：根据 since 参数只返回变更的数据。
        """
        relay_id = request_data.get("node_id", "")
        system_info = request_data.get("system_info", {})
        since = request_data.get("since", 0)

        # 更新 Relay 状态
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
            }
            self._storage.write(NODES_FILE, nodes)

        all_nodes = self._storage.read(NODES_FILE, {})
        all_states = self._storage.read(STATES_FILE, {})
        all_chat = self._storage.read(CHAT_FILE, [])
        all_snippets = self._storage.read(SNIPPETS_FILE, [])

        # 增量过滤返回数据
        resp_nodes = self._filter_nodes_since(all_nodes, since)
        resp_states = self._filter_states_since(all_states, since)
        resp_chat = self._filter_chat_since(all_chat, since)
        resp_snippets = self._filter_snippets_since(all_snippets, since)

        # 获取待分发给该 Relay 的任务
        pending_tasks = []
        if self._task_service:
            pending_tasks = self._task_service.get_pending_tasks_for_relay(relay_id)

        # 处理 Relay 上报的任务结果
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
