"""
任务与命令 API

提供：
- 远程终端（直接执行命令）
- 任务管理（创建、查询、列表）
- 审计日志查询
"""

from fastapi import APIRouter, Request

from core.logger import get_logger

router = APIRouter(prefix="/tasks", tags=["tasks"])
_logger = get_logger("api.tasks")


@router.post("/execute")
async def execute_command(request: Request):
    """
    远程终端：在指定节点上执行命令。

    对于本机 → 直接执行。
    对于远程 Full → 通过 API 转发。
    对于远程 Relay → 通过心跳队列转发。
    """
    task_service = request.app.state.task_service
    node_identity = request.app.state.node_identity
    data = await request.json()

    command = data.get("command", "").strip()
    target = data.get("target_node_id", node_identity.node_id)
    timeout = data.get("timeout", 60)

    if not command:
        return {"error": "command 不能为空"}

    # 本机直接执行
    if target == node_identity.node_id:
        result = await task_service.execute_command_direct(
            command=command, timeout=timeout
        )
        return result

    # 远程节点
    nodes = request.app.state.storage.read("nodes.json", {})
    target_info = nodes.get(target, {})

    if not target_info:
        return {"error": f"节点不存在: {target}"}

    target_mode = target_info.get("mode", "")

    if target_mode in ("full", "temp_full"):
        # Full 节点 → 直接 API 转发
        import httpx
        target_url = f"http://{target_info['host']}:{target_info['port']}"
        try:
            async with httpx.AsyncClient(timeout=timeout + 5) as client:
                resp = await client.post(
                    f"{target_url}/api/v1/tasks/execute",
                    json={
                        "command": command,
                        "target_node_id": target,  # 让远端知道是本地执行
                        "timeout": timeout,
                    },
                )
                return resp.json()
        except Exception as e:
            return {"error": f"转发到 {target_url} 失败: {str(e)}"}

    elif target_mode == "relay":
        # Relay 节点 → 创建任务放入心跳队列
        task = task_service.create_task(
            target_node_id=target,
            command=command,
            timeout=timeout,
        )
        return {
            "queued": True,
            "task_id": task["task_id"],
            "message": f"命令已加入队列，等待 {target} 下次心跳时取走执行",
        }

    return {"error": f"不支持的节点模式: {target_mode}"}


@router.post("/create")
async def create_task(request: Request):
    """创建一个命令任务"""
    task_service = request.app.state.task_service
    data = await request.json()

    command = data.get("command", "").strip()
    target = data.get("target_node_id", "")
    timeout = data.get("timeout", 300)

    if not command or not target:
        return {"error": "command 和 target_node_id 必填"}

    task = task_service.create_task(
        target_node_id=target,
        command=command,
        timeout=timeout,
    )
    return task


@router.get("")
async def list_tasks(request: Request):
    """列出最近的任务"""
    task_service = request.app.state.task_service
    limit = int(request.query_params.get("limit", "50"))
    tasks = task_service.list_tasks(limit=limit)
    return {"tasks": tasks, "total": len(tasks)}


@router.get("/audit")
async def query_audit(request: Request):
    """查询审计日志"""
    audit_service = request.app.state.audit_service
    date = request.query_params.get("date")
    limit = int(request.query_params.get("limit", "50"))

    if date:
        entries = audit_service.query(date=date, limit=limit)
    else:
        entries = audit_service.query_recent(limit=limit)

    return {"entries": entries, "total": len(entries)}


@router.get("/{task_id}")
async def get_task(task_id: str, request: Request):
    """获取单个任务详情"""
    task_service = request.app.state.task_service
    task = task_service.get_task(task_id)

    if not task:
        return {"error": "任务不存在"}
    return task
