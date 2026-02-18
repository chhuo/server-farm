"""
系统信息 API

提供本机系统信息查询接口。
"""

from fastapi import APIRouter

from services.collector import collect_system_info

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/info")
async def get_system_info():
    """获取本机系统信息（CPU/内存/磁盘/网络）"""
    return collect_system_info()
