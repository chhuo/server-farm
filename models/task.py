"""
任务数据模型
"""

import time
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class TaskInfo(BaseModel):
    """
    任务定义
    """
    task_id: str = Field(..., description="任务唯一标识")
    target_node_id: str = Field(..., description="目标节点 ID")
    command: str = Field(..., description="要执行的命令")
    status: TaskStatus = Field(TaskStatus.PENDING)
    created_at: float = Field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    timeout: int = Field(300, description="超时秒数")
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    created_by: str = Field("admin", description="创建者")
