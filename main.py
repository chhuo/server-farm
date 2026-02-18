"""
NodePanel — 分布式控制台面板入口

使用 bootstrap 初始化 Config + Logger，然后启动 FastAPI 服务。
集成节点身份、存储引擎、Peer 同步的完整生命周期。
"""

import os
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
