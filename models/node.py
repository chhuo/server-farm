"""
节点数据模型

定义节点注册信息、运行模式和信任状态。
"""

import time
from enum import Enum

from pydantic import BaseModel, Field


class NodeMode(str, Enum):
    """节点运行模式"""
    FULL = "full"
    RELAY = "relay"
    TEMP_FULL = "temp_full"  # 临时全功能模式（所有 Full 节点下线时自动升级）


class TrustStatus(str, Enum):
    """节点信任状态"""
    SELF = "self"                       # 本机节点
    PENDING = "pending"                 # 收到加入申请，待审批
    TRUSTED = "trusted"                 # 已批准，正式网络成员
    KICKED = "kicked"                   # 已被踢出网络
    WAITING_APPROVAL = "waiting_approval"  # 我发出了申请，等对方批准（仅本地视角）


class NodeInfo(BaseModel):
    """
    节点注册信息（持久化到 nodes.json）
    """
    node_id: str = Field(..., description="节点唯一标识")
    name: str = Field("", description="节点显示名称")
    mode: NodeMode = Field(NodeMode.FULL, description="运行模式")
    connectable: bool = Field(False, description="是否有公网 IP 可被直连（默认 false）")
    host: str = Field(..., description="节点地址（IP 或域名）")
    port: int = Field(8300, description="节点端口")
    public_url: str = Field("", description="公网可访问的 URL（connectable=true 时填写）")
    primary_server: str = Field("", description="Relay 模式的 Primary 地址")
    registered_at: float = Field(default_factory=time.time, description="注册时间戳")
    public_key: str = Field("", description="节点 secp256k1 公钥（hex 编码）")
    trust_status: TrustStatus = Field(TrustStatus.PENDING, description="信任状态")
    kicked_at: float = Field(0, description="被踢出的时间戳（trust_status=kicked 时有效）")

    @property
    def url(self) -> str:
        """节点的完整 URL"""
        return f"http://{self.host}:{self.port}"
