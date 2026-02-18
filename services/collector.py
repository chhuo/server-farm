"""
系统信息采集服务

采集本机 CPU、内存、磁盘、网络等信息。
使用 psutil 库，提供结构化的数据输出。
"""

import platform
import time
from typing import Any

import psutil

from core.logger import get_logger

_logger = get_logger("services.collector")


def collect_system_info() -> dict[str, Any]:
    """
    采集本机系统信息，返回结构化字典。

    Returns:
        包含 cpu、memory、disk、network、system 信息的字典
    """
    try:
        info = {
            "timestamp": time.time(),
            "system": _collect_system_meta(),
            "cpu": _collect_cpu(),
            "memory": _collect_memory(),
            "disk": _collect_disk(),
            "network": _collect_network(),
            "uptime": _collect_uptime(),
        }
        return info
    except Exception as e:
        _logger.error(f"系统信息采集失败: {e}")
        return {"timestamp": time.time(), "error": str(e)}


def _collect_system_meta() -> dict[str, Any]:
    """系统元信息"""
    uname = platform.uname()
    return {
        "hostname": uname.node,
        "os": uname.system,
        "os_version": uname.version,
        "architecture": uname.machine,
        "python_version": platform.python_version(),
    }


def _collect_cpu() -> dict[str, Any]:
    """CPU 信息"""
    cpu_freq = psutil.cpu_freq()
    return {
        "count_physical": psutil.cpu_count(logical=False) or 0,
        "count_logical": psutil.cpu_count(logical=True) or 0,
        "percent": psutil.cpu_percent(interval=0.5),
        "percent_per_core": psutil.cpu_percent(interval=0, percpu=True),
        "frequency_mhz": round(cpu_freq.current, 1) if cpu_freq else 0,
    }


def _collect_memory() -> dict[str, Any]:
    """内存信息"""
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        "total_mb": round(mem.total / 1024 / 1024, 1),
        "used_mb": round(mem.used / 1024 / 1024, 1),
        "available_mb": round(mem.available / 1024 / 1024, 1),
        "percent": mem.percent,
        "swap_total_mb": round(swap.total / 1024 / 1024, 1),
        "swap_used_mb": round(swap.used / 1024 / 1024, 1),
        "swap_percent": swap.percent,
    }


def _collect_disk() -> dict[str, Any]:
    """磁盘信息"""
    partitions = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            partitions.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "total_gb": round(usage.total / 1024 / 1024 / 1024, 2),
                "used_gb": round(usage.used / 1024 / 1024 / 1024, 2),
                "free_gb": round(usage.free / 1024 / 1024 / 1024, 2),
                "percent": usage.percent,
            })
        except (PermissionError, OSError):
            continue

    return {"partitions": partitions}


def _collect_network() -> dict[str, Any]:
    """网络信息"""
    net_io = psutil.net_io_counters()
    return {
        "bytes_sent": net_io.bytes_sent,
        "bytes_recv": net_io.bytes_recv,
        "packets_sent": net_io.packets_sent,
        "packets_recv": net_io.packets_recv,
    }


def _collect_uptime() -> float:
    """系统运行时长（秒）"""
    return time.time() - psutil.boot_time()
