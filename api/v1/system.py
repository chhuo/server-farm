"""
系统信息 API

提供本机系统信息查询接口。
"""

from fastapi import APIRouter, Request

from services.collector import collect_system_info

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/branding")
async def get_branding(request: Request):
    """获取面板品牌信息（无需认证，供登录页等使用）"""
    config = request.app.state.config
    return {
        "name": config.get("app.name", "NodePanel"),
        "version": config.get("app.version", "0.1.0"),
    }


@router.get("/info")
async def get_system_info():
    """获取本机系统信息（CPU/内存/磁盘/网络）"""
    return collect_system_info()
