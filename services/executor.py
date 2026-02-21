"""
命令执行器

安全地执行系统命令，支持：
- 命令黑名单检查
- 超时控制
- stdout/stderr 捕获
"""

import asyncio
import platform
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
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )

                # 自动检测编码
                stdout_str = self._decode(stdout)
                stderr_str = self._decode(stderr)

                result = {
                    "exit_code": process.returncode,
                    "stdout": stdout_str,
                    "stderr": stderr_str,
                    "timed_out": False,
                }

                _logger.info(f"命令执行完成: exit_code={process.returncode}")
                return result

            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                _logger.warning(f"命令执行超时({timeout}s): {command}")
                return {
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": f"命令执行超时 ({timeout}秒)",
                    "timed_out": True,
                }

        except Exception as e:
            _logger.error(f"命令执行异常: {e}")
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "timed_out": False,
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
