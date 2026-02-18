"""
配置管理 API

在线查看和修改系统配置。
"""

import os
import yaml
from fastapi import APIRouter, Request

from core.logger import get_logger

router = APIRouter(prefix="/config", tags=["config"])
_logger = get_logger("api.config")


@router.get("")
async def get_config(request: Request):
    """获取当前配置（敏感字段脱敏）"""
    config = request.app.state.config
    node_identity = request.app.state.node_identity

    # 构建安全的配置视图
    safe_config = {
        "app": {
            "name": config.get("app.name"),
            "version": config.get("app.version"),
            "env": config.get("app.env"),
            "debug": config.get("app.debug"),
        },
        "server": {
            "host": config.get("server.host"),
            "port": config.get("server.port"),
        },
        "node": {
            "id": node_identity.node_id,
            "name": node_identity.name,
            "mode": node_identity.mode.value,
            "primary_server": config.get("node.primary_server", ""),
        },
        "peer": {
            "sync_interval": config.get("peer.sync_interval"),
            "heartbeat_interval": config.get("peer.heartbeat_interval"),
            "timeout": config.get("peer.timeout"),
            "max_fanout": config.get("peer.max_fanout"),
            "max_heartbeat_failures": config.get("peer.max_heartbeat_failures"),
        },
        "security": {
            "admin_user": config.get("security.admin_user"),
            "command_blacklist": config.get("security.command_blacklist", []),
            # 密码和 key 不暴露
        },
        "logging": {
            "level": config.get("logging.level"),
            "console_enabled": config.get("logging.console.enabled"),
            "file_enabled": config.get("logging.file.enabled"),
        },
    }

    return {"config": safe_config}


@router.post("/update")
async def update_config(request: Request):
    """
    更新配置（写入 config.yaml 并热重载）。
    仅允许修改安全的字段。
    """
    config = request.app.state.config
    data = await request.json()

    # 允许修改的字段白名单
    allowed_fields = {
        "app.debug", "app.env",
        "node.name", "node.mode", "node.primary_server",
        "peer.sync_interval", "peer.heartbeat_interval",
        "peer.timeout", "peer.max_fanout", "peer.max_heartbeat_failures",
        "security.command_blacklist",
        "logging.level",
    }

    updates = data.get("updates", {})
    applied = {}
    rejected = []

    for key, value in updates.items():
        if key in allowed_fields:
            config.set(key, value)
            applied[key] = value
        else:
            rejected.append(key)

    if applied:
        # 保存到 config.yaml
        _save_config_yaml(config)
        _logger.info(f"配置已更新: {list(applied.keys())}")

    result = {"applied": applied, "rejected": rejected}
    if rejected:
        result["message"] = f"以下字段不允许在线修改: {rejected}"

    return result


@router.get("/blacklist")
async def get_blacklist(request: Request):
    """获取命令黑名单"""
    config = request.app.state.config
    return {"blacklist": config.get("security.command_blacklist", [])}


@router.post("/blacklist")
async def update_blacklist(request: Request):
    """更新命令黑名单"""
    config = request.app.state.config
    data = await request.json()

    blacklist = data.get("blacklist", [])
    config.set("security.command_blacklist", blacklist)
    _save_config_yaml(config)

    # 更新执行器的黑名单
    task_service = request.app.state.task_service
    task_service._executor._blacklist = blacklist

    _logger.info(f"命令黑名单已更新: {len(blacklist)} 条规则")
    return {"success": True, "blacklist": blacklist}


def _save_config_yaml(config):
    """保存配置到 config.yaml"""
    config_path = os.path.join(config.project_root, "config.yaml")
    try:
        # 读取现有配置
        current = {}
        if os.path.isfile(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                current = yaml.safe_load(f) or {}

        # 合并已修改的值
        def set_nested(d, key, value):
            parts = key.split(".")
            for part in parts[:-1]:
                d = d.setdefault(part, {})
            d[parts[-1]] = value

        # 从 config 对象获取最新的完整配置
        sections = ["app", "server", "node", "peer", "security", "logging"]
        for section in sections:
            section_data = config.get(section, {})
            if isinstance(section_data, dict):
                current[section] = section_data

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(current, f, default_flow_style=False, allow_unicode=True)

        _logger.debug("配置已保存到 config.yaml")
    except Exception as e:
        _logger.error(f"配置保存失败: {e}")
