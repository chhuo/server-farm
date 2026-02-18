"""
MyServer — 应用入口

使用 bootstrap 引导加载器同步初始化 Config 和 Logger。
"""

from core import bootstrap
from core.logger import get_logger


def main():
    # 引导加载：初始化配置系统和日志系统
    config, logger = bootstrap.init()

    # 获取子模块 Logger 的示例
    app_logger = get_logger("main")
    app_logger.info(f"应用 [{config.get('app.name')}] v{config.get('app.version')} 启动成功")
    app_logger.info(f"运行环境: {config.get('app.env')}")
    app_logger.info(f"监听地址: {config.get('server.host')}:{config.get('server.port')}")

    # 演示各级别日志输出
    app_logger.debug("这是一条 DEBUG 日志")
    app_logger.info("这是一条 INFO 日志")
    app_logger.warning("这是一条 WARNING 日志")
    app_logger.error("这是一条 ERROR 日志")
    app_logger.critical("这是一条 CRITICAL 日志")

    app_logger.info("系统初始化完毕，准备开始业务逻辑...")


if __name__ == "__main__":
    main()
