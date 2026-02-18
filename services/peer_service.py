"""
Peer 同步服务

实现：
- Full ↔ Full：有界 Gossip 协议（fan-out=3，增量同步，自适应间隔）
- Relay → Primary：心跳上报 + 接收任务
- 自动故障转移：Primary 下线切换 + Temp-Full 升降级
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


class PeerService:
    """
    Peer 通信与同步服务。

    根据节点模式执行不同的同步策略：
    - Full 模式：运行 Gossip 同步循环
    - Relay 模式：运行心跳循环
    """

    def __init__(self, node_identity, storage, config):
        """
        Args:
            node_identity: NodeIdentity 实例
            storage: FileStore 实例
            config: ConfigManager 实例
        """
        self._node = node_identity
        self._storage = storage
        self._config = config

        # 版本号（每次数据变更递增）
        self._version: int = 0

        # Relay 模式的心跳失败计数
        self._heartbeat_failures: int = 0
        self._current_primary: str = ""

        # 后台任务引用
        self._sync_task: Optional[asyncio.Task] = None
        self._running = False

    # ──────────────────────────────────────────
    # 生命周期
    # ──────────────────────────────────────────

    async def start(self):
        """启动后台同步循环"""
        self._running = True

        # 初始化当前 Primary
        self._current_primary = self._config.get("node.primary_server", "")

        if self._node.is_full:
            _logger.info("启动 Full 模式 Gossip 同步循环")
            self._sync_task = asyncio.create_task(self._gossip_loop())
        elif self._node.is_relay:
            _logger.info(f"启动 Relay 模式心跳循环 → {self._current_primary}")
            self._sync_task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self):
        """停止后台同步"""
        self._running = False
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None
        _logger.info("同步服务已停止")

    # ──────────────────────────────────────────
    # Full 模式：Gossip 同步
    # ──────────────────────────────────────────

    async def _gossip_loop(self):
        """
        Gossip 同步主循环。

        每轮从已知 Full 节点中随机选取 max_fanout 个 Peer 进行增量同步。
        间隔根据 Full 节点数量自适应调整。
        """
        base_interval = self._config.get("peer.sync_interval", 30)
        max_fanout = self._config.get("peer.max_fanout", 3)
        timeout = self._config.get("peer.timeout", 10)

        while self._running:
            try:
                # 计算自适应间隔
                full_count = self._count_full_nodes()
                interval = base_interval + math.log2(max(full_count, 1)) * 5

                # 选择同步目标
                peers = self._select_gossip_peers(max_fanout)

                if peers:
                    _logger.debug(
                        f"Gossip 同步轮次: {len(peers)} 个 Peer, "
                        f"间隔 {interval:.0f}s, 总 Full 节点 {full_count}"
                    )

                    # 并发同步
                    tasks = [
                        self._sync_with_peer(peer, timeout)
                        for peer in peers
                    ]
                    await asyncio.gather(*tasks, return_exceptions=True)

                # 更新自身状态
                await self._update_self_state()

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                _logger.error(f"Gossip 同步异常: {e}")
                await asyncio.sleep(10)

    def _count_full_nodes(self) -> int:
        """统计已知 Full 节点数量"""
        nodes = self._storage.read(NODES_FILE, {})
        return sum(
            1 for n in nodes.values()
            if n.get("mode") in ("full", "temp_full")
            and n.get("node_id") != self._node.node_id
        )

    def _select_gossip_peers(self, max_fanout: int) -> list[dict]:
        """
        随机选择 Gossip 同步目标（有界扇出）。

        只选 Full/Temp-Full 模式的节点，排除自身。
        """
        nodes = self._storage.read(NODES_FILE, {})
        candidates = [
            n for n in nodes.values()
            if n.get("mode") in ("full", "temp_full")
            and n.get("node_id") != self._node.node_id
        ]

        if not candidates:
            return []

        # 随机选 min(max_fanout, 总数) 个
        k = min(max_fanout, len(candidates))
        return random.sample(candidates, k)

    async def _sync_with_peer(self, peer: dict, timeout: float):
        """
        与单个 Full Peer 执行增量同步。

        发送本地的增量数据，接收对方的增量数据并合并。
        """
        peer_url = f"http://{peer['host']}:{peer['port']}"
        peer_id = peer.get("node_id", "unknown")

        try:
            # 构建同步请求
            local_nodes = self._storage.read(NODES_FILE, {})
            local_states = self._storage.read(STATES_FILE, {})

            payload = {
                "node_id": self._node.node_id,
                "node_key": self._node.node_key,
                "last_seen_version": self._version,
                "nodes": local_nodes,
                "states": local_states,
            }

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{peer_url}/api/v1/peer/sync",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

            # 合并对方数据
            remote_nodes = data.get("nodes", {})
            remote_states = data.get("states", {})
            remote_version = data.get("current_version", 0)

            merged_nodes = self._merge_nodes(local_nodes, remote_nodes)
            merged_states = self._merge_states(local_states, remote_states)

            self._storage.write(NODES_FILE, merged_nodes)
            self._storage.write(STATES_FILE, merged_states)

            if remote_version > self._version:
                self._version = remote_version

            _logger.debug(f"Gossip 同步完成: {peer_id} (v{remote_version})")

        except Exception as e:
            _logger.warning(f"Gossip 同步失败 [{peer_id}]: {e}")

            # 标记该节点为离线
            self._mark_node_offline(peer_id)

    # ──────────────────────────────────────────
    # Relay 模式：心跳
    # ──────────────────────────────────────────

    async def _heartbeat_loop(self):
        """
        Relay 心跳主循环。

        定期向 Primary 发送心跳 + 系统信息，接收任务。
        连续失败达到阈值时触发故障转移。
        """
        interval = self._config.get("peer.heartbeat_interval", 10)
        max_failures = self._config.get("peer.max_heartbeat_failures", 3)
        timeout = self._config.get("peer.timeout", 10)

        while self._running:
            try:
                if not self._current_primary:
                    _logger.warning("未配置 Primary Server，等待中...")
                    await asyncio.sleep(interval)
                    continue

                success = await self._send_heartbeat(timeout)

                if success:
                    self._heartbeat_failures = 0
                else:
                    self._heartbeat_failures += 1
                    _logger.warning(
                        f"心跳失败 ({self._heartbeat_failures}/{max_failures}): "
                        f"{self._current_primary}"
                    )

                    if self._heartbeat_failures >= max_failures:
                        await self._handle_primary_failure()

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                _logger.error(f"心跳循环异常: {e}")
                await asyncio.sleep(interval)

    async def _send_heartbeat(self, timeout: float) -> bool:
        """发送心跳到 Primary"""
        from services.collector import collect_system_info

        try:
            system_info = collect_system_info()

            payload = {
                "node_id": self._node.node_id,
                "node_key": self._node.node_key,
                "mode": self._node.mode.value,
                "system_info": system_info,
                "task_results": [],
            }

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{self._current_primary}/api/v1/peer/heartbeat",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

            # 处理响应：更新节点表和状态表
            if data.get("nodes"):
                self._storage.write(NODES_FILE, data["nodes"])
            if data.get("states"):
                self._storage.write(STATES_FILE, data["states"])

            return data.get("accepted", True)

        except Exception as e:
            _logger.debug(f"心跳发送失败: {e}")
            return False

    async def _handle_primary_failure(self):
        """
        处理 Primary 不可达。

        流程：
        1. 遍历已知 Full 节点，尝试切换 Primary
        2. 所有 Full 节点均不可达 → 升级为 Temp-Full
        """
        _logger.warning("Primary 连续心跳失败，开始故障转移...")

        # 尝试切换到其他 Full 节点
        nodes = self._storage.read(NODES_FILE, {})
        full_nodes = [
            n for n in nodes.values()
            if n.get("mode") in ("full", "temp_full")
            and n.get("node_id") != self._node.node_id
        ]

        timeout = self._config.get("peer.timeout", 5)

        for candidate in full_nodes:
            candidate_url = f"http://{candidate['host']}:{candidate['port']}"
            if candidate_url == self._current_primary:
                continue  # 跳过当前已失败的 Primary

            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.get(f"{candidate_url}/api/v1/system/info")
                    if resp.status_code == 200:
                        # 找到可用的 Full 节点
                        self._current_primary = candidate_url
                        self._heartbeat_failures = 0
                        _logger.info(f"已切换 Primary: {candidate_url}")
                        return
            except Exception:
                continue

        # 所有 Full 节点均不可达 → 升级为 Temp-Full
        _logger.warning("所有已知 Full 节点均不可达")
        self._node.promote_to_temp_full()
        self._heartbeat_failures = 0

        # 切换到 Gossip 模式
        if self._sync_task:
            self._sync_task.cancel()
        self._sync_task = asyncio.create_task(self._gossip_loop())

        # 启动 Full 节点恢复检测
        asyncio.create_task(self._watch_full_recovery())

    async def _watch_full_recovery(self):
        """
        监控 Full 节点是否恢复。
        在 Temp-Full 模式下每隔心跳间隔检查一次。
        """
        interval = self._config.get("peer.heartbeat_interval", 10)
        timeout = self._config.get("peer.timeout", 5)
        original_primary = self._config.get("node.primary_server", "")

        while self._running and self._node.is_temp_full:
            try:
                await asyncio.sleep(interval)

                # 检查已知 Full 节点
                nodes = self._storage.read(NODES_FILE, {})
                full_nodes = [
                    n for n in nodes.values()
                    if n.get("mode") == "full"
                    and n.get("node_id") != self._node.node_id
                ]

                for candidate in full_nodes:
                    candidate_url = f"http://{candidate['host']}:{candidate['port']}"
                    try:
                        async with httpx.AsyncClient(timeout=timeout) as client:
                            resp = await client.get(f"{candidate_url}/api/v1/system/info")
                            if resp.status_code == 200:
                                _logger.info(f"检测到 Full 节点恢复: {candidate_url}")

                                # 降级回 Relay
                                self._node.demote_from_temp_full()
                                self._current_primary = candidate_url

                                # 切换回心跳模式
                                if self._sync_task:
                                    self._sync_task.cancel()
                                self._sync_task = asyncio.create_task(self._heartbeat_loop())
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
                # 取最新的注册信息
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
        处理来自其他 Full 节点的 Gossip 同步请求。

        Returns:
            包含本地数据的同步响应
        """
        remote_nodes = request_data.get("nodes", {})
        remote_states = request_data.get("states", {})

        # 读取本地数据
        local_nodes = self._storage.read(NODES_FILE, {})
        local_states = self._storage.read(STATES_FILE, {})

        # 合并远端数据到本地
        merged_nodes = self._merge_nodes(local_nodes, remote_nodes)
        merged_states = self._merge_states(local_states, remote_states)

        self._storage.write(NODES_FILE, merged_nodes)
        self._storage.write(STATES_FILE, merged_states)

        # 返回本地数据给对方
        return {
            "node_id": self._node.node_id,
            "current_version": self._version,
            "nodes": merged_nodes,
            "states": merged_states,
        }

    def handle_heartbeat(self, request_data: dict) -> dict:
        """
        处理来自 Relay 节点的心跳请求。

        更新 Relay 的状态，返回全局数据。
        """
        relay_id = request_data.get("node_id", "")
        system_info = request_data.get("system_info", {})

        # 更新 Relay 的状态
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
                "host": "",
                "port": 8300,
                "registered_at": time.time(),
            }
            self._storage.write(NODES_FILE, nodes)

        # 返回全局数据
        all_nodes = self._storage.read(NODES_FILE, {})
        all_states = self._storage.read(STATES_FILE, {})

        return {
            "accepted": True,
            "nodes": all_nodes,
            "states": all_states,
            "current_version": self._version,
            "tasks": [],  # Phase 3
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
