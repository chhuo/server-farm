/**
 * 节点管理页面
 * 展示所有节点列表，提供节点添加功能。
 * 无公网 IP 的节点通过 Relay 模式 → Full 节点转发，面板可管理整个网络。
 */

const NodesPage = {
    title: '节点管理',
    _refreshTimer: null,

    render() {
        return `
            <div class="stats-grid" style="grid-template-columns: repeat(auto-fit, minmax(180px, 1fr))">
                <div class="stat-card blue">
                    <div class="stat-card-header">
                        <span class="stat-card-title">总节点数</span>
                        <span class="stat-card-icon">⊡</span>
                    </div>
                    <div class="stat-card-value" id="nodes-total">--</div>
                </div>
                <div class="stat-card green">
                    <div class="stat-card-header">
                        <span class="stat-card-title">在线</span>
                        <span class="stat-card-icon">●</span>
                    </div>
                    <div class="stat-card-value" id="nodes-online">--</div>
                </div>
                <div class="stat-card purple">
                    <div class="stat-card-header">
                        <span class="stat-card-title">Full 节点</span>
                        <span class="stat-card-icon">◈</span>
                    </div>
                    <div class="stat-card-value" id="nodes-full">--</div>
                </div>
                <div class="stat-card red">
                    <div class="stat-card-header">
                        <span class="stat-card-title">离线</span>
                        <span class="stat-card-icon">○</span>
                    </div>
                    <div class="stat-card-value" id="nodes-offline">--</div>
                </div>
            </div>

            <div class="panel">
                <div class="panel-header">
                    <span class="panel-title">节点列表</span>
                    <button class="add-node-btn" id="add-node-btn" onclick="NodesPage._showAddDialog()">
                        + 添加节点
                    </button>
                </div>
                <div class="panel-body" id="nodes-table-body">
                    <div class="loading">
                        <div class="loading-spinner"></div>
                        加载节点列表...
                    </div>
                </div>
            </div>

            <!-- 添加节点对话框 -->
            <div class="dialog-overlay" id="add-node-dialog" style="display:none">
                <div class="dialog">
                    <div class="dialog-header">
                        <span class="dialog-title">添加节点</span>
                        <button class="dialog-close" onclick="NodesPage._hideAddDialog()">✕</button>
                    </div>
                    <div class="dialog-body">
                        <div class="form-group">
                            <label class="form-label">节点地址</label>
                            <input type="text" class="form-input" id="add-node-host"
                                   placeholder="如 https://servers.example.com 或 192.168.1.100:8300">
                        </div>
                        <div class="form-tip">
                            💡 输入对方节点的访问地址（IP、域名或完整 URL）。
                            无公网 IP 的节点请在目标机器的 <code>node.public_url</code> 中配置对外地址。
                        </div>
                        <div class="form-actions">
                            <button class="btn btn-secondary" onclick="NodesPage._hideAddDialog()">取消</button>
                            <button class="btn btn-primary" id="add-node-submit" onclick="NodesPage._addNode()">
                                连接并添加
                            </button>
                        </div>
                        <div class="form-message" id="add-node-message"></div>
                    </div>
                </div>
            </div>
        `;
    },

    mount() {
        this._fetchNodes();
        this._refreshTimer = setInterval(() => this._fetchNodes(), 5000);
    },

    destroy() {
        if (this._refreshTimer) {
            clearInterval(this._refreshTimer);
            this._refreshTimer = null;
        }
    },

    async _fetchNodes() {
        try {
            const data = await API.get('/api/v1/nodes');
            this._updateUI(data);
        } catch (err) {
            console.error('节点列表加载失败:', err);
        }
    },

    _updateUI(data) {
        const nodes = data.nodes || [];

        // 统计
        const total = nodes.length;
        const online = nodes.filter(n => n.status === 'online').length;
        const offline = total - online;
        const full = nodes.filter(n => ['full', 'temp_full'].includes(n.mode)).length;

        const setEl = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
        setEl('nodes-total', total);
        setEl('nodes-online', online);
        setEl('nodes-full', full);
        setEl('nodes-offline', offline);

        // 节点表
        const body = document.getElementById('nodes-table-body');
        if (!body) return;

        if (nodes.length === 0) {
            body.innerHTML = `
                <div class="placeholder-page" style="padding:40px">
                    <div class="placeholder-icon">⊡</div>
                    <div class="placeholder-title">暂无其他节点</div>
                    <div class="placeholder-desc">点击 "添加节点" 来连接远程服务器</div>
                </div>
            `;
            return;
        }

        body.innerHTML = `
            <table class="data-table">
                <thead>
                    <tr>
                        <th>名称</th>
                        <th>状态</th>
                        <th>模式</th>
                        <th>网络</th>
                        <th>地址</th>
                        <th>CPU</th>
                        <th>内存</th>
                        <th>最后心跳</th>
                    </tr>
                </thead>
                <tbody>
                    ${nodes.map(n => this._renderNodeRow(n)).join('')}
                </tbody>
            </table>
        `;
    },

    _renderNodeRow(node) {
        const statusClass = node.status === 'online' ? 'green' :
            node.status === 'offline' ? 'red' : 'yellow';
        const statusText = node.status === 'online' ? '在线' :
            node.status === 'offline' ? '离线' : '未知';

        const modeMap = {
            full: 'Full',
            relay: 'Relay',
            temp_full: 'Temp-Full',
        };
        const modeClass = node.mode === 'full' ? 'blue' :
            node.mode === 'relay' ? 'yellow' : 'purple';

        const sys = node.system_info || {};
        const cpu = sys.cpu ? `${sys.cpu.percent}%` : '--';
        const mem = sys.memory ? `${sys.memory.percent}%` : '--';

        const selfBadge = node.is_self ? ' <span class="tag blue" style="margin-left:4px">本机</span>' : '';

        const lastSeen = node.last_seen ?
            this._formatTimeAgo(node.last_seen) : '--';

        const addr = node.public_url || (node.host && node.port ? `${node.host}:${node.port}` : '--');

        const connectable = node.connectable;
        const connectTag = connectable
            ? '<span class="connectivity-tag public">🌐 公网</span>'
            : '<span class="connectivity-tag private">🏠 内网</span>';

        return `
            <tr>
                <td>${node.name || node.node_id}${selfBadge}</td>
                <td><span class="tag ${statusClass}">${statusText}</span></td>
                <td><span class="tag ${modeClass}">${modeMap[node.mode] || node.mode}</span></td>
                <td>${connectTag}</td>
                <td class="mono">${addr}</td>
                <td class="mono">${cpu}</td>
                <td class="mono">${mem}</td>
                <td>${lastSeen}</td>
            </tr>
        `;
    },

    _formatTimeAgo(timestamp) {
        const diff = Date.now() / 1000 - timestamp;
        if (diff < 10) return '刚刚';
        if (diff < 60) return `${Math.floor(diff)}秒前`;
        if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`;
        return `${Math.floor(diff / 86400)}天前`;
    },

    _showAddDialog() {
        const dialog = document.getElementById('add-node-dialog');
        if (dialog) dialog.style.display = 'flex';
    },

    _hideAddDialog() {
        const dialog = document.getElementById('add-node-dialog');
        if (dialog) dialog.style.display = 'none';
        const msg = document.getElementById('add-node-message');
        if (msg) msg.textContent = '';
    },

    async _addNode() {
        const host = document.getElementById('add-node-host')?.value?.trim();
        const port = 8300;
        const msgEl = document.getElementById('add-node-message');
        const btn = document.getElementById('add-node-submit');

        if (!host) {
            if (msgEl) { msgEl.textContent = '请输入主机地址'; msgEl.className = 'form-message error'; }
            return;
        }

        if (btn) btn.disabled = true;
        if (msgEl) { msgEl.textContent = '正在连接...'; msgEl.className = 'form-message'; }

        try {
            const result = await API.post('/api/v1/nodes/add', { host, port });
            if (result.error) {
                if (msgEl) { msgEl.textContent = result.error; msgEl.className = 'form-message error'; }
            } else {
                if (msgEl) { msgEl.textContent = '✓ 添加成功!'; msgEl.className = 'form-message success'; }
                setTimeout(() => {
                    this._hideAddDialog();
                    this._fetchNodes();
                }, 1000);
            }
        } catch (err) {
            if (msgEl) { msgEl.textContent = `连接失败: ${err.message}`; msgEl.className = 'form-message error'; }
        } finally {
            if (btn) btn.disabled = false;
        }
    },
};
