"""
日志工具模块

基于 Python 标准 logging 模块，提供：
- 彩色控制台输出（无第三方依赖）
- 文件日志 + 自动轮转
- 错误日志分离（error.log 仅记录 ERROR+）
- 临时模式（Bootstrap 阶段使用）
- 重配置（Config 加载后用正式配置接管）
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional


# ──────────────────────────────────────────────
# 彩色输出 Formatter（支持 Windows Terminal / ANSI）
# ──────────────────────────────────────────────

class _ColorCode:
    """ANSI 色彩码常量"""
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"

    # 前景色
    RED     = "\033[31m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    BLUE    = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN    = "\033[36m"
    WHITE   = "\033[37m"

    # 亮色
    BRIGHT_RED    = "\033[91m"
    BRIGHT_GREEN  = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_CYAN   = "\033[96m"
    BRIGHT_WHITE  = "\033[97m"

    # 背景色
    BG_RED = "\033[41m"


# 级别 → 颜色映射
_LEVEL_COLORS: dict[int, str] = {
    logging.DEBUG:    _ColorCode.BRIGHT_CYAN,
    logging.INFO:     _ColorCode.BRIGHT_GREEN,
    logging.WARNING:  _ColorCode.BRIGHT_YELLOW,
    logging.ERROR:    _ColorCode.BRIGHT_RED,
    logging.CRITICAL: _ColorCode.BG_RED + _ColorCode.BRIGHT_WHITE + _ColorCode.BOLD,
}


class ColoredFormatter(logging.Formatter):
    """
    为控制台输出添加 ANSI 颜色的 Formatter。
    仅着色级别名称和消息文本，时间戳与位置信息保持灰白色以提升可读性。
    """

    def __init__(self, fmt: Optional[str] = None, datefmt: Optional[str] = None,
                 colorize: bool = True):
        super().__init__(fmt=fmt, datefmt=datefmt)
        self._colorize = colorize

    def format(self, record: logging.LogRecord) -> str:
        if not self._colorize:
            return super().format(record)

        # 保存原始值
        orig_levelname = record.levelname
        orig_msg = record.msg

        color = _LEVEL_COLORS.get(record.levelno, _ColorCode.WHITE)

        # 着色级别名
        record.levelname = f"{color}{record.levelname:<8}{_ColorCode.RESET}"

        # 着色消息
        record.msg = f"{color}{record.msg}{_ColorCode.RESET}"

        # 格式化
        result = super().format(record)

        # 恢复原始值（避免影响其他 Handler）
        record.levelname = orig_levelname
        record.msg = orig_msg

        return result


# ──────────────────────────────────────────────
# 日志管理器
# ──────────────────────────────────────────────

# 默认日志格式
_DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s"
_DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 模块级别的根 Logger 名称
_ROOT_LOGGER_NAME = "app"

# 全局日志管理器实例
_manager: Optional["LogManager"] = None


class LogManager:
    """
    日志管理器：管理所有 Handler 的生命周期。

    支持两个阶段：
    1. 临时阶段（create_temporary）：仅 stderr 输出，用于 Bootstrap
    2. 正式阶段（reconfigure）：根据 Config 设置完整的 Handler
    """

    def __init__(self):
        self._root_logger = logging.getLogger(_ROOT_LOGGER_NAME)
        self._handlers: list[logging.Handler] = []
        self._configured = False

    @property
    def logger(self) -> logging.Logger:
        return self._root_logger

    def _clear_handlers(self):
        """移除所有已注册的 Handler"""
        for handler in self._handlers:
            self._root_logger.removeHandler(handler)
            handler.close()
        self._handlers.clear()

    def setup_temporary(self) -> logging.Logger:
        """
        设置临时 Logger。仅输出到 stderr，DEBUG 级别。
        用于 Bootstrap 阶段，在 Config 加载之前记录日志。
        """
        self._clear_handlers()

        self._root_logger.setLevel(logging.DEBUG)

        # stderr Handler - 使用简化格式
        temp_format = "%(asctime)s | %(levelname)-8s | [BOOT] %(message)s"
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(ColoredFormatter(
            fmt=temp_format,
            datefmt=_DEFAULT_DATE_FORMAT,
            colorize=True,
        ))

        self._root_logger.addHandler(handler)
        self._handlers.append(handler)

        return self._root_logger

    def reconfigure(self, config: dict) -> logging.Logger:
        """
        使用配置字典重新配置 Logger。

        Args:
            config: logging 配置段，结构参见 default.yaml

        Returns:
            配置完成的 Logger 实例
        """
        self._clear_handlers()

        # 解析配置
        level_str = config.get("level", "DEBUG").upper()
        level = getattr(logging, level_str, logging.DEBUG)
        self._root_logger.setLevel(level)

        log_format = config.get("format", _DEFAULT_FORMAT)

        # ── Console Handler ──
        console_cfg = config.get("console", {})
        if console_cfg.get("enabled", True):
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(level)
            console_handler.setFormatter(ColoredFormatter(
                fmt=log_format,
                datefmt=_DEFAULT_DATE_FORMAT,
                colorize=console_cfg.get("colorize", True),
            ))
            self._root_logger.addHandler(console_handler)
            self._handlers.append(console_handler)

        # ── File Handlers ──
        file_cfg = config.get("file", {})
        if file_cfg.get("enabled", True):
            log_dir = file_cfg.get("directory", "logs")

            # 确保日志目录存在（相对路径基于项目根目录）
            if not os.path.isabs(log_dir):
                # 获取项目根目录（core/ 的上级目录）
                project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                log_dir = os.path.join(project_root, log_dir)

            os.makedirs(log_dir, exist_ok=True)

            max_bytes = file_cfg.get("max_size_mb", 10) * 1024 * 1024
            backup_count = file_cfg.get("backup_count", 5)
            file_formatter = logging.Formatter(
                fmt=log_format,
                datefmt=_DEFAULT_DATE_FORMAT,
            )

            # app.log - 记录所有级别
            app_log_name = file_cfg.get("app_log", "app.log")
            app_log_path = os.path.join(log_dir, app_log_name)
            app_handler = RotatingFileHandler(
                app_log_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            app_handler.setLevel(level)
            app_handler.setFormatter(file_formatter)
            self._root_logger.addHandler(app_handler)
            self._handlers.append(app_handler)

            # error.log - 仅记录 ERROR 及以上
            error_log_name = file_cfg.get("error_log", "error.log")
            error_log_path = os.path.join(log_dir, error_log_name)
            error_handler = RotatingFileHandler(
                error_log_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            error_handler.setLevel(logging.ERROR)
            error_handler.setFormatter(file_formatter)
            self._root_logger.addHandler(error_handler)
            self._handlers.append(error_handler)

        self._configured = True
        return self._root_logger

    @property
    def is_configured(self) -> bool:
        return self._configured


def _get_manager() -> "LogManager":
    """获取或创建全局 LogManager 实例"""
    global _manager
    if _manager is None:
        _manager = LogManager()
    return _manager


# ──────────────────────────────────────────────
# 公共 API
# ──────────────────────────────────────────────

def create_temporary_logger() -> logging.Logger:
    """
    创建临时 Logger（Bootstrap 阶段使用）。
    仅输出到 stderr，DEBUG 级别，带 [BOOT] 前缀。
    """
    return _get_manager().setup_temporary()


def reconfigure_logger(config: dict) -> logging.Logger:
    """
    使用配置字典重新配置 Logger。
    会清除临时 Handler，按照配置设置正式的 Console/File Handler。

    Args:
        config: logging 配置段
    """
    return _get_manager().reconfigure(config)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    获取一个子 Logger。

    Args:
        name: 子 Logger 名称。传入模块名即可，
              会自动挂载到 app 根 Logger 下（如 app.core.config）。

    Returns:
        Logger 实例
    """
    if name is None:
        return logging.getLogger(_ROOT_LOGGER_NAME)
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")
