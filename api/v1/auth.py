"""
认证 API

登录 / 注销 / 状态检查 / 修改密码
"""

from fastapi import APIRouter, Request, Response

from core.logger import get_logger

router = APIRouter(prefix="/auth", tags=["auth"])
_logger = get_logger("api.auth")


@router.post("/login")
async def login(request: Request, response: Response):
    """用户登录"""
    auth_service = request.app.state.auth_service
    data = await request.json()

    username = data.get("username", "")
    password = data.get("password", "")

    if not username or not password:
        return {"error": "请输入用户名和密码"}

    token = auth_service.login(username, password)

    if token:
        response.set_cookie(
            key="token",
            value=token,
            httponly=True,
            max_age=86400,
            samesite="lax",
        )
        return {"success": True, "user": username}
    else:
        return {"error": "用户名或密码错误"}


@router.post("/logout")
async def logout(request: Request, response: Response):
    """用户注销"""
    auth_service = request.app.state.auth_service
    token = request.cookies.get("token", "")
    auth_service.logout(token)
    response.delete_cookie("token")
    return {"success": True}


@router.get("/status")
async def auth_status(request: Request):
    """检查登录状态"""
    auth_service = request.app.state.auth_service
    token = request.cookies.get("token", "")
    session = auth_service.validate_token(token)

    if session:
        return {
            "authenticated": True,
            "user": session["user"],
        }
    else:
        return {
            "authenticated": False,
            "setup_required": auth_service.is_setup_required(),
        }


@router.post("/change-password")
async def change_password(request: Request):
    """修改密码"""
    auth_service = request.app.state.auth_service

    # 验证登录
    token = request.cookies.get("token", "")
    session = auth_service.validate_token(token)
    if not session:
        return {"error": "请先登录"}

    data = await request.json()
    old_password = data.get("old_password", "")
    new_password = data.get("new_password", "")

    if not old_password or not new_password:
        return {"error": "请输入原密码和新密码"}

    if len(new_password) < 6:
        return {"error": "新密码至少 6 位"}

    if auth_service.change_password(old_password, new_password):
        return {"success": True, "message": "密码已修改"}
    else:
        return {"error": "原密码错误"}
