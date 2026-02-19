/**
 * NodePanel SPA 入口
 * 集成认证守卫：未登录时跳转登录页，登录后恢复侧边栏。
 */

(function () {
    'use strict';

    // 注册路由
    Router.register('/dashboard', DashboardPage);
    Router.register('/nodes', NodesPage);
    Router.register('/terminal', TerminalPage);
    Router.register('/tasks', TasksPage);
    Router.register('/settings', SettingsPage);
    Router.register('/login', LoginPage);

    // ── 认证守卫 ──
    async function checkAuth() {
        try {
            const data = await API.get('/api/v1/auth/status');
            return data.authenticated === true;
        } catch {
            return false;
        }
    }

    async function initApp() {
        const authenticated = await checkAuth();

        if (!authenticated) {
            // 隐藏侧边栏和顶栏，显示登录全屏
            const sidebar = document.getElementById('sidebar');
            const topbar = document.querySelector('.topbar');
            const mainContent = document.getElementById('main-content');

            if (sidebar) sidebar.style.display = 'none';
            if (topbar) topbar.style.display = 'none';
            if (mainContent) {
                mainContent.style.marginLeft = '0';
                mainContent.style.padding = '0';
            }

            // 直接渲染登录页
            const container = document.getElementById('page-container');
            if (container) {
                container.innerHTML = LoginPage.render();
                LoginPage.mount();
            }
            return;
        }

        // 检查是否需要弹出修改密码提示（首次登录）
        if (sessionStorage.getItem('showChangePassword') === '1') {
            sessionStorage.removeItem('showChangePassword');
            _showChangePasswordPrompt();
        }

        // 已登录 — 侧边栏切换（移动端）
        const sidebarToggle = document.getElementById('sidebar-toggle');
        const sidebar = document.getElementById('sidebar');
        if (sidebarToggle && sidebar) {
            sidebarToggle.addEventListener('click', () => {
                sidebar.classList.toggle('open');
            });

            document.getElementById('main-content').addEventListener('click', (e) => {
                if (sidebar.classList.contains('open') && !sidebar.contains(e.target)) {
                    sidebar.classList.remove('open');
                }
            });
        }

        // 添加注销按钮到侧边栏底部
        const sidebarFooter = document.querySelector('.sidebar-footer');
        if (sidebarFooter) {
            const logoutBtn = document.createElement('button');
            logoutBtn.className = 'logout-btn';
            logoutBtn.innerHTML = '<span class="nav-icon">⊗</span> 注销';
            logoutBtn.addEventListener('click', async () => {
                await API.post('/api/v1/auth/logout');
                window.location.reload();
            });
            sidebarFooter.appendChild(logoutBtn);
        }

        // 启动路由
        Router.init();
    }

    function _showChangePasswordPrompt() {
        const overlay = document.createElement('div');
        overlay.className = 'dialog-overlay';
        overlay.id = 'init-password-dialog';
        overlay.style.cssText = 'display:flex;z-index:9999';
        overlay.innerHTML = `
            <div class="dialog">
                <div class="dialog-header">
                    <span class="dialog-title">⚠️ 建议修改默认密码</span>
                </div>
                <div class="dialog-body">
                    <p style="color:var(--text-muted);margin-bottom:16px;font-size:0.9rem">
                        您正在使用系统自动生成的初始密码，建议立即修改以保障安全。
                    </p>
                    <div class="form-group">
                        <label class="form-label">原密码（初始密码）</label>
                        <input type="password" class="form-input" id="init-old-password">
                    </div>
                    <div class="form-group">
                        <label class="form-label">新密码（至少 6 位）</label>
                        <input type="password" class="form-input" id="init-new-password">
                    </div>
                    <div class="form-group">
                        <label class="form-label">确认新密码</label>
                        <input type="password" class="form-input" id="init-confirm-password">
                    </div>
                    <div id="init-password-error" style="display:none;color:var(--accent-red);font-size:0.85rem;margin-top:8px"></div>
                </div>
                <div class="dialog-footer" style="display:flex;gap:8px;justify-content:flex-end;padding:16px">
                    <button class="btn btn-secondary" id="init-skip-btn">跳过（下次启动仍会提示）</button>
                    <button class="btn btn-primary" id="init-confirm-btn">立即修改</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);

        document.getElementById('init-skip-btn').addEventListener('click', () => {
            overlay.remove();
        });

        document.getElementById('init-confirm-btn').addEventListener('click', async () => {
            const oldPass = document.getElementById('init-old-password').value;
            const newPass = document.getElementById('init-new-password').value;
            const confirmPass = document.getElementById('init-confirm-password').value;
            const errEl = document.getElementById('init-password-error');

            if (!oldPass || !newPass || !confirmPass) {
                errEl.textContent = '请填写所有字段';
                errEl.style.display = 'block';
                return;
            }
            if (newPass !== confirmPass) {
                errEl.textContent = '两次输入的新密码不一致';
                errEl.style.display = 'block';
                return;
            }
            if (newPass.length < 6) {
                errEl.textContent = '新密码至少 6 位';
                errEl.style.display = 'block';
                return;
            }

            const btn = document.getElementById('init-confirm-btn');
            btn.disabled = true;
            btn.textContent = '修改中...';

            try {
                const result = await API.post('/api/v1/auth/change-password', {
                    old_password: oldPass,
                    new_password: newPass,
                });
                if (result.error) {
                    errEl.textContent = result.error;
                    errEl.style.display = 'block';
                    btn.disabled = false;
                    btn.textContent = '立即修改';
                } else {
                    overlay.remove();
                }
            } catch (err) {
                errEl.textContent = '请求失败: ' + err.message;
                errEl.style.display = 'block';
                btn.disabled = false;
                btn.textContent = '立即修改';
            }
        });
    }

    initApp();
    console.log('[NodePanel] 应用已启动');
})();
