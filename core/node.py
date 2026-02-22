"""
节点身份管理

负责：
- 节点 ID 生成（首次启动自动分配）
- 运行模式判断（Full / Relay / Temp-Full）
- secp256k1 密钥对管理（生成、加载、签名、验签）
- 模式切换（故障转移时的自动升降级）
"""

import base64
import hashlib
import json
import os
import platform
import secrets
import socket
import time
from typing import Optional

from ecdsa import SECP256k1, SigningKey, VerifyingKey, BadSignatureError

from core.logger import get_logger
from models.node import NodeMode, TrustStatus

_logger = get_logger("core.node")


class NodeIdentity:
    """
    节点身份管理器。

    在 bootstrap 之后由 main.py 初始化，提供本节点的身份信息和模式管理。
    使用 secp256k1 非对称密钥对进行节点间身份认证。
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
        self._connectable: bool = False
        self._public_url: str = ""
        self._host: str = ""
        self._port: int = 8300

        # secp256k1 密钥对
        self._signing_key: Optional[SigningKey] = None   # 私钥（仅本地）
        self._verifying_key: Optional[VerifyingKey] = None  # 公钥
        self._public_key_hex: str = ""  # 公钥的 hex 编码

        # Temp-Full 模式标记
        self._is_temp_full = False
        self._original_mode: Optional[NodeMode] = None

    def initialize(self) -> "NodeIdentity":
        """
        初始化节点身份。
        
        读取或生成 node_id、密钥对，判断运行模式。
        """
        _logger.info("正在初始化节点身份...")

        # 读取或生成 Node ID
        self._node_id = self._resolve_node_id()

        # 显示名称
        self._name = self._config.get("node.name", "") or platform.node()

        # 网络地址
        self._host = self._config.get("server.host", "0.0.0.0")
        self._port = self._config.get("server.port", 8300)

        # 公网可达性
        self._connectable = self._config.get("node.connectable", False)
        self._public_url = self._config.get("node.public_url", "")

        # 读取或生成密钥对
        self._load_or_generate_keypair()

        # 判断运行模式
        self._mode = self._resolve_mode()

        # 注册自身到节点表
        self._register_self()

        _logger.info(f"节点身份初始化完成:")
        _logger.info(f"  ID:       {self._node_id}")
        _logger.info(f"  名称:     {self._name}")
        _logger.info(f"  模式:     {self._mode.value}")
        _logger.info(f"  可直连:   {'是 (' + self._public_url + ')' if self._connectable else '否（内网节点）'}")
        _logger.info(f"  地址:     {self._host}:{self._port}")
        _logger.info(f"  公钥指纹: {self.public_key_fingerprint}")

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

    def _load_or_generate_keypair(self):
        """加载或生成 secp256k1 密钥对"""
        identity_data = self._storage.read("identity.json", {})

        private_key_hex = identity_data.get("private_key", "")

        if private_key_hex:
            # 从持久化文件加载
            try:
                self._signing_key = SigningKey.from_string(
                    bytes.fromhex(private_key_hex), curve=SECP256k1
                )
                self._verifying_key = self._signing_key.get_verifying_key()
                self._public_key_hex = self._verifying_key.to_string().hex()
                _logger.debug("已加载持久化的 secp256k1 密钥对")
                return
            except Exception as e:
                _logger.warning(f"加载密钥对失败: {e}，将重新生成")

        # 首次启动，生成新密钥对
        self._signing_key = SigningKey.generate(curve=SECP256k1)
        self._verifying_key = self._signing_key.get_verifying_key()
        self._public_key_hex = self._verifying_key.to_string().hex()

        # 持久化私钥
        identity_data = self._storage.read("identity.json", {})
        identity_data["private_key"] = self._signing_key.to_string().hex()
        identity_data["public_key"] = self._public_key_hex
        # 清理旧的 node_key 字段
        identity_data.pop("node_key", None)
        self._storage.write("identity.json", identity_data)

        _logger.info("首次启动，已生成 secp256k1 密钥对")
        _logger.info(f"  公钥指纹: {self.public_key_fingerprint}")

    def _resolve_mode(self) -> NodeMode:
        """
        判断节点运行模式。

        逻辑：
        - mode=full → Full 模式
        - mode=relay + primary_server → Relay 模式
        - mode=auto:
            - 有 primary_server → Relay 模式
            - 无 primary_server + connectable → Hub Full 模式
            - 无 primary_server + 不可直连 → Full 模式但警告无法同步
        """
        mode_config = self._config.get("node.mode", "auto")
        primary = self._config.get("node.primary_server", "")

        if mode_config == "full":
            if not self._connectable and not primary:
                _logger.warning("Full 模式但无公网 IP 且未配置 primary_server，节点将无法与其他节点同步")
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
            elif self._connectable:
                _logger.info("可直连节点，进入 Hub Full 模式（等待其他节点连入）")
                return NodeMode.FULL
            else:
                _logger.info("未配置 primary_server 且不可直连，进入独立 Full 模式")
                _logger.warning("⚠️ 建议配置 primary_server 以连接 Hub 节点进行数据同步")
                return NodeMode.FULL

    def _get_actual_host(self) -> str:
        """获取实际可访问的 IP 地址（当绑定地址为 0.0.0.0 时自动探测）"""
        if self._host not in ("0.0.0.0", "", "::"):
            return self._host
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _register_self(self):
        """将自身注册到本地节点表"""
        actual_host = self._get_actual_host()
        node_info = {
            "node_id": self._node_id,
            "name": self._name,
            "mode": self._mode.value,
            "connectable": self._connectable,
            "host": actual_host,
            "port": self._port,
            "public_url": self._public_url,
            "primary_server": self._config.get("node.primary_server", ""),
            "registered_at": time.time(),
            "public_key": self._public_key_hex,
            "trust_status": TrustStatus.SELF.value,
        }

        def updater(nodes):
            nodes[self._node_id] = node_info
            return nodes

        self._storage.update("nodes.json", updater, default={})
        _logger.debug("已将自身注册到本地节点表")

    # ──────────────────────────────────────────
    # 签名 / 验签
    # ──────────────────────────────────────────

    def sign_request(self, body: bytes) -> dict:
        """
        对请求内容进行签名。

        Args:
            body: 请求体的原始字节

        Returns:
            签名头字典，用于附加到 HTTP 请求 Header:
            {
                "X-Node-Id": node_id,
                "X-Node-Ts": timestamp,
                "X-Body-Hash": sha256(body),
                "X-Node-Sig": base64(signature)
            }
        """
        timestamp = str(time.time())
        body_hash = hashlib.sha256(body).hexdigest()

        # 构造签名消息
        sign_message = json.dumps({
            "node_id": self._node_id,
            "timestamp": timestamp,
            "body_hash": body_hash,
        }, sort_keys=True).encode()

        signature = self._signing_key.sign(sign_message)

        return {
            "X-Node-Id": self._node_id,
            "X-Node-Ts": timestamp,
            "X-Body-Hash": body_hash,
            "X-Node-Sig": base64.b64encode(signature).decode(),
        }

    @staticmethod
    def verify_signature(
        node_id: str,
        timestamp: str,
        body_hash: str,
        signature_b64: str,
        public_key_hex: str,
        max_age: float = 60.0,
    ) -> bool:
        """
        验证请求签名。

        Args:
            node_id: 发送方节点 ID
            timestamp: 请求时间戳
            body_hash: 请求体的 SHA256 哈希
            signature_b64: Base64 编码的签名
            public_key_hex: 发送方公钥（hex）
            max_age: 签名最大有效期（秒），默认 60 秒

        Returns:
            验证是否通过
        """
        # 检查时间戳有效性（防重放）
        try:
            ts = float(timestamp)
            if abs(time.time() - ts) > max_age:
                _logger.debug(f"签名时间戳过期: node={node_id}, age={time.time() - ts:.1f}s")
                return False
        except (ValueError, TypeError):
            return False

        # 重建签名消息
        sign_message = json.dumps({
            "node_id": node_id,
            "timestamp": timestamp,
            "body_hash": body_hash,
        }, sort_keys=True).encode()

        try:
            vk = VerifyingKey.from_string(
                bytes.fromhex(public_key_hex), curve=SECP256k1
            )
            signature = base64.b64decode(signature_b64)
            vk.verify(signature, sign_message)
            return True
        except (BadSignatureError, Exception) as e:
            _logger.debug(f"签名验证失败: node={node_id}, error={e}")
            return False

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
                nodes[self._node_id]["registered_at"] = time.time()
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
    def public_key_hex(self) -> str:
        """公钥的 hex 编码"""
        return self._public_key_hex

    @property
    def public_key_fingerprint(self) -> str:
        """公钥指纹（前 16 位 SHA256 哈希，用于人类可读展示）"""
        if not self._public_key_hex:
            return ""
        return hashlib.sha256(self._public_key_hex.encode()).hexdigest()[:16]

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def connectable(self) -> bool:
        """是否有公网 IP 可被直连"""
        return self._connectable

    @property
    def public_url(self) -> str:
        """公网可访问的 URL"""
        return self._public_url

    def update_connectable(self, connectable: bool, public_url: str = ""):
        """
        动态更新公网可达性配置（从设置页面调用）。
        
        Args:
            connectable: 是否有公网 IP
            public_url: 公网地址
        """
        old_connectable = self._connectable
        self._connectable = connectable
        self._public_url = public_url

        # 更新本地节点表（同时更新 registered_at 以触发同步传播）
        def updater(nodes):
            if self._node_id in nodes:
                nodes[self._node_id]["connectable"] = connectable
                nodes[self._node_id]["public_url"] = public_url
                nodes[self._node_id]["registered_at"] = time.time()
            return nodes
        self._storage.update("nodes.json", updater, default={})

        if old_connectable != connectable:
            _logger.info(f"节点可达性已更新: connectable={connectable}, public_url={public_url}")

    def update_name(self, name: str):
        """动态更新节点显示名称"""
        self._name = name
        # 更新本地节点表（同时更新 registered_at 以触发同步传播）
        def updater(nodes):
            if self._node_id in nodes:
                nodes[self._node_id]["name"] = name
                nodes[self._node_id]["registered_at"] = time.time()
            return nodes
        self._storage.update("nodes.json", updater, default={})
        _logger.info(f"节点名称已更新: {name}")

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}"

    def to_dict(self) -> dict:
        """返回节点身份信息字典"""
        return {
            "node_id": self._node_id,
            "name": self._name,
            "mode": self._mode.value,
            "connectable": self._connectable,
            "public_url": self._public_url,
            "host": self._host,
            "port": self._port,
            "is_temp_full": self._is_temp_full,
            "public_key": self._public_key_hex,
            "public_key_fingerprint": self.public_key_fingerprint,
        }

    def get_handshake_info(self) -> dict:
        """返回握手所需的公开信息（不含私钥）"""
        actual_host = self._get_actual_host()
        return {
            "node_id": self._node_id,
            "name": self._name,
            "mode": self._mode.value,
            "connectable": self._connectable,
            "host": actual_host,
            "port": self._port,
            "public_url": self._public_url,
            "public_key": self._public_key_hex,
            "public_key_fingerprint": self.public_key_fingerprint,
        }
