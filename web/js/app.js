/**
 * NodePanel SPA 入口
 * 注册路由并启动应用。
 */

(function () {
    'use strict';

    // 注册路由
    Router.register('/dashboard', DashboardPage);
    Router.register('/nodes', NodesPage);
    Router.register('/terminal', TerminalPage);
    Router.register('/tasks', TasksPage);
    Router.register('/settings', SettingsPage);

    // 侧边栏切换（移动端）
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const sidebar = document.getElementById('sidebar');
    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener('click', () => {
            sidebar.classList.toggle('open');
        });

        // 点击主内容区关闭侧边栏
        document.getElementById('main-content').addEventListener('click', (e) => {
            if (sidebar.classList.contains('open') && !sidebar.contains(e.target)) {
                sidebar.classList.remove('open');
            }
        });
    }

    // 启动路由
    Router.init();

    console.log('[NodePanel] 应用已启动');
})();
