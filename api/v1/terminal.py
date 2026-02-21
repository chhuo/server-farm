"""
WebSocket 终端 API

提供持久 Shell 会话，通过 WebSocket 双向通信，
实现和真实终端一样的体验（cd 保持、环境变量保持、实时输出、Tab 补全等）。
"""

import asyncio
import os
import platform
import subprocess
import threading

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from core.logger import get_logger

router = APIRouter(prefix="/terminal", tags=["terminal"])
_logger = get_logger("api.terminal")

IS_WINDOWS = platform.system() == "Windows"


class ShellSession:
    """
    持久 Shell 会话，封装一个子进程。
    通过管道与 shell stdin/stdout/stderr 通信。
    """

    def __init__(self):
        self.process = None
        self._closed = False

    def start(self):
        """启动 shell 进程"""
        if IS_WINDOWS:
            # Windows: 使用 cmd.exe，启用 ANSI 转义序列支持
            shell_cmd = ["cmd.exe"]
            # 设置环境变量启用 VT100
            env = os.environ.copy()
            env["PROMPT"] = "$P$G"

            self.process = subprocess.Popen(
                shell_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            # Unix: 使用 bash（或 sh）
            shell_cmd = ["/bin/bash", "--noediting", "-i"]
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"

            self.process = subprocess.Popen(
                shell_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
            )

        _logger.info(f"Shell 会话已启动, PID={self.process.pid}")

    def write(self, data: bytes):
        """写入数据到 shell stdin"""
        if self.process and self.process.stdin and not self._closed:
            try:
                self.process.stdin.write(data)
                self.process.stdin.flush()
            except (OSError, BrokenPipeError):
                self._closed = True

    def read_output(self, callback):
        """
        在线程中持续读取 shell 输出，每次读取到数据调用 callback(data: bytes)。
        当 shell 退出或出错时返回。
        """
        try:
            while not self._closed and self.process and self.process.stdout:
                data = self.process.stdout.read1(4096) if hasattr(self.process.stdout, 'read1') else self.process.stdout.read(1)
                if not data:
                    break
                callback(data)
        except (OSError, ValueError):
            pass
        finally:
            _logger.info("Shell 输出读取结束")

    def close(self):
        """关闭 shell 进程"""
        self._closed = True
        if self.process:
            try:
                self.process.stdin.close()
            except Exception:
                pass
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            _logger.info(f"Shell 会话已关闭, PID={self.process.pid}")

    @property
    def is_alive(self):
        return self.process and self.process.poll() is None and not self._closed


@router.websocket("/ws")
async def terminal_ws(websocket: WebSocket, node_id: str = Query(default="")):
    """
    WebSocket 终端端点。

    前端通过 WebSocket 连接此端点，建立持久 shell 会话。
    - 前端发送的文本直接写入 shell stdin
    - shell stdout 输出实时推送到前端
    """
    await websocket.accept()
    _logger.info(f"终端 WebSocket 已连接, node_id={node_id}")

    # 获取 app state
    app = websocket.app
    node_identity = app.state.node_identity

    # 检查认证 (从 cookie 或 query param)
    auth_service = app.state.auth_service
    token = websocket.cookies.get("token", "")
    session = auth_service.validate_token(token)
    if not session:
        await websocket.send_text("\r\n\x1b[31m认证失败：未登录或会话已过期\x1b[0m\r\n")
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # 判断目标节点
    target_id = node_id or node_identity.node_id

    if target_id != node_identity.node_id:
        # 远程节点 — 检查是否可达
        nodes = app.state.storage.read("nodes.json", {})
        target_info = nodes.get(target_id, {})

        if not target_info:
            await websocket.send_text(f"\r\n\x1b[31m节点不存在: {target_id}\x1b[0m\r\n")
            await websocket.close(code=4004, reason="Node not found")
            return

        target_mode = target_info.get("mode", "")
        if target_mode in ("full", "temp_full"):
            # 代理到远程 Full 节点的 WebSocket
            await _proxy_to_remote(websocket, target_info, target_id)
            return
        else:
            await websocket.send_text(
                f"\r\n\x1b[31mRelay 节点不支持实时终端，请使用命令执行功能\x1b[0m\r\n"
            )
            await websocket.close(code=4003, reason="Relay not supported")
            return

    # 本机 — 启动 shell 会话
    shell = ShellSession()
    try:
        shell.start()
    except Exception as e:
        await websocket.send_text(f"\r\n\x1b[31m启动 Shell 失败: {e}\x1b[0m\r\n")
        await websocket.close(code=4500, reason="Shell start failed")
        return

    loop = asyncio.get_event_loop()

    # 在线程中读取 shell 输出并推送到 WebSocket
    async def send_output(data: bytes):
        """将 shell 输出发送到 WebSocket"""
        try:
            # 尝试 UTF-8，失败则用 GBK (Windows 中文)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    text = data.decode("gbk")
                except UnicodeDecodeError:
                    text = data.decode("utf-8", errors="replace")
            await websocket.send_text(text)
        except Exception:
            pass

    def output_callback(data: bytes):
        """在读取线程中回调，将数据发送到事件循环"""
        asyncio.run_coroutine_threadsafe(send_output(data), loop)

    # 启动输出读取线程
    reader_thread = threading.Thread(target=shell.read_output, args=(output_callback,), daemon=True)
    reader_thread.start()

    # 主循环：从 WebSocket 接收输入，写入 shell
    try:
        while True:
            try:
                data = await websocket.receive_text()
                shell.write(data.encode("utf-8"))
            except WebSocketDisconnect:
                _logger.info("WebSocket 断开连接")
                break
            except Exception as e:
                _logger.error(f"WebSocket 接收异常: {e}")
                break
    finally:
        shell.close()
        reader_thread.join(timeout=2)
        _logger.info("终端会话结束")


async def _proxy_to_remote(websocket: WebSocket, target_info: dict, target_id: str):
    """
    代理 WebSocket 到远程 Full 节点。
    """
    import websockets

    host = target_info.get("host", "")
    port = target_info.get("port", 8300)
    remote_url = f"ws://{host}:{port}/api/v1/terminal/ws?node_id={target_id}"

    _logger.info(f"代理终端到远程节点: {remote_url}")

    try:
        # 转发 cookie
        token = websocket.cookies.get("token", "")
        extra_headers = {"Cookie": f"token={token}"} if token else {}

        async with websockets.connect(
            remote_url,
            additional_headers=extra_headers,
            ping_interval=20,
            ping_timeout=10,
        ) as remote_ws:

            async def forward_to_remote():
                """前端 → 远程"""
                try:
                    while True:
                        data = await websocket.receive_text()
                        await remote_ws.send(data)
                except (WebSocketDisconnect, Exception):
                    pass

            async def forward_to_client():
                """远程 → 前端"""
                try:
                    async for msg in remote_ws:
                        await websocket.send_text(msg)
                except Exception:
                    pass

            # 双向转发
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(forward_to_remote()),
                    asyncio.create_task(forward_to_client()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

    except Exception as e:
        _logger.error(f"代理到远程节点失败: {e}")
        await websocket.send_text(f"\r\n\x1b[31m连接远程节点失败: {e}\x1b[0m\r\n")

    try:
        await websocket.close()
    except Exception:
        pass
