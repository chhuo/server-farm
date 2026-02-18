"""
审计日志服务

记录所有命令执行的审计信息：
- 谁（user / node_id）
- 什么时候
- 在哪个节点执行
- 执行了什么命令
- 结果如何
"""

import json
import os
import time
from datetime import datetime
from typing import Any

from core.logger import get_logger

_logger = get_logger("services.audit")


class AuditService:
    """审计日志服务"""

    def __init__(self, storage):
        """
        Args:
            storage: FileStore 实例
        """
        self._storage = storage
        self._audit_dir = storage.ensure_subdir("audit")

    def log(
        self,
        action: str,
        user: str = "system",
        target_node: str = "",
        command: str = "",
        result: str = "",
        details: dict[str, Any] = None,
    ):
        """
        记录一条审计日志。

        Args:
            action: 操作类型 (command_execute, node_add, node_remove, etc.)
            user: 执行者
            target_node: 目标节点 ID
            command: 执行的命令
            result: 结果 (success, failed, blocked, timeout)
            details: 详细信息
        """
        entry = {
            "timestamp": time.time(),
            "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "action": action,
            "user": user,
            "target_node": target_node,
            "command": command,
            "result": result,
            "details": details or {},
        }

        # 写入按日期分割的审计文件
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"audit_{date_str}.json"
        filepath = os.path.join(self._audit_dir, filename)

        try:
            # 读取当天的审计日志
            entries = []
            if os.path.isfile(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    entries = json.load(f)

            entries.append(entry)

            # 写回
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(entries, f, ensure_ascii=False, indent=2)

            _logger.debug(
                f"审计日志: [{action}] user={user} node={target_node} "
                f"cmd={command[:50] if command else '-'} result={result}"
            )

        except Exception as e:
            _logger.error(f"审计日志写入失败: {e}")

    def query(self, date: str = None, limit: int = 100) -> list[dict]:
        """
        查询审计日志。

        Args:
            date: 日期 (YYYY-MM-DD)，默认今天
            limit: 最大返回条数

        Returns:
            审计日志列表（最新在前）
        """
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        filename = f"audit_{date}.json"
        filepath = os.path.join(self._audit_dir, filename)

        if not os.path.isfile(filepath):
            return []

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                entries = json.load(f)
            # 最新在前
            entries.reverse()
            return entries[:limit]
        except Exception as e:
            _logger.error(f"审计日志读取失败: {e}")
            return []

    def query_recent(self, limit: int = 50) -> list[dict]:
        """
        查询最近的审计日志（跨天）。
        """
        all_entries = []

        try:
            # 按文件名倒序列出审计文件
            files = sorted(
                [f for f in os.listdir(self._audit_dir) if f.startswith("audit_")],
                reverse=True,
            )

            for filename in files:
                filepath = os.path.join(self._audit_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        entries = json.load(f)
                    all_entries.extend(entries)
                    if len(all_entries) >= limit:
                        break
                except Exception:
                    continue

        except Exception as e:
            _logger.error(f"审计日志查询失败: {e}")

        all_entries.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
        return all_entries[:limit]
