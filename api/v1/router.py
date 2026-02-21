"""
API v1 路由汇总
"""

from fastapi import APIRouter

from api.v1.system import router as system_router
from api.v1.peer import router as peer_router
from api.v1.nodes import router as nodes_router
from api.v1.tasks import router as tasks_router
from api.v1.auth import router as auth_router
from api.v1.config_api import router as config_router
from api.v1.chat import router as chat_router
from api.v1.snippets import router as snippets_router
from api.v1.terminal import router as terminal_router

router = APIRouter(prefix="/api/v1")

# 注册子路由
router.include_router(system_router)
router.include_router(peer_router)
router.include_router(nodes_router)
router.include_router(tasks_router)
router.include_router(auth_router)
router.include_router(config_router)
router.include_router(chat_router)
router.include_router(snippets_router)
router.include_router(terminal_router)
