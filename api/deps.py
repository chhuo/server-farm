"""
API 依赖注入模块

提供 FastAPI 依赖项，让路由函数可以获取 config 和 logger。
"""

from core.config import ConfigManager
from core.logger import get_logger

import logging

# 全局引用，由 main.py 启动时设置
_config: ConfigManager | None = None
_logger: logging.Logger | None = None


def init_deps(config: ConfigManager, logger: logging.Logger):
    """初始化依赖（由 main.py 在启动时调用）"""
    global _config, _logger
    _config = config
    _logger = logger


def get_config() -> ConfigManager:
    """获取全局配置"""
    if _config is None:
        raise RuntimeError("依赖未初始化，请先调用 init_deps()")
    return _config


def get_app_logger(name: str | None = None) -> logging.Logger:
    """获取 Logger 实例"""
    return get_logger(name)
