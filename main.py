"""
NodePanel — 分布式控制台面板入口

使用 bootstrap 初始化 Config + Logger，然后启动 FastAPI 服务。
"""

import os
import sys
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from core import bootstrap
from core.logger import get_logger
from api.deps import init_deps
from api.v1.router import router as v1_router


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用"""

    # 引导加载
    config, logger = bootstrap.init()

    # 初始化 API 依赖
    init_deps(config, logger)

    app_logger = get_logger("main")

    # 创建 FastAPI 实例
    app = FastAPI(
        title=config.get("app.name"),
        version=config.get("app.version"),
        docs_url="/api/docs" if config.get("app.debug") else None,
        redoc_url=None,
    )

    # 注册 API 路由
    app.include_router(v1_router)

    # 静态文件服务
    web_dir = os.path.join(config.project_root, "web")
    if os.path.isdir(web_dir):
        # CSS 和 JS 静态文件
        css_dir = os.path.join(web_dir, "css")
        js_dir = os.path.join(web_dir, "js")

        if os.path.isdir(css_dir):
            app.mount("/css", StaticFiles(directory=css_dir), name="css")
        if os.path.isdir(js_dir):
            app.mount("/js", StaticFiles(directory=js_dir), name="js")

        # SPA 入口 — 所有非 API/静态资源请求返回 index.html
        index_path = os.path.join(web_dir, "index.html")

        @app.get("/")
        async def serve_index():
            return FileResponse(index_path)

        # 捕获其他路径，返回 index.html（支持 SPA 刷新）
        @app.get("/{path:path}")
        async def serve_spa(path: str):
            # 排除 API 和静态资源路径
            if path.startswith(("api/", "css/", "js/")):
                return None
            return FileResponse(index_path)

    app_logger.info(f"FastAPI 应用创建完成: {config.get('app.name')} v{config.get('app.version')}")

    # 把 config 挂载到 app.state 方便后续访问
    app.state.config = config

    return app


# 创建应用实例（uvicorn 需要模块级别的 app 变量）
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
