"""
Peer 同步服务

实现：
- Hub Full（connectable=true）：Gossip 协议，仅与其他可直连 Full 节点
- 内网 Full（connectable=false + primary_server）：主动双向同步到 Hub 节点
- Relay → Primary：心跳上报 + 接收任务
- 自动故障转移：Primary 下线切换 + Temp-Full 升降级
- 跨节点同步：聊天记录 + 信息片段
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


class PeerService:
    """
    Peer 通信与同步服务。

    根据节点模式和可达性执行不同的同步策略：
    - Hub Full（connectable）：运行 Gossip 同步循环
    - 内网 Full（not connectable + primary_server）：主动双向同步循环
    - Relay 模式：运行心跳循环
    """

    def __init__(self, node_identity, storage, config, task_service=None):
        self._node = node_identity
        self._storage = storage
        self._config = config
        self._task_service = task_service

        # 版本号（每次数据变更递增）
        self._version: int = 0

        # 心跳失败计数
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
        self._current_primary = self._config.get("node.primary_server", "")

        if self._node.is_full and self._node.connectable:
            # Hub Full：Gossip 同步（仅与其他可直连 Full 节点）
            _logger.info("启动 Hub Full 模式 Gossip 同步循环")
            self._sync_task = asyncio.create_task(self._gossip_loop())

        elif self._node.is_full and not self._node.connectable:
            # 内网 Full：主动双向同步
            if self._current_primary:
                _logger.info(f"启动内网 Full 模式主动同步循环 → {self._current_primary}")
                self._sync_task = asyncio.create_task(self._active_sync_loop())
            else:
                _logger.warning("内网 Full 节点未配置 primary_server，无法与其他节点同步")

        elif self._node.is_relay:
            # Relay：心跳
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
                full_count = self._count_connectable_full_nodes()
                interval = base_interval + math.log2(max(full_count, 1)) * 5

                peers = self._select_gossip_peers(max_fanout)

                if peers:
                    _logger.debug(
                        f"Gossip 同步轮次: {len(peers)} 个 Peer, "
                        f"间隔 {interval:.0f}s, 可直连 Full 节点 {full_count}"
                    )
                    tasks = [self._sync_with_peer(peer, timeout) for peer in peers]
                    await asyncio.gather(*tasks, return_exceptions=True)

                await self._update_self_state()
                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                _logger.error(f"Gossip 同步异常: {e}")
                await asyncio.sleep(10)

    def _count_connectable_full_nodes(self) -> int:
        """统计已知可直连 Full 节点数量"""
        nodes = self._storage.read(NODES_FILE, {})
        return sum(
            1 for n in nodes.values()
            if n.get("mode") in ("full", "temp_full")
            and n.get("connectable", False)
            and n.get("node_id") != self._node.node_id
        )

    def _select_gossip_peers(self, max_fanout: int) -> list[dict]:
        """
        随机选择 Gossip 同步目标。

        只选可直连的 Full/Temp-Full 节点，排除自身。
        """
        nodes = self._storage.read(NODES_FILE, {})
        candidates = [
            n for n in nodes.values()
            if n.get("mode") in ("full", "temp_full")
            and n.get("connectable", False)
            and n.get("node_id") != self._node.node_id
        ]

        if not candidates:
            return []

        k = min(max_fanout, len(candidates))
        return random.sample(candidates, k)

    async def _sync_with_peer(self, peer: dict, timeout: float):
        """与单个 Full Peer 执行增量同步（含聊天和片段）"""
        peer_url = peer.get("public_url") or f"http://{peer['host']}:{peer['port']}"
        peer_id = peer.get("node_id", "unknown")

        try:
            local_nodes = self._storage.read(NODES_FILE, {})
            local_states = self._storage.read(STATES_FILE, {})
            local_chat = self._storage.read(CHAT_FILE, [])
            local_snippets = self._storage.read(SNIPPETS_FILE, [])

            payload = {
                "node_id": self._node.node_id,
                "node_key": self._node.node_key,
                "last_seen_version": self._version,
                "nodes": local_nodes,
                "states": local_states,
                "chat": local_chat,
                "snippets": local_snippets,
            }

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{peer_url}/api/v1/peer/sync", json=payload)
                resp.raise_for_status()
                data = resp.json()

            # 合并对方数据
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

            _logger.debug(f"Gossip 同步完成: {peer_id} (v{remote_version})")

        except Exception as e:
            _logger.warning(f"Gossip 同步失败 [{peer_id}]: {e}")
            self._mark_node_offline(peer_id)

    # ──────────────────────────────────────────
    # 内网 Full 模式：主动双向同步
    # ──────────────────────────────────────────

    async def _active_sync_loop(self):
        """
        内网 Full 节点主动同步循环。

        定期向 Hub 节点（primary_server）发起双向数据同步。
        与 Gossip 的区别：不会被别人连接，只能自己主动发起。
        数据同步是双向的（发送本地数据 + 接收远端数据），与 Relay 单向心跳不同。
        """
        interval = self._config.get("peer.sync_interval", 30)
        max_failures = self._config.get("peer.max_heartbeat_failures", 3)
        timeout = self._config.get("peer.timeout", 10)

        while self._running:
            try:
                if not self._current_primary:
                    _logger.warning("内网 Full 节点未配置 primary_server，等待中...")
                    await asyncio.sleep(interval)
                    continue

                success = await self._do_active_sync(timeout)

                if success:
                    self._heartbeat_failures = 0
                else:
                    self._heartbeat_failures += 1
                    _logger.warning(
                        f"主动同步失败 ({self._heartbeat_failures}/{max_failures}): "
                        f"{self._current_primary}"
                    )

                    if self._heartbeat_failures >= max_failures:
                        await self._handle_primary_failure()

                await self._update_self_state()
                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                _logger.error(f"主动同步循环异常: {e}")
                await asyncio.sleep(interval)

    async def _do_active_sync(self, timeout: float) -> bool:
        """向 Hub 节点执行一次双向数据同步"""
        try:
            local_nodes = self._storage.read(NODES_FILE, {})
            local_states = self._storage.read(STATES_FILE, {})
            local_chat = self._storage.read(CHAT_FILE, [])
            local_snippets = self._storage.read(SNIPPETS_FILE, [])

            # 先更新自身状态
            from services.collector import collect_system_info
            system_info = collect_system_info()

            payload = {
                "node_id": self._node.node_id,
                "node_key": self._node.node_key,
                "last_seen_version": self._version,
                "nodes": local_nodes,
                "states": local_states,
                "chat": local_chat,
                "snippets": local_snippets,
                "system_info": system_info,
            }

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{self._current_primary}/api/v1/peer/sync",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

            # 合并远端数据
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

            _logger.debug(f"内网 Full 主动同步完成 (v{remote_version})")
            return True

        except Exception as e:
            _logger.debug(f"主动同步失败: {e}")
            return False

    # ──────────────────────────────────────────
    # Relay 模式：心跳
    # ──────────────────────────────────────────

    async def _heartbeat_loop(self):
        """Relay 心跳主循环"""
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
            task_results = self._collect_completed_task_results()

            payload = {
                "node_id": self._node.node_id,
                "node_key": self._node.node_key,
                "mode": self._node.mode.value,
                "system_info": system_info,
                "task_results": task_results,
            }

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{self._current_primary}/api/v1/peer/heartbeat",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

            # 处理响应
            if data.get("nodes"):
                self._storage.write(NODES_FILE, data["nodes"])
            if data.get("states"):
                self._storage.write(STATES_FILE, data["states"])

            # 同步聊天和片段数据
            if data.get("chat"):
                local_chat = self._storage.read(CHAT_FILE, [])
                merged_chat = self._merge_chat(local_chat, data["chat"])
                self._storage.write(CHAT_FILE, merged_chat)
            if data.get("snippets"):
                local_snippets = self._storage.read(SNIPPETS_FILE, [])
                merged_snippets = self._merge_snippets(local_snippets, data["snippets"])
                self._storage.write(SNIPPETS_FILE, merged_snippets)

            # 处理 Primary 下发的任务
            pending_tasks = data.get("tasks", [])
            if pending_tasks and self._task_service:
                for task_data in pending_tasks:
                    _logger.info(f"收到 Primary 下发的任务: {task_data.get('task_id')}")
                    asyncio.create_task(self._execute_relay_task(task_data))

            return data.get("accepted", True)

        except Exception as e:
            _logger.debug(f"心跳发送失败: {e}")
            return False

    # ──────────────────────────────────────────
    # 故障转移
    # ──────────────────────────────────────────

    async def _handle_primary_failure(self):
        """
        处理 Primary 不可达。

        1. 遍历已知可直连 Full 节点，尝试切换 Primary
        2. 所有可直连 Full 节点均不可达 → 升级为 Temp-Full
        """
        _logger.warning("Primary 连续失败，开始故障转移...")

        nodes = self._storage.read(NODES_FILE, {})
        connectable_full_nodes = [
            n for n in nodes.values()
            if n.get("mode") in ("full", "temp_full")
            and n.get("connectable", False)
            and n.get("node_id") != self._node.node_id
        ]

        timeout = self._config.get("peer.timeout", 5)

        for candidate in connectable_full_nodes:
            candidate_url = candidate.get("public_url") or f"http://{candidate['host']}:{candidate['port']}"
            if candidate_url == self._current_primary:
                continue

            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.get(f"{candidate_url}/api/v1/system/info")
                    if resp.status_code == 200:
                        self._current_primary = candidate_url
                        self._heartbeat_failures = 0
                        _logger.info(f"已切换 Primary: {candidate_url}")
                        return
            except Exception:
                continue

        # 所有可直连 Full 节点均不可达
        _logger.warning("所有已知可直连 Full 节点均不可达")

        if self._node.is_relay:
            self._node.promote_to_temp_full()
            self._heartbeat_failures = 0

            if self._sync_task:
                self._sync_task.cancel()

            # Temp-Full 但不可直连 → 独立运行，无法 Gossip
            if self._node.connectable:
                self._sync_task = asyncio.create_task(self._gossip_loop())
            else:
                _logger.warning("已升级为 Temp-Full 但无公网 IP，仅本地独立运行")

            asyncio.create_task(self._watch_full_recovery())

    async def _watch_full_recovery(self):
        """监控可直连 Full 节点是否恢复"""
        interval = self._config.get("peer.heartbeat_interval", 10)
        timeout = self._config.get("peer.timeout", 5)

        while self._running and self._node.is_temp_full:
            try:
                await asyncio.sleep(interval)

                nodes = self._storage.read(NODES_FILE, {})
                connectable_full = [
                    n for n in nodes.values()
                    if n.get("mode") == "full"
                    and n.get("connectable", False)
                    and n.get("node_id") != self._node.node_id
                ]

                for candidate in connectable_full:
                    candidate_url = candidate.get("public_url") or f"http://{candidate['host']}:{candidate['port']}"
                    try:
                        async with httpx.AsyncClient(timeout=timeout) as client:
                            resp = await client.get(f"{candidate_url}/api/v1/system/info")
                            if resp.status_code == 200:
                                _logger.info(f"检测到可直连 Full 节点恢复: {candidate_url}")
                                self._node.demote_from_temp_full()
                                self._current_primary = candidate_url

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
                # 保留最新版本
                if snippet.get("updated_at", 0) > snippets_map[sid].get("updated_at", 0):
                    snippets_map[sid] = snippet

        # 过滤已删除的
        result = [s for s in snippets_map.values() if not s.get("_deleted", False)]
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

        返回合并后的全量数据。
        """
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

        return {
            "node_id": self._node.node_id,
            "current_version": self._version,
            "nodes": merged_nodes,
            "states": merged_states,
            "chat": merged_chat,
            "snippets": merged_snippets,
        }

    def handle_heartbeat(self, request_data: dict) -> dict:
        """处理来自 Relay 节点的心跳请求"""
        relay_id = request_data.get("node_id", "")
        system_info = request_data.get("system_info", {})

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
            "nodes": all_nodes,
            "states": all_states,
            "chat": self._storage.read(CHAT_FILE, []),
            "snippets": self._storage.read(SNIPPETS_FILE, []),
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
        """在 Relay 端执行从 Primary 收到的任务"""
        if not self._task_service:
            return

        task_id = task_data.get("task_id", "")
        command = task_data.get("command", "")

        _logger.info(f"Relay 执行任务: {task_id}: {command[:60]}")

        self._task_service._save_task(task_data)
        await self._task_service.execute_task(task_id)
