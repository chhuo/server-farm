"""
引导加载器模块

解决 Config 与 Logger 之间的循环依赖（鸡与蛋问题）：
1. 创建临时 Logger（仅 stderr，DEBUG）
2. 使用临时 Logger 加载 Config
3. 用 Config 中的 logging 配置重新配置正式 Logger
4. 返回 (config, logger)
"""

import logging
from typing import Optional

from core.config import ConfigManager
from core.logger import create_temporary_logger, get_logger, reconfigure_logger


def init(config_path: Optional[str] = None) -> tuple["ConfigManager", logging.Logger]:
    """
    初始化整个应用的基础设施。

    Args:
        config_path: 可选的配置文件路径

    Returns:
        (config, logger) 元组
    """

    # ── Phase 1: 临时 Logger ──
    temp_logger = create_temporary_logger()
    temp_logger.info("引导加载器启动")
    temp_logger.debug("Phase 1: 临时日志系统已就绪")

    # ── Phase 2: 加载配置 ──
    temp_logger.debug("Phase 2: 开始加载配置系统")
    config = ConfigManager(logger=temp_logger)
    config.load(config_path=config_path)

    # ── Phase 3: 重新配置正式 Logger ──
    temp_logger.debug("Phase 3: 正在用配置重新初始化日志系统")
    logging_config = config.get("logging", {})
    logger = reconfigure_logger(logging_config)

    logger.info("=" * 60)
    logger.info("日志系统已切换到正式模式")
    logger.info(f"日志级别: {logging_config.get('level', 'DEBUG')}")
    logger.info(f"控制台输出: {'启用' if logging_config.get('console', {}).get('enabled', True) else '禁用'}")
    logger.info(f"文件日志: {'启用' if logging_config.get('file', {}).get('enabled', True) else '禁用'}")
    logger.info("=" * 60)

    logger.info("系统初始化完成")

    return config, logger
