"""
任务管理服务

负责：
- 任务创建与分发
- 本地任务执行
- 任务状态追踪
- 通过心跳转发任务到 Relay 节点（NAT 友好）
"""

import time
import uuid
from typing import Any, Optional

from core.logger import get_logger
from models.task import TaskInfo, TaskStatus
from services.executor import CommandExecutor

_logger = get_logger("services.task")


class TaskService:
    """任务管理服务"""

    def __init__(self, node_identity, storage, config, audit_service):
        """
        Args:
            node_identity: NodeIdentity 实例
            storage: FileStore 实例
            config: ConfigManager 实例
            audit_service: AuditService 实例
        """
        self._node = node_identity
        self._storage = storage
        self._config = config
        self._audit = audit_service

        # 命令执行器
        blacklist = config.get("security.command_blacklist", [])
        self._executor = CommandExecutor(blacklist=blacklist)

        # 待发给 Relay 的任务队列：{node_id: [task_dict, ...]}
        self._relay_task_queue: dict[str, list[dict]] = {}

    def create_task(
        self,
        target_node_id: str,
        command: str,
        timeout: int = 300,
        created_by: str = "admin",
    ) -> dict:
        """
        创建一个新任务。

        如果目标是本机 → 直接排队执行
        如果目标是远程 Full 节点 → 通过 API 转发
        如果目标是 Relay 节点 → 放入心跳队列等 Relay 来取

        Args:
            target_node_id: 目标节点 ID
            command: 要执行的命令
            timeout: 超时秒数
            created_by: 创建者

        Returns:
            任务信息字典
        """
        task_id = f"task-{uuid.uuid4().hex[:8]}"

        task = {
            "task_id": task_id,
            "target_node_id": target_node_id,
            "command": command,
            "status": TaskStatus.PENDING.value,
            "created_at": time.time(),
            "started_at": None,
            "completed_at": None,
            "timeout": timeout,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "created_by": created_by,
        }

        # 保存任务
        self._save_task(task)

        # 审计日志
        self._audit.log(
            action="task_create",
            user=created_by,
            target_node=target_node_id,
            command=command,
            result="pending",
            details={"task_id": task_id, "timeout": timeout},
        )

        _logger.info(f"任务创建: {task_id} → {target_node_id}: {command[:60]}")

        # 分发策略
        if target_node_id == self._node.node_id:
            # 本地执行 — 标记为 pending，等调用方手动执行
            pass
        else:
            # 检查目标节点模式
            nodes = self._storage.read("nodes.json", {})
            target_info = nodes.get(target_node_id, {})
            target_mode = target_info.get("mode", "")

            if target_mode == "relay":
                # Relay 节点：放入心跳转发队列
                if target_node_id not in self._relay_task_queue:
                    self._relay_task_queue[target_node_id] = []
                self._relay_task_queue[target_node_id].append(task)
                _logger.info(f"任务 {task_id} 加入 Relay 心跳队列 → {target_node_id}")
            # Full 节点的远程执行在 API 层通过 httpx 转发

        return task

    async def execute_task(self, task_id: str) -> dict:
        """
        执行一个本地任务。

        Args:
            task_id: 任务 ID

        Returns:
            执行结果字典
        """
        task = self._load_task(task_id)
        if not task:
            return {"error": "任务不存在"}

        command = task["command"]

        # 更新状态为执行中
        task["status"] = TaskStatus.RUNNING.value
        task["started_at"] = time.time()
        self._save_task(task)

        # 执行命令
        result = await self._executor.execute(
            command=command,
            timeout=task.get("timeout", 300),
        )

        # 更新任务结果
        task["completed_at"] = time.time()
        task["exit_code"] = result["exit_code"]
        task["stdout"] = result["stdout"]
        task["stderr"] = result["stderr"]

        if result.get("timed_out"):
            task["status"] = TaskStatus.TIMEOUT.value
        elif result["exit_code"] == 0:
            task["status"] = TaskStatus.COMPLETED.value
        else:
            task["status"] = TaskStatus.FAILED.value

        self._save_task(task)

        # 审计日志
        self._audit.log(
            action="command_execute",
            user=task.get("created_by", "system"),
            target_node=self._node.node_id,
            command=command,
            result=task["status"],
            details={
                "task_id": task_id,
                "exit_code": result["exit_code"],
                "duration": task["completed_at"] - task["started_at"],
            },
        )

        _logger.info(
            f"任务完成: {task_id} status={task['status']} "
            f"exit_code={result['exit_code']}"
        )

        return task

    async def execute_command_direct(
        self,
        command: str,
        timeout: int = 60,
        user: str = "admin",
    ) -> dict:
        """
        直接执行命令（不创建任务，用于远程终端即时执行）。

        Returns:
            执行结果字典
        """
        # 审计
        self._audit.log(
            action="command_direct",
            user=user,
            target_node=self._node.node_id,
            command=command,
            result="running",
        )

        result = await self._executor.execute(command=command, timeout=timeout)

        # 审计结果
        status = "timeout" if result.get("timed_out") else \
                 "success" if result["exit_code"] == 0 else "failed"
        self._audit.log(
            action="command_result",
            user=user,
            target_node=self._node.node_id,
            command=command,
            result=status,
            details={"exit_code": result["exit_code"]},
        )

        return result

    def get_pending_tasks_for_relay(self, relay_node_id: str) -> list[dict]:
        """
        获取并清空某个 Relay 节点的待执行任务队列。
        在心跳响应时调用。
        """
        tasks = self._relay_task_queue.pop(relay_node_id, [])
        if tasks:
            _logger.info(f"分发 {len(tasks)} 个任务给 Relay: {relay_node_id}")
        return tasks

    def report_task_results(self, results: list[dict]):
        """
        处理 Relay 上报的任务执行结果。
        """
        for result in results:
            task_id = result.get("task_id", "")
            task = self._load_task(task_id)
            if task:
                task.update({
                    "status": result.get("status", TaskStatus.COMPLETED.value),
                    "completed_at": result.get("completed_at", time.time()),
                    "exit_code": result.get("exit_code"),
                    "stdout": result.get("stdout", ""),
                    "stderr": result.get("stderr", ""),
                })
                self._save_task(task)

                self._audit.log(
                    action="task_result_relay",
                    target_node=task.get("target_node_id", ""),
                    command=task.get("command", ""),
                    result=task["status"],
                    details={"task_id": task_id},
                )

    def list_tasks(self, limit: int = 50) -> list[dict]:
        """列出最近的任务"""
        tasks_dir = self._storage.ensure_subdir("tasks")
        import os
        files = sorted(
            [f for f in os.listdir(tasks_dir) if f.endswith(".json")],
            reverse=True,
        )

        result = []
        for filename in files[:limit]:
            filepath = os.path.join(tasks_dir, filename)
            try:
                import json
                with open(filepath, "r", encoding="utf-8") as f:
                    result.append(json.load(f))
            except Exception:
                continue

        result.sort(key=lambda t: t.get("created_at", 0), reverse=True)
        return result[:limit]

    def get_task(self, task_id: str) -> Optional[dict]:
        """获取单个任务"""
        return self._load_task(task_id)

    def _save_task(self, task: dict):
        """保存任务到文件"""
        import json
        tasks_dir = self._storage.ensure_subdir("tasks")
        filepath = os.path.join(tasks_dir, f"{task['task_id']}.json")
        try:
            import os
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(task, f, ensure_ascii=False, indent=2)
        except Exception as e:
            _logger.error(f"任务保存失败: {e}")

    def _load_task(self, task_id: str) -> Optional[dict]:
        """加载任务从文件"""
        import json, os
        tasks_dir = self._storage.ensure_subdir("tasks")
        filepath = os.path.join(tasks_dir, f"{task_id}.json")

        if not os.path.isfile(filepath):
            return None
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
