"""
配置管理器模块

提供：
- YAML 文件加载（默认 + 自定义路径）
- 环境变量覆盖（APP_ 前缀，双下划线表示层级）
- 命令行参数支持（--config 指定配置文件）
- 深度合并（默认 < YAML < 环境变量 < CLI）
- 点号路径访问（config.get("server.host")）
- 冻结锁定（防止意外修改）
- 配置验证
"""

import argparse
import copy
import logging
import os
import sys
from typing import Any, Optional

# YAML 为可选依赖，在函数内延迟导入以支持更好的错误提示
_yaml_available = False
try:
    import yaml
    _yaml_available = True
except ImportError:
    pass


# 环境变量前缀
_ENV_PREFIX = "APP_"
# 环境变量中表示嵌套层级的分隔符
_ENV_SEPARATOR = "__"


# ──────────────────────────────────────────────
# 内置默认配置（当 YAML 文件缺失时作为回退）
# ──────────────────────────────────────────────

_BUILTIN_DEFAULTS: dict[str, Any] = {
    "app": {
        "name": "NodePanel",
        "version": "0.1.0",
        "env": "development",
        "debug": True,
    },
    "server": {
        "host": "0.0.0.0",
        "port": 8300,
    },
    "node": {
        "id": "",
        "name": "",
        "mode": "auto",
        "primary_server": "",
        "public_url": "",
    },
    "peer": {
        "sync_interval": 30,
        "heartbeat_interval": 10,
        "timeout": 10,
        "max_fanout": 3,
        "max_heartbeat_failures": 3,
    },
    "security": {
        "node_key": "",
        "admin_user": "admin",
        "admin_password": "",
        "command_blacklist": [
            "rm -rf /",
            "mkfs",
            "dd if=/dev/zero",
        ],
    },
    "logging": {
        "level": "DEBUG",
        "console": {
            "enabled": True,
            "colorize": True,
        },
        "file": {
            "enabled": True,
            "directory": "logs",
            "max_size_mb": 10,
            "backup_count": 5,
            "app_log": "app.log",
            "error_log": "error.log",
        },
        "format": "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s",
    },
}


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

def _deep_merge(base: dict, override: dict) -> dict:
    """
    深度合并两个字典。override 中的值会覆盖 base 中的值。
    对于嵌套字典会递归合并，而非直接替换。
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _parse_env_value(value: str) -> Any:
    """
    尝试将环境变量的字符串值解析为合适的 Python 类型。
    支持 bool、int、float、None。
    """
    # bool
    if value.lower() in ("true", "yes", "1", "on"):
        return True
    if value.lower() in ("false", "no", "0", "off"):
        return False
    # None
    if value.lower() in ("null", "none", ""):
        return None
    # int
    try:
        return int(value)
    except ValueError:
        pass
    # float
    try:
        return float(value)
    except ValueError:
        pass
    # 保持字符串
    return value


def _set_nested(data: dict, keys: list[str], value: Any):
    """在嵌套字典中按键路径设置值"""
    for key in keys[:-1]:
        if key not in data or not isinstance(data[key], dict):
            data[key] = {}
        data = data[key]
    data[keys[-1]] = value


def _get_nested(data: dict, keys: list[str], default: Any = None) -> Any:
    """在嵌套字典中按键路径获取值"""
    current = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


# ──────────────────────────────────────────────
# 配置管理器
# ──────────────────────────────────────────────

class ConfigManager:
    """
    配置管理器。

    加载优先级（从低到高）：
    1. 内置默认值
    2. YAML 配置文件
    3. 环境变量（APP_ 前缀）
    4. 命令行参数

    使用方式：
        config = ConfigManager(logger=temp_logger)
        config.load()
        host = config.get("server.host")
        config.freeze()
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        self._data: dict[str, Any] = {}
        self._frozen = False
        self._logger = logger or logging.getLogger(__name__)
        self._config_file_path: Optional[str] = None
        self._project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def load(self, config_path: Optional[str] = None) -> "ConfigManager":
        """
        按优先级加载配置。

        Args:
            config_path: 可选的配置文件路径。如果不传，
                         会从命令行参数 --config 中读取，
                         或使用默认路径 config/default.yaml。

        Returns:
            self（支持链式调用）
        """
        self._logger.info("=" * 60)
        self._logger.info("开始加载配置系统")
        self._logger.info("=" * 60)

        # Step 1: 内置默认值
        self._data = copy.deepcopy(_BUILTIN_DEFAULTS)
        self._logger.debug("已加载内置默认配置")

        # Step 2: 解析命令行参数（获取 --config 路径）
        cli_config_path = self._parse_cli_args()

        # 确定最终配置文件路径
        final_path = config_path or cli_config_path
        if final_path is None:
            final_path = os.path.join(self._project_root, "config.yaml")

        self._config_file_path = os.path.abspath(final_path)

        # Step 3: 加载 YAML 文件
        self._load_yaml(self._config_file_path)

        # Step 4: 环境变量覆盖
        self._load_env_overrides()

        # 打印最终生效的关键配置
        self._log_effective_config()

        self._logger.info("配置系统加载完成")
        return self

    def _parse_cli_args(self) -> Optional[str]:
        """解析命令行参数，返回 --config 路径（如果有）"""
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--config", "-c", type=str, default=None,
                            help="自定义配置文件路径")
        args, _ = parser.parse_known_args()

        if args.config:
            self._logger.info(f"命令行指定配置文件: {args.config}")
            return args.config

        return None

    def _load_yaml(self, path: str):
        """从 YAML 文件加载配置"""
        if not _yaml_available:
            self._logger.warning(
                "PyYAML 未安装，无法加载 YAML 配置文件。"
                "请运行: pip install PyYAML"
            )
            return

        if not os.path.isfile(path):
            self._logger.info(f"配置文件不存在，正在创建默认配置: {path}")
            try:
                import yaml as _yaml
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as _f:
                    _yaml.dump(
                        copy.deepcopy(_BUILTIN_DEFAULTS),
                        _f,
                        default_flow_style=False,
                        allow_unicode=True,
                        sort_keys=False,
                    )
                self._logger.info(f"已生成默认配置文件: {path}")
            except Exception as _e:
                self._logger.warning(f"生成默认配置文件失败: {_e}，将使用内置默认配置")
            return

        self._logger.info(f"正在加载配置文件: {path}")
        try:
            with open(path, "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f)

            if yaml_data is None:
                self._logger.warning("配置文件为空，使用内置默认配置")
                return

            if not isinstance(yaml_data, dict):
                self._logger.error(f"配置文件格式错误（期望字典，得到 {type(yaml_data).__name__}）")
                return

            self._data = _deep_merge(self._data, yaml_data)
            self._logger.info(f"已合并 YAML 配置（{len(yaml_data)} 个顶级键）")

            for key in yaml_data:
                self._logger.debug(f"  YAML 配置段: {key}")

        except yaml.YAMLError as e:
            self._logger.error(f"YAML 解析失败: {e}")
            self._logger.warning("将使用内置默认配置继续运行")
        except OSError as e:
            self._logger.error(f"读取配置文件失败: {e}")

    def _load_env_overrides(self):
        """从环境变量加载覆盖配置"""
        overrides_count = 0

        for key, value in sorted(os.environ.items()):
            if not key.startswith(_ENV_PREFIX):
                continue

            # 去掉前缀，转小写，按 __ 分割为路径
            config_key = key[len(_ENV_PREFIX):].lower()
            parts = config_key.split(_ENV_SEPARATOR.lower())

            parsed_value = _parse_env_value(value)
            _set_nested(self._data, parts, parsed_value)
            self._logger.debug(f"环境变量覆盖: {'.'.join(parts)} = {parsed_value!r}")
            overrides_count += 1

        if overrides_count > 0:
            self._logger.info(f"已应用 {overrides_count} 个环境变量覆盖")
        else:
            self._logger.debug("未检测到 APP_ 前缀的环境变量")

    def _log_effective_config(self):
        """打印最终生效的关键配置"""
        self._logger.info("-" * 40)
        self._logger.info("当前生效配置:")
        self._logger.info(f"  应用名称:   {self.get('app.name')}")
        self._logger.info(f"  版本:       {self.get('app.version')}")
        self._logger.info(f"  环境:       {self.get('app.env')}")
        self._logger.info(f"  调试模式:   {self.get('app.debug')}")
        self._logger.info(f"  服务地址:   {self.get('server.host')}:{self.get('server.port')}")
        self._logger.info(f"  日志级别:   {self.get('logging.level')}")
        self._logger.info(f"  配置文件:   {self._config_file_path}")
        self._logger.info("-" * 40)

    # ──────────────────────────────────────────
    # 公共 API
    # ──────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """
        按点号路径获取配置值。

        Args:
            key: 点号分隔的配置路径，如 "server.host"
            default: 键不存在时的默认值

        Returns:
            配置值

        Examples:
            config.get("server.port")        # -> 8000
            config.get("app.name")           # -> "MyServer"
            config.get("db.host", "localhost")  # -> "localhost" (不存在时)
        """
        parts = key.split(".")
        return _get_nested(self._data, parts, default)

    def set(self, key: str, value: Any):
        """
        按点号路径设置配置值。

        Args:
            key: 点号分隔的配置路径
            value: 要设置的值

        Raises:
            RuntimeError: 配置已冻结时
        """
        if self._frozen:
            raise RuntimeError(f"配置已冻结，无法修改: {key}")
        parts = key.split(".")
        _set_nested(self._data, parts, value)

    def freeze(self):
        """冻结配置，之后的 set() 调用将抛出 RuntimeError"""
        self._frozen = True
        self._logger.debug("配置已冻结，不再允许修改")

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def to_dict(self) -> dict[str, Any]:
        """返回配置的深拷贝字典"""
        return copy.deepcopy(self._data)

    def save_to_yaml(self, path: Optional[str] = None):
        """
        将当前配置保存到 YAML 文件。

        Args:
            path: 可选的保存路径，默认使用加载时的配置文件路径

        Raises:
            RuntimeError: 如果 PyYAML 不可用或保存失败
        """
        if not _yaml_available:
            raise RuntimeError("PyYAML 未安装，无法保存配置文件")

        save_path = path or self._config_file_path
        if not save_path:
            save_path = os.path.join(self._project_root, "config.yaml")

        try:
            # 读取现有文件内容（保留未在内存中管理的额外字段）
            current = {}
            if os.path.isfile(save_path):
                with open(save_path, "r", encoding="utf-8") as f:
                    current = yaml.safe_load(f) or {}

            # 用内存中的配置覆盖各段
            sections = ["app", "server", "node", "peer", "security", "logging"]
            for section in sections:
                section_data = self.get(section)
                if isinstance(section_data, dict):
                    current[section] = copy.deepcopy(section_data)

            # YAML 文件头注释
            header = (
                "# ============================================================\n"
                "# NodePanel 配置文件\n"
                "# ============================================================\n"
                "# 加载优先级（从低到高）：\n"
                "#   1. 内置默认值（代码中硬编码）\n"
                "#   2. 本文件 (config.yaml)\n"
                "#   3. 环境变量（APP_ 前缀，双下划线 __ 表示层级）\n"
                "#   4. 命令行参数（--config 指定的自定义文件）\n"
                "# ============================================================\n\n"
            )

            os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(header)
                yaml.dump(
                    current, f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )

            self._logger.debug(f"配置已保存到 {save_path}")
        except Exception as e:
            self._logger.error(f"配置保存失败: {e}")
            raise RuntimeError(f"配置保存失败: {e}") from e

    @property
    def config_file_path(self) -> Optional[str]:
        """返回实际使用的配置文件路径"""
        return self._config_file_path

    @property
    def project_root(self) -> str:
        """返回项目根目录路径"""
        return self._project_root

    def __repr__(self) -> str:
        status = "frozen" if self._frozen else "mutable"
        return f"<ConfigManager({status}, keys={list(self._data.keys())})>"
