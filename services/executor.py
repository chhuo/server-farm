"""
命令执行器

安全地执行系统命令，支持：
- 命令黑名单检查
- 超时控制
- stdout/stderr 捕获

使用 subprocess.run + asyncio.to_thread 实现异步执行，
兼容 Windows 和 Unix 上所有事件循环类型。
"""

import asyncio
import platform
import subprocess
from typing import Optional

from core.logger import get_logger

_logger = get_logger("services.executor")


class CommandExecutor:
    """安全的命令执行器"""

    def __init__(self, blacklist: list[str] = None):
        self._blacklist = blacklist or []
        self._is_windows = platform.system() == "Windows"

    def is_blocked(self, command: str) -> bool:
        """检查命令是否在黑名单中"""
        cmd_lower = command.lower().strip()
        for pattern in self._blacklist:
            if pattern.lower() in cmd_lower:
                _logger.warning(f"命令被黑名单拦截: {command} (匹配: {pattern})")
                return True
        return False

    async def execute(
        self,
        command: str,
        timeout: int = 300,
        cwd: Optional[str] = None,
    ) -> dict:
        """
        异步执行系统命令。

        使用 subprocess.run 在线程池中运行，避免 Windows 上
        asyncio.create_subprocess_shell 对事件循环类型的限制。

        Args:
            command: 要执行的命令字符串
            timeout: 超时秒数
            cwd: 工作目录

        Returns:
            包含 exit_code, stdout, stderr, timed_out 的字典
        """
        if self.is_blocked(command):
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"命令被安全策略拦截: {command}",
                "timed_out": False,
            }

        _logger.info(f"执行命令: {command}")

        try:
            result = await asyncio.to_thread(
                self._run_sync, command, timeout, cwd
            )
            _logger.info(f"命令执行完成: exit_code={result['exit_code']}")
            return result
        except Exception as e:
            _logger.error(f"命令执行异常: {e}")
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "timed_out": False,
            }

    def _run_sync(
        self,
        command: str,
        timeout: int,
        cwd: Optional[str],
    ) -> dict:
        """
        同步执行命令（在线程池中被调用）。
        """
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                timeout=timeout,
                cwd=cwd,
            )

            stdout_str = self._decode(proc.stdout)
            stderr_str = self._decode(proc.stderr)

            return {
                "exit_code": proc.returncode,
                "stdout": stdout_str,
                "stderr": stderr_str,
                "timed_out": False,
            }

        except subprocess.TimeoutExpired:
            _logger.warning(f"命令执行超时({timeout}s): {command}")
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"命令执行超时 ({timeout}秒)",
                "timed_out": True,
            }

    def _decode(self, data: bytes) -> str:
        """尝试多种编码解码输出"""
        if not data:
            return ""
        for encoding in ("utf-8", "gbk", "gb2312", "latin-1"):
            try:
                return data.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        return data.decode("utf-8", errors="replace")
