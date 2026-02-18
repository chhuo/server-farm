"""
API v1 路由汇总
"""

from fastapi import APIRouter

from api.v1.system import router as system_router
from api.v1.peer import router as peer_router
from api.v1.nodes import router as nodes_router

router = APIRouter(prefix="/api/v1")

# 注册子路由
router.include_router(system_router)
router.include_router(peer_router)
router.include_router(nodes_router)
