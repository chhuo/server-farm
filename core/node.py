"""
节点身份管理

负责：
- 节点 ID 生成（首次启动自动分配）
- 运行模式判断（Full / Relay / Temp-Full）
- 节点密钥生成
- 模式切换（故障转移时的自动升降级）
"""

import hashlib
import os
import platform
import secrets
import time
from typing import Optional

from core.logger import get_logger
from models.node import NodeInfo, NodeMode

_logger = get_logger("core.node")


class NodeIdentity:
    """
    节点身份管理器。

    在 bootstrap 之后由 main.py 初始化，提供本节点的身份信息和模式管理。
    """

    def __init__(self, config, storage):
        """
        Args:
            config: ConfigManager 实例
            storage: FileStore 实例
        """
        self._config = config
        self._storage = storage

        self._node_id: str = ""
        self._name: str = ""
        self._mode: NodeMode = NodeMode.FULL
        self._node_key: str = ""
        self._host: str = ""
        self._port: int = 8300

        # Temp-Full 模式标记
        self._is_temp_full = False
        self._original_mode: Optional[NodeMode] = None

    def initialize(self) -> "NodeIdentity":
        """
        初始化节点身份。
        
        读取或生成 node_id、node_key，判断运行模式。
        """
        _logger.info("正在初始化节点身份...")

        # 读取或生成 Node ID
        self._node_id = self._resolve_node_id()

        # 显示名称
        self._name = self._config.get("node.name", "") or platform.node()

        # 网络地址
        self._host = self._config.get("server.host", "0.0.0.0")
        self._port = self._config.get("server.port", 8300)

        # 读取或生成 Node Key
        self._node_key = self._resolve_node_key()

        # 判断运行模式
        self._mode = self._resolve_mode()

        # 注册自身到节点表
        self._register_self()

        _logger.info(f"节点身份初始化完成:")
        _logger.info(f"  ID:    {self._node_id}")
        _logger.info(f"  名称:  {self._name}")
        _logger.info(f"  模式:  {self._mode.value}")
        _logger.info(f"  地址:  {self._host}:{self._port}")

        return self

    def _resolve_node_id(self) -> str:
        """读取或生成节点 ID"""
        # 优先从配置读取
        configured_id = self._config.get("node.id", "")
        if configured_id:
            _logger.debug(f"使用配置的节点 ID: {configured_id}")
            return configured_id

        # 尝试从持久化文件读取（之前生成过的）
        identity_data = self._storage.read("identity.json", {})
        if identity_data.get("node_id"):
            _logger.debug(f"使用持久化的节点 ID: {identity_data['node_id']}")
            return identity_data["node_id"]

        # 首次启动，生成新 ID: hostname-随机4位
        hostname = platform.node().lower().replace(" ", "-")[:16]
        random_suffix = secrets.token_hex(2)  # 4 个十六进制字符
        node_id = f"{hostname}-{random_suffix}"

        # 持久化
        identity_data["node_id"] = node_id
        identity_data["created_at"] = time.time()
        self._storage.write("identity.json", identity_data)

        _logger.info(f"首次启动，生成节点 ID: {node_id}")
        return node_id

    def _resolve_node_key(self) -> str:
        """读取或生成节点通信密钥"""
        # 优先从配置读取
        configured_key = self._config.get("security.node_key", "")
        if configured_key:
            return configured_key

        # 尝试从持久化文件读取
        identity_data = self._storage.read("identity.json", {})
        if identity_data.get("node_key"):
            return identity_data["node_key"]

        # 首次启动，生成新密钥
        node_key = secrets.token_urlsafe(32)

        identity_data = self._storage.read("identity.json", {})
        identity_data["node_key"] = node_key
        self._storage.write("identity.json", identity_data)

        _logger.info("首次启动，已生成节点通信密钥")
        return node_key

    def _resolve_mode(self) -> NodeMode:
        """判断节点运行模式"""
        mode_config = self._config.get("node.mode", "auto")
        primary = self._config.get("node.primary_server", "")

        if mode_config == "full":
            return NodeMode.FULL
        elif mode_config == "relay":
            if not primary:
                _logger.warning("配置为 relay 模式但未指定 primary_server，回退到 full 模式")
                return NodeMode.FULL
            return NodeMode.RELAY
        else:  # auto
            if primary:
                _logger.info(f"检测到 primary_server 配置，自动进入 Relay 模式 → {primary}")
                return NodeMode.RELAY
            else:
                _logger.info("未配置 primary_server，进入 Full 模式")
                return NodeMode.FULL

    def _register_self(self):
        """将自身注册到本地节点表"""
        node_info = {
            "node_id": self._node_id,
            "name": self._name,
            "mode": self._mode.value,
            "host": self._host,
            "port": self._port,
            "public_url": self._config.get("node.public_url", ""),
            "primary_server": self._config.get("node.primary_server", ""),
            "registered_at": time.time(),
            "node_key_hash": hashlib.sha256(self._node_key.encode()).hexdigest()[:16],
        }

        def updater(nodes):
            nodes[self._node_id] = node_info
            return nodes

        self._storage.update("nodes.json", updater, default={})
        _logger.debug("已将自身注册到本地节点表")

    # ──────────────────────────────────────────
    # 故障转移：模式切换
    # ──────────────────────────────────────────

    def promote_to_temp_full(self):
        """
        升级为 Temp-Full 模式。
        当所有已知 Full 节点不可达时触发。
        """
        if self._mode == NodeMode.FULL:
            return  # 本来就是 Full，无需升级

        self._original_mode = self._mode
        self._mode = NodeMode.TEMP_FULL
        self._is_temp_full = True

        # 更新本地节点表中自身的模式
        self._update_self_mode_in_store()

        _logger.warning("⚠️ 所有 Full 节点离线，本节点临时升级为 Full 模式")

    def demote_from_temp_full(self):
        """
        从 Temp-Full 降级回原来的模式。
        当有 Full 节点恢复时触发。
        """
        if not self._is_temp_full:
            return

        self._mode = self._original_mode or NodeMode.RELAY
        self._is_temp_full = False
        self._original_mode = None

        self._update_self_mode_in_store()

        _logger.info("Full 节点已恢复，本节点降级回 Relay 模式")

    def _update_self_mode_in_store(self):
        """更新节点表中自身的模式"""
        def updater(nodes):
            if self._node_id in nodes:
                nodes[self._node_id]["mode"] = self._mode.value
            return nodes
        self._storage.update("nodes.json", updater, default={})

    # ──────────────────────────────────────────
    # 属性访问
    # ──────────────────────────────────────────

    @property
    def node_id(self) -> str:
        return self._node_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def mode(self) -> NodeMode:
        return self._mode

    @property
    def is_full(self) -> bool:
        """是否在 Full 模式（包括 Temp-Full）"""
        return self._mode in (NodeMode.FULL, NodeMode.TEMP_FULL)

    @property
    def is_relay(self) -> bool:
        return self._mode == NodeMode.RELAY

    @property
    def is_temp_full(self) -> bool:
        return self._is_temp_full

    @property
    def node_key(self) -> str:
        return self._node_key

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}"

    def to_dict(self) -> dict:
        """返回节点身份信息字典"""
        return {
            "node_id": self._node_id,
            "name": self._name,
            "mode": self._mode.value,
            "host": self._host,
            "port": self._port,
            "is_temp_full": self._is_temp_full,
        }
