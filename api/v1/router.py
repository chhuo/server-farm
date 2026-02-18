"""
API v1 路由汇总
"""

from fastapi import APIRouter

from api.v1.system import router as system_router

router = APIRouter(prefix="/api/v1")

# 注册子路由
router.include_router(system_router)
