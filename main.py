"""
NodePanel — 分布式控制台面板入口

使用 bootstrap 初始化 Config + Logger，然后启动 FastAPI 服务。
集成节点身份、存储引擎、Peer 同步的完整生命周期。
"""

import os
import socket
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from core import bootstrap
from core.logger import get_logger
from core.node import NodeIdentity
from api.deps import init_deps
from api.v1.router import router as v1_router
from services.storage import FileStore
from services.peer_service import PeerService
from services.audit import AuditService
from services.task_service import TaskService
from services.auth import AuthService


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用"""

    # ── Phase 1: 引导加载 ──
    config, logger = bootstrap.init()
    init_deps(config, logger)
    app_logger = get_logger("main")

    # ── Phase 2: 存储 + 节点身份 ──
    data_dir = os.path.join(config.project_root, "data")
    storage = FileStore(data_dir)
    storage.ensure_subdir("tasks")
    storage.ensure_subdir("audit")
    # 确保聊天和片段数据文件存在
    if not storage.exists("chat.json"):
        storage.write("chat.json", [])
    if not storage.exists("snippets.json"):
        storage.write("snippets.json", [])

    node_identity = NodeIdentity(config, storage)
    node_identity.initialize()

    # ── Phase 3: 审计 + 任务 ──
    audit_service = AuditService(storage)
    task_service = TaskService(node_identity, storage, config, audit_service)

    # Peer 同步服务（传入 task_service 用于心跳任务转发）
    peer_service = PeerService(node_identity, storage, config, task_service)

    # ── Phase 4: 认证 ──
    auth_service = AuthService(config, storage)

    # ── 生命周期管理 ──
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """应用启动/关闭生命周期"""
        app_logger.info("正在启动后台服务...")

        # 更新自身状态
        await peer_service._update_self_state()

        # 启动 Peer 同步
        await peer_service.start()

        app_logger.info(f"NodePanel 就绪 [{node_identity.mode.value} 模式]")

        # 打印就绪 banner
        _print_ready_banner(config, node_identity, auth_service)

        yield

        # 关闭
        app_logger.info("正在停止后台服务...")
        await peer_service.stop()

    # ── 创建 FastAPI 实例 ──
    app = FastAPI(
        title=config.get("app.name"),
        version=config.get("app.version"),
        docs_url="/api/docs" if config.get("app.debug") else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # 全局状态挂载
    app.state.config = config
    app.state.storage = storage
    app.state.node_identity = node_identity
    app.state.peer_service = peer_service
    app.state.audit_service = audit_service
    app.state.task_service = task_service
    app.state.auth_service = auth_service

    # ── 认证中间件 ──
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class AuthMiddleware(BaseHTTPMiddleware):
        # 不需要认证的 API 路径前缀
        EXEMPT_PREFIXES = (
            "/api/v1/auth/",
            "/api/v1/peer/",
            "/api/v1/system/info",
            "/api/v1/nodes/self",
        )

        async def dispatch(self, request, call_next):
            path = request.url.path

            # 静态资源和 SPA 页面 — 免认证
            if not path.startswith("/api/"):
                return await call_next(request)

            # 免认证 API 路径
            for exempt in self.EXEMPT_PREFIXES:
                if path == exempt or path.startswith(exempt):
                    return await call_next(request)

            # 检查 Token
            token = request.cookies.get("token", "")
            session = auth_service.validate_token(token)

            if not session:
                return JSONResponse(
                    status_code=401,
                    content={"error": "未登录或会话已过期"},
                )

            return await call_next(request)

    app.add_middleware(AuthMiddleware)

    # ── 注册 API 路由 ──
    app.include_router(v1_router)

    # ── 静态文件服务 ──
    web_dir = os.path.join(config.project_root, "web")
    if os.path.isdir(web_dir):
        css_dir = os.path.join(web_dir, "css")
        js_dir = os.path.join(web_dir, "js")

        if os.path.isdir(css_dir):
            app.mount("/css", StaticFiles(directory=css_dir), name="css")
        if os.path.isdir(js_dir):
            app.mount("/js", StaticFiles(directory=js_dir), name="js")

        index_path = os.path.join(web_dir, "index.html")

        @app.get("/")
        async def serve_index():
            return FileResponse(index_path)

        @app.get("/{path:path}")
        async def serve_spa(path: str):
            if path.startswith(("api/", "css/", "js/")):
                return None
            return FileResponse(index_path)

    app_logger.info(
        f"FastAPI 应用创建完成: {config.get('app.name')} v{config.get('app.version')} "
        f"[{node_identity.mode.value}]"
    )

    return app


def _print_ready_banner(config, node_identity, auth_service):
    """在所有启动日志之后打印醒目的就绪信息"""
    host = config.get("server.host", "0.0.0.0")
    port = config.get("server.port", 8300)

    # 获取实际可访问的 IP
    if host in ("0.0.0.0", ""):
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            local_ip = "127.0.0.1"
    else:
        local_ip = host

    # ANSI 颜色（Windows 10+ 和所有 Linux/macOS 终端支持）
    CYAN  = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BOLD  = "\033[1m"
    RESET = "\033[0m"

    mode = node_identity.mode.value
    name = config.get("app.name", "NodePanel")
    version = config.get("app.version", "")

    lines = [
        f"{CYAN}{'═' * 52}{RESET}",
        f"{CYAN}  {BOLD}{name} v{version}{RESET}{CYAN}  已就绪{RESET}",
        f"{CYAN}{'─' * 52}{RESET}",
        f"  {GREEN}访问地址{RESET}  http://{local_ip}:{port}",
        f"  {GREEN}本机回环{RESET}  http://127.0.0.1:{port}",
        f"  {GREEN}节点模式{RESET}  {mode}",
    ]

    # 首次启动时显示初始密码
    if auth_service.is_setup_required():
        auth_data = auth_service._storage.read("auth.json", {})
        init_user = auth_data.get("admin_user", "admin")
        init_pass = auth_service.get_initial_password()
        lines += [
            f"{CYAN}{'─' * 52}{RESET}",
            f"  {YELLOW}⚠ 初始账号{RESET}  {init_user}",
            f"  {YELLOW}⚠ 初始密码{RESET}  {init_pass}",
            f"  {YELLOW}  请登录后及时修改密码！{RESET}",
        ]

    lines.append(f"{CYAN}{'═' * 52}{RESET}")

    print("\n" + "\n".join(lines) + "\n", flush=True)


# 创建应用实例
app = create_app()


if __name__ == "__main__":
    config = app.state.config
    uvicorn.run(
        "main:app",
        host=config.get("server.host"),
        port=config.get("server.port"),
        reload=config.get("app.debug", False),
        log_level="info",
    )
