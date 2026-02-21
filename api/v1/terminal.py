"""
WebSocket 终端 API

使用 PTY（伪终端）提供持久 Shell 会话，通过 WebSocket 双向通信，
实现和真实终端一样的体验（按键回显、cd 保持、环境变量保持、实时输出、Tab 补全、Ctrl+C 等）。

协议约定：
- 前端发送纯文本 → 直接写入 PTY stdin（用户按键）
- 前端发送 JSON（以 { 开头）→ 控制消息，如 {"type":"resize","cols":80,"rows":24}
- 后端向前端发送纯文本 → PTY 输出
"""

import asyncio
import json
import os
import platform
import threading

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from core.logger import get_logger

router = APIRouter(prefix="/terminal", tags=["terminal"])
_logger = get_logger("api.terminal")

IS_WINDOWS = platform.system() == "Windows"


class ShellSession:
    """
    基于 PTY 的持久 Shell 会话。
    - Windows: 使用 pywinpty
    - Unix: 使用 pty + os 模块
    """

    def __init__(self):
        self._closed = False
        # Windows: pywinpty 对象
        self._winpty = None
        # Unix: master fd / child pid
        self._master_fd = None
        self._child_pid = None

    def start(self, cols=80, rows=24):
        """启动 PTY shell 进程"""
        if IS_WINDOWS:
            self._start_windows(cols, rows)
        else:
            self._start_unix(cols, rows)

    def _start_windows(self, cols, rows):
        """Windows: 使用 pywinpty 创建 PTY"""
        from winpty import PtyProcess

        # 使用 cmd.exe 或 powershell
        shell = os.environ.get("COMSPEC", "cmd.exe")
        env = os.environ.copy()

        self._winpty = PtyProcess.spawn(
            shell,
            dimensions=(rows, cols),
            env=env,
        )
        _logger.info(f"Windows PTY 会话已启动, PID={self._winpty.pid}")

    def _start_unix(self, cols, rows):
        """Unix: 使用 pty.openpty() 创建伪终端"""
        import pty
        import struct
        import fcntl
        import termios

        # 创建 PTY
        master_fd, slave_fd = pty.openpty()

        # 设置终端尺寸
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        # Fork 子进程
        pid = os.fork()
        if pid == 0:
            # 子进程
            os.close(master_fd)
            os.setsid()

            # 设置 slave 为控制终端
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

            # 重定向 stdin/stdout/stderr 到 slave
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)

            # 设置环境变量
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["COLORTERM"] = "truecolor"

            # 启动 shell
            shell = os.environ.get("SHELL", "/bin/bash")
            os.execvpe(shell, [shell, "-i"], env)
        else:
            # 父进程
            os.close(slave_fd)
            self._master_fd = master_fd
            self._child_pid = pid
            _logger.info(f"Unix PTY 会话已启动, PID={pid}")

    def write(self, data: bytes):
        """写入数据到 PTY"""
        if self._closed:
            return
        try:
            if IS_WINDOWS and self._winpty:
                # pywinpty 接受 str
                self._winpty.write(data.decode("utf-8", errors="replace"))
            elif self._master_fd is not None:
                os.write(self._master_fd, data)
        except (OSError, BrokenPipeError, EOFError):
            self._closed = True

    def resize(self, cols: int, rows: int):
        """调整 PTY 窗口大小"""
        if self._closed:
            return
        try:
            if IS_WINDOWS and self._winpty:
                self._winpty.setwinsize(rows, cols)
            elif self._master_fd is not None:
                import struct
                import fcntl
                import termios
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)
        except Exception as e:
            _logger.warning(f"Resize 失败: {e}")

    def read_output(self, callback):
        """
        在线程中持续读取 PTY 输出，每次读取到数据调用 callback(data: bytes)。
        当 shell 退出或出错时返回。
        """
        try:
            if IS_WINDOWS and self._winpty:
                self._read_windows(callback)
            elif self._master_fd is not None:
                self._read_unix(callback)
        except Exception as e:
            _logger.debug(f"PTY 读取结束: {e}")
        finally:
            _logger.info("PTY 输出读取线程退出")

    def _read_windows(self, callback):
        """Windows PTY 读取循环"""
        while not self._closed and self._winpty and self._winpty.isalive():
            try:
                data = self._winpty.read(4096)
                if data:
                    # pywinpty 返回 str，转为 bytes
                    callback(data.encode("utf-8", errors="replace"))
            except EOFError:
                break
            except Exception:
                break

    def _read_unix(self, callback):
        """Unix PTY 读取循环"""
        import select
        while not self._closed and self._master_fd is not None:
            try:
                r, _, _ = select.select([self._master_fd], [], [], 0.1)
                if r:
                    data = os.read(self._master_fd, 4096)
                    if not data:
                        break
                    callback(data)
            except (OSError, ValueError):
                break

    def close(self):
        """关闭 PTY 会话"""
        if self._closed:
            return
        self._closed = True

        if IS_WINDOWS and self._winpty:
            try:
                pid = self._winpty.pid
                self._winpty.close(force=True)
                _logger.info(f"Windows PTY 会话已关闭, PID={pid}")
            except Exception:
                pass
            self._winpty = None

        elif self._master_fd is not None:
            pid = self._child_pid
            try:
                os.close(self._master_fd)
            except Exception:
                pass
            if self._child_pid:
                try:
                    import signal
                    os.kill(self._child_pid, signal.SIGTERM)
                    os.waitpid(self._child_pid, 0)
                except Exception:
                    try:
                        os.kill(self._child_pid, signal.SIGKILL)
                        os.waitpid(self._child_pid, 0)
                    except Exception:
                        pass
            self._master_fd = None
            self._child_pid = None
            _logger.info(f"Unix PTY 会话已关闭, PID={pid}")

    @property
    def is_alive(self):
        if self._closed:
            return False
        if IS_WINDOWS and self._winpty:
            return self._winpty.isalive()
        if self._child_pid:
            try:
                pid, status = os.waitpid(self._child_pid, os.WNOHANG)
                return pid == 0
            except ChildProcessError:
                return False
        return False


@router.websocket("/ws")
async def terminal_ws(websocket: WebSocket, node_id: str = Query(default="")):
    """
    WebSocket 终端端点。

    前端通过 WebSocket 连接此端点，建立持久 PTY shell 会话。
    - 前端发送纯文本 → 写入 PTY stdin（用户按键输入）
    - 前端发送 JSON → 控制消息（如 resize）
    - 后端推送 PTY stdout 输出 → 前端显示
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

    # 本机 — 启动 PTY shell 会话
    shell = ShellSession()
    try:
        shell.start(cols=80, rows=24)
    except Exception as e:
        _logger.error(f"启动 PTY 失败: {e}")
        await websocket.send_text(f"\r\n\x1b[31m启动终端失败: {e}\x1b[0m\r\n")
        await websocket.close(code=4500, reason="Shell start failed")
        return

    loop = asyncio.get_event_loop()

    # 在线程中读取 PTY 输出并推送到 WebSocket
    async def send_output(data: bytes):
        """将 PTY 输出发送到 WebSocket"""
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

    # 主循环：从 WebSocket 接收输入，写入 PTY
    try:
        while True:
            try:
                data = await websocket.receive_text()

                # 检查是否是控制消息（JSON 格式，以 { 开头）
                if data.startswith("{"):
                    try:
                        msg = json.loads(data)
                        msg_type = msg.get("type", "")
                        if msg_type == "resize":
                            cols = msg.get("cols", 80)
                            rows = msg.get("rows", 24)
                            shell.resize(cols, rows)
                        # 其他控制消息可在此扩展
                        continue
                    except (json.JSONDecodeError, KeyError):
                        pass  # 不是有效 JSON，当作普通输入

                # 普通用户输入 → 写入 PTY
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
