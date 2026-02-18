"""
文件存储引擎

提供原子性的 JSON 文件读写，支持文件锁防止并发冲突。
所有节点（包括 Relay）都会持久化 nodes.json 和 states.json。
"""

import json
import os
import tempfile
import threading
from typing import Any, Optional

from core.logger import get_logger

_logger = get_logger("services.storage")


class FileStore:
    """
    线程安全的 JSON 文件存储。

    特性：
    - 原子写入：先写临时文件，再重命名（防止写入中断导致数据损坏）
    - 线程锁：防止多线程并发写入冲突
    - 自动创建目录
    """

    def __init__(self, data_dir: str):
        self._data_dir = os.path.abspath(data_dir)
        self._locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

        # 确保数据目录存在
        os.makedirs(self._data_dir, exist_ok=True)
        _logger.info(f"文件存储引擎初始化: {self._data_dir}")

    def _get_lock(self, filename: str) -> threading.Lock:
        """获取指定文件名的锁"""
        with self._global_lock:
            if filename not in self._locks:
                self._locks[filename] = threading.Lock()
            return self._locks[filename]

    def _filepath(self, filename: str) -> str:
        """获取完整文件路径"""
        return os.path.join(self._data_dir, filename)

    def read(self, filename: str, default: Any = None) -> Any:
        """
        读取 JSON 文件内容。

        Args:
            filename: 文件名（如 'nodes.json'）
            default: 文件不存在时的默认值

        Returns:
            解析后的 Python 对象
        """
        filepath = self._filepath(filename)

        if not os.path.isfile(filepath):
            return default if default is not None else {}

        lock = self._get_lock(filename)
        with lock:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data
            except (json.JSONDecodeError, OSError) as e:
                _logger.error(f"读取文件失败 [{filename}]: {e}")
                return default if default is not None else {}

    def write(self, filename: str, data: Any) -> bool:
        """
        原子性写入 JSON 文件。

        先写入临时文件，成功后再重命名覆盖目标文件。
        如果在写入过程中崩溃，原始文件不会受到影响。

        Args:
            filename: 文件名
            data: 要写入的数据

        Returns:
            是否成功
        """
        filepath = self._filepath(filename)
        lock = self._get_lock(filename)

        with lock:
            try:
                # 写入临时文件
                dir_path = os.path.dirname(filepath)
                fd, tmp_path = tempfile.mkstemp(
                    dir=dir_path, suffix=".tmp", prefix=f".{filename}_"
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)

                    # 原子重命名（在同一文件系统上）
                    # Windows 上需要先删除目标文件
                    if os.path.exists(filepath):
                        os.replace(tmp_path, filepath)
                    else:
                        os.rename(tmp_path, filepath)

                except Exception:
                    # 清理临时文件
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
                    raise

                return True
            except OSError as e:
                _logger.error(f"写入文件失败 [{filename}]: {e}")
                return False

    def update(self, filename: str, updater, default: Any = None) -> Any:
        """
        读取-修改-写回 的原子操作。

        Args:
            filename: 文件名
            updater: 回调函数 (data) -> modified_data
            default: 文件不存在时的默认值

        Returns:
            修改后的数据
        """
        lock = self._get_lock(filename)
        with lock:
            # 读
            filepath = self._filepath(filename)
            if os.path.isfile(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except (json.JSONDecodeError, OSError):
                    data = default if default is not None else {}
            else:
                data = default if default is not None else {}

            # 改
            data = updater(data)

            # 写
            try:
                dir_path = os.path.dirname(filepath)
                fd, tmp_path = tempfile.mkstemp(
                    dir=dir_path, suffix=".tmp", prefix=f".{filename}_"
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    if os.path.exists(filepath):
                        os.replace(tmp_path, filepath)
                    else:
                        os.rename(tmp_path, filepath)
                except Exception:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
                    raise
            except OSError as e:
                _logger.error(f"更新文件失败 [{filename}]: {e}")

            return data

    def exists(self, filename: str) -> bool:
        """检查文件是否存在"""
        return os.path.isfile(self._filepath(filename))

    def ensure_subdir(self, subdir: str):
        """确保子目录存在"""
        path = os.path.join(self._data_dir, subdir)
        os.makedirs(path, exist_ok=True)
        return path
