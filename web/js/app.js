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

    initApp();
    console.log('[NodePanel] 应用已启动');
})();
