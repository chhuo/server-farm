"""
API 依赖注入模块

提供 FastAPI 依赖项辅助函数。

注意：当前大部分服务通过 request.app.state.xxx 直接访问，
本模块保留 get_logger 的快捷封装供路由使用。
"""

from core.logger import get_logger


def get_app_logger(name: str = None):
    """获取 Logger 实例的快捷方式"""
    return get_logger(name)
