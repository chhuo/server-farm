"""
认证与会话管理

支持：
- 密码哈希（SHA-256 + salt，避免额外依赖）
- Token 会话管理
- 首次启动引导设置管理员密码
"""

import hashlib
import os
import secrets
import time
from typing import Optional

from core.logger import get_logger

_logger = get_logger("services.auth")

# Token 有效期（秒） — 24 小时
TOKEN_EXPIRY = 86400


class AuthService:
    """认证与会话管理服务"""

    def __init__(self, config, storage):
        self._config = config
        self._storage = storage

        # 活跃 Token 表：{token: {user, created_at, expires_at}}
        self._sessions: dict[str, dict] = {}

        # 初始化管理员账户
        self._ensure_admin_account()

    def _ensure_admin_account(self):
        """确保管理员账户已创建（首次启动时生成密码）"""
        auth_data = self._storage.read("auth.json", {})

        if auth_data.get("admin_password_hash"):
            _logger.debug("管理员账户已存在")
            return

        # 检查配置文件中是否有预设密码
        configured_password = self._config.get("security.admin_password", "")

        if configured_password:
            # 使用配置的密码
            password_hash = self._hash_password(configured_password)
            auth_data["admin_user"] = self._config.get("security.admin_user", "admin")
            auth_data["admin_password_hash"] = password_hash
            self._storage.write("auth.json", auth_data)
            _logger.info("已从配置文件初始化管理员密码")
        else:
            # 生成随机密码
            random_password = secrets.token_urlsafe(12)
            password_hash = self._hash_password(random_password)

            auth_data["admin_user"] = self._config.get("security.admin_user", "admin")
            auth_data["admin_password_hash"] = password_hash
            auth_data["initial_password"] = random_password  # 保留以供首次登录查看
            self._storage.write("auth.json", auth_data)

            _logger.warning("=" * 50)
            _logger.warning(f"  首次启动 — 管理员密码已生成")
            _logger.warning(f"  用户名: {auth_data['admin_user']}")
            _logger.warning(f"  密码:   {random_password}")
            _logger.warning(f"  请登录后及时修改密码！")
            _logger.warning("=" * 50)

    def _hash_password(self, password: str) -> str:
        """哈希密码（SHA-256 + salt）"""
        salt = secrets.token_hex(16)
        hashed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
        return f"{salt}:{hashed}"

    def _verify_password(self, password: str, stored_hash: str) -> bool:
        """验证密码"""
        try:
            salt, hashed = stored_hash.split(":", 1)
            return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest() == hashed
        except Exception:
            return False

    def login(self, username: str, password: str) -> Optional[str]:
        """
        登录验证。

        Returns:
            成功返回 Token，失败返回 None
        """
        auth_data = self._storage.read("auth.json", {})
        admin_user = auth_data.get("admin_user", "admin")
        password_hash = auth_data.get("admin_password_hash", "")

        if username != admin_user:
            _logger.warning(f"登录失败: 用户名不存在 ({username})")
            return None

        if not self._verify_password(password, password_hash):
            _logger.warning(f"登录失败: 密码错误 ({username})")
            return None

        # 生成 Token
        token = secrets.token_urlsafe(32)
        now = time.time()
        self._sessions[token] = {
            "user": username,
            "created_at": now,
            "expires_at": now + TOKEN_EXPIRY,
        }

        _logger.info(f"登录成功: {username}")
        return token

    def logout(self, token: str) -> bool:
        """注销 Token"""
        if token in self._sessions:
            user = self._sessions[token]["user"]
            del self._sessions[token]
            _logger.info(f"用户注销: {user}")
            return True
        return False

    def validate_token(self, token: str) -> Optional[dict]:
        """
        验证 Token。

        Returns:
            有效返回会话信息，无效返回 None
        """
        session = self._sessions.get(token)
        if not session:
            return None

        # 检查过期
        if time.time() > session["expires_at"]:
            del self._sessions[token]
            return None

        return session

    def change_password(self, old_password: str, new_password: str) -> bool:
        """修改管理员密码"""
        auth_data = self._storage.read("auth.json", {})
        password_hash = auth_data.get("admin_password_hash", "")

        if not self._verify_password(old_password, password_hash):
            _logger.warning("修改密码失败: 原密码错误")
            return False

        auth_data["admin_password_hash"] = self._hash_password(new_password)
        # 修改密码成功后清除首次启动临时密码
        if "initial_password" in auth_data:
            del auth_data["initial_password"]
        self._storage.write("auth.json", auth_data)
        _logger.info("管理员密码已修改")
        return True

    def is_setup_required(self) -> bool:
        """是否需要首次设置（存在临时密码）"""
        auth_data = self._storage.read("auth.json", {})
        return bool(auth_data.get("initial_password"))

    def get_initial_password(self) -> str:
        """获取首次生成的临时密码（仅首次设置时）"""
        auth_data = self._storage.read("auth.json", {})
        return auth_data.get("initial_password", "")

    def cleanup_expired(self):
        """清理过期 Token"""
        now = time.time()
        expired = [t for t, s in self._sessions.items() if now > s["expires_at"]]
        for token in expired:
            del self._sessions[token]
