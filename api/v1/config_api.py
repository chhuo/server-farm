"""
配置管理 API

在线查看和修改系统配置。
"""

import asyncio
from fastapi import APIRouter, Request

from core.logger import get_logger

router = APIRouter(prefix="/config", tags=["config"])
_logger = get_logger("api.config")


@router.get("")
async def get_config(request: Request):
    """获取当前配置（敏感字段脱敏）"""
    config = request.app.state.config
    node_identity = request.app.state.node_identity

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
            "connectable": node_identity.connectable,
            "public_url": node_identity.public_url,
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
    """更新配置（写入 config.yaml）。仅允许修改白名单字段。"""
    config = request.app.state.config
    node_identity = request.app.state.node_identity
    data = await request.json()

    allowed_fields = {
        "app.debug", "app.env",
        "node.name", "node.mode", "node.primary_server",
        "node.public_url", "node.connectable",
        "peer.sync_interval", "peer.heartbeat_interval",
        "peer.timeout", "peer.max_fanout", "peer.max_heartbeat_failures",
        "security.command_blacklist",
        "logging.level",
    }

    updates = data.get("updates", {})
    applied = {}
    rejected = []

    # 跟踪是否需要重启同步
    need_restart_sync = False

    for key, value in updates.items():
        if key in allowed_fields:
            config.set(key, value)
            applied[key] = value
        else:
            rejected.append(key)

    if applied:
        # 持久化到 config.yaml
        try:
            config.save_to_yaml()
        except RuntimeError as e:
            _logger.error(f"配置保存失败: {e}")

        _logger.info(f"配置已更新: {list(applied.keys())}")

        # 处理需要动态更新内存状态的配置
        if "node.connectable" in applied or "node.public_url" in applied:
            connectable = config.get("node.connectable", False)
            public_url = config.get("node.public_url", "")
            node_identity.update_connectable(connectable, public_url)
            need_restart_sync = True

        if "node.name" in applied:
            node_identity.update_name(applied["node.name"])

        # 重启同步循环（connectable 变更会影响同步策略）
        if need_restart_sync:
            peer_service = request.app.state.peer_service
            asyncio.create_task(peer_service.restart_sync())

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

    try:
        config.save_to_yaml()
    except RuntimeError as e:
        _logger.error(f"黑名单保存失败: {e}")

    task_service = request.app.state.task_service
    task_service._executor._blacklist = blacklist

    _logger.info(f"命令黑名单已更新: {len(blacklist)} 条规则")
    return {"success": True, "blacklist": blacklist}
