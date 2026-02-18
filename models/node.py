"""
节点数据模型

定义节点注册信息和状态的 Pydantic 模型。
"""

import time
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class NodeMode(str, Enum):
    """节点运行模式"""
    FULL = "full"
    RELAY = "relay"
    TEMP_FULL = "temp_full"  # 临时全功能模式（所有 Full 节点下线时自动升级）


class NodeStatus(str, Enum):
    """节点在线状态"""
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class NodeInfo(BaseModel):
    """
    节点注册信息（持久化到 nodes.json）
    """
    node_id: str = Field(..., description="节点唯一标识")
    name: str = Field("", description="节点显示名称")
    mode: NodeMode = Field(NodeMode.FULL, description="运行模式")
    host: str = Field(..., description="节点地址（IP 或域名）")
    port: int = Field(8300, description="节点端口")
    primary_server: str = Field("", description="Relay 模式的 Primary 地址")
    registered_at: float = Field(default_factory=time.time, description="注册时间戳")
    node_key_hash: str = Field("", description="节点密钥的哈希值（用于认证）")

    @property
    def url(self) -> str:
        """节点的完整 URL"""
        return f"http://{self.host}:{self.port}"


class NodeState(BaseModel):
    """
    节点实时状态（持久化到 states.json）
    """
    node_id: str
    status: NodeStatus = NodeStatus.UNKNOWN
    last_seen: float = Field(default_factory=time.time, description="最后一次心跳/同步时间")
    system_info: dict[str, Any] = Field(default_factory=dict, description="系统采集快照")
    version: int = Field(0, description="状态版本号（每次更新递增）")

    def is_alive(self, timeout: float = 60.0) -> bool:
        """判断节点是否在线（超过 timeout 秒未心跳则视为离线）"""
        return (time.time() - self.last_seen) < timeout


class PeerSyncRequest(BaseModel):
    """
    Full ↔ Full 同步请求
    """
    node_id: str
    node_key: str = ""
    last_seen_version: int = 0  # 请求方最后见过的版本号
    nodes: dict[str, dict[str, Any]] = Field(default_factory=dict, description="节点注册表增量")
    states: dict[str, dict[str, Any]] = Field(default_factory=dict, description="节点状态增量")


class PeerSyncResponse(BaseModel):
    """
    Full ↔ Full 同步响应
    """
    node_id: str
    current_version: int = 0
    nodes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    states: dict[str, dict[str, Any]] = Field(default_factory=dict)


class HeartbeatRequest(BaseModel):
    """
    Relay → Primary 心跳请求
    """
    node_id: str
    node_key: str = ""
    mode: NodeMode = NodeMode.RELAY
    system_info: dict[str, Any] = Field(default_factory=dict)
    task_results: list[dict[str, Any]] = Field(default_factory=list)


class HeartbeatResponse(BaseModel):
    """
    Primary → Relay 心跳响应
    """
    accepted: bool = True
    tasks: list[dict[str, Any]] = Field(default_factory=list)  # 待执行任务
    nodes: dict[str, dict[str, Any]] = Field(default_factory=dict)  # 全局节点表
    states: dict[str, dict[str, Any]] = Field(default_factory=dict)  # 全局状态表
    current_version: int = 0


class NodeAddRequest(BaseModel):
    """
    前端/API 添加新节点请求
    """
    host: str
    port: int = 8300
    node_key: str = ""  # 远端节点的通信密钥
