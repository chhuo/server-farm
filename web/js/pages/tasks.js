/**
 * 任务中心页面
 * 展示所有命令任务的执行历史和审计日志。
 */

const TasksPage = {
    title: '任务中心',
    _refreshTimer: null,
    _activeTab: 'tasks',

    render() {
        return `
            <div class="tab-bar">
                <button class="tab-btn active" onclick="TasksPage._switchTab('tasks')">任务列表</button>
                <button class="tab-btn" onclick="TasksPage._switchTab('audit')">审计日志</button>
            </div>

            <div id="tasks-content">
                <div class="loading">
                    <div class="loading-spinner"></div>
                    加载中...
                </div>
            </div>
        `;
    },

    mount() {
        this._loadTasks();
        this._refreshTimer = setInterval(() => {
            if (this._activeTab === 'tasks') this._loadTasks();
            else this._loadAudit();
        }, 5000);
    },

    destroy() {
        if (this._refreshTimer) {
            clearInterval(this._refreshTimer);
            this._refreshTimer = null;
        }
    },

    _switchTab(tab) {
        this._activeTab = tab;
        document.querySelectorAll('.tab-btn').forEach((btn, i) => {
            btn.classList.toggle('active', (i === 0 && tab === 'tasks') || (i === 1 && tab === 'audit'));
        });
        if (tab === 'tasks') this._loadTasks();
        else this._loadAudit();
    },

    async _loadTasks() {
        try {
            const data = await API.get('/api/v1/tasks?limit=50');
            const content = document.getElementById('tasks-content');
            if (!content) return;

            const tasks = data.tasks || [];
            if (tasks.length === 0) {
                content.innerHTML = `
                    <div class="placeholder-page" style="padding:40px">
                        <div class="placeholder-icon">📋</div>
                        <div class="placeholder-title">暂无任务</div>
                        <div class="placeholder-desc">在远程终端中执行命令来创建任务</div>
                    </div>
                `;
                return;
            }

            content.innerHTML = `
                <div class="panel" style="margin-top:16px">
                    <table class="data-table">
                        <thead><tr>
                            <th>任务 ID</th>
                            <th>目标节点</th>
                            <th>命令</th>
                            <th>状态</th>
                            <th>退出码</th>
                            <th>创建时间</th>
                        </tr></thead>
                        <tbody>
                            ${tasks.map(t => this._renderTaskRow(t)).join('')}
                        </tbody>
                    </table>
                </div>
            `;
        } catch (err) {
            console.error('任务加载失败:', err);
        }
    },

    _renderTaskRow(task) {
        const statusMap = {
            pending: ['等待', 'yellow'],
            running: ['执行中', 'blue'],
            completed: ['完成', 'green'],
            failed: ['失败', 'red'],
            timeout: ['超时', 'red'],
        };
        const [label, cls] = statusMap[task.status] || [task.status, 'yellow'];

        const time = task.created_at
            ? new Date(task.created_at * 1000).toLocaleString()
            : '--';

        const cmd = task.command.length > 40
            ? task.command.substring(0, 40) + '...'
            : task.command;

        return `
            <tr>
                <td class="mono">${task.task_id}</td>
                <td>${task.target_node_id}</td>
                <td class="mono">${this._escapeHtml(cmd)}</td>
                <td><span class="tag ${cls}">${label}</span></td>
                <td class="mono">${task.exit_code ?? '--'}</td>
                <td>${time}</td>
            </tr>
        `;
    },

    async _loadAudit() {
        try {
            const data = await API.get('/api/v1/tasks/audit?limit=50');
            const content = document.getElementById('tasks-content');
            if (!content) return;

            const entries = data.entries || [];
            if (entries.length === 0) {
                content.innerHTML = `
                    <div class="placeholder-page" style="padding:40px">
                        <div class="placeholder-icon">📝</div>
                        <div class="placeholder-title">暂无审计日志</div>
                    </div>
                `;
                return;
            }

            content.innerHTML = `
                <div class="panel" style="margin-top:16px">
                    <table class="data-table">
                        <thead><tr>
                            <th>时间</th>
                            <th>操作</th>
                            <th>用户</th>
                            <th>节点</th>
                            <th>命令</th>
                            <th>结果</th>
                        </tr></thead>
                        <tbody>
                            ${entries.map(e => this._renderAuditRow(e)).join('')}
                        </tbody>
                    </table>
                </div>
            `;
        } catch (err) {
            console.error('审计日志加载失败:', err);
        }
    },

    _renderAuditRow(entry) {
        const resultMap = {
            success: 'green', completed: 'green', pending: 'yellow',
            running: 'blue', failed: 'red', timeout: 'red', blocked: 'red',
        };
        const cls = resultMap[entry.result] || 'yellow';

        const cmd = (entry.command || '').length > 30
            ? entry.command.substring(0, 30) + '...'
            : (entry.command || '-');

        return `
            <tr>
                <td>${entry.datetime || '--'}</td>
                <td>${entry.action}</td>
                <td>${entry.user || 'system'}</td>
                <td>${entry.target_node || '--'}</td>
                <td class="mono">${this._escapeHtml(cmd)}</td>
                <td><span class="tag ${cls}">${entry.result || '--'}</span></td>
            </tr>
        `;
    },

    _escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },
};
