/**
 * 节点管理页面
 * 
 * 功能：
 * - 展示所有节点列表（含信任状态）
 * - 加入网络（向目标节点发送申请）
 * - 审批/拒绝 pending 节点
 * - 踢出 trusted 节点
 * - 显示加入申请的轮询状态
 */

const NodesPage = {
    title: '节点管理',
    _refreshTimer: null,
    _joinPollTimer: null,

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
                        <span class="stat-card-title">已信任</span>
                        <span class="stat-card-icon">✓</span>
                    </div>
                    <div class="stat-card-value" id="nodes-trusted">--</div>
                </div>
                <div class="stat-card yellow">
                    <div class="stat-card-header">
                        <span class="stat-card-title">待审批</span>
                        <span class="stat-card-icon">⏳</span>
                    </div>
                    <div class="stat-card-value" id="nodes-pending">--</div>
                </div>
                <div class="stat-card red">
                    <div class="stat-card-header">
                        <span class="stat-card-title">离线</span>
                        <span class="stat-card-icon">○</span>
                    </div>
                    <div class="stat-card-value" id="nodes-offline">--</div>
                </div>
            </div>

            <!-- 加入状态提示 -->
            <div class="panel" id="join-status-panel" style="display:none">
                <div class="panel-body" style="padding:16px">
                    <div id="join-status-content"></div>
                </div>
            </div>

            <div class="panel">
                <div class="panel-header">
                    <span class="panel-title">节点列表</span>
                    <button class="add-node-btn" id="join-network-btn" onclick="NodesPage._showJoinDialog()">
                        🔗 加入网络
                    </button>
                </div>
                <div class="panel-body" id="nodes-table-body">
                    <div class="loading">
                        <div class="loading-spinner"></div>
                        加载节点列表...
                    </div>
                </div>
            </div>

            <!-- 加入网络对话框 -->
            <div class="dialog-overlay" id="join-network-dialog" style="display:none">
                <div class="dialog">
                    <div class="dialog-header">
                        <span class="dialog-title">加入网络</span>
                        <button class="dialog-close" onclick="NodesPage._hideJoinDialog()">✕</button>
                    </div>
                    <div class="dialog-body">
                        <div class="form-group">
                            <label class="form-label">目标节点地址</label>
                            <input type="text" class="form-input" id="join-node-host"
                                   placeholder="如 https://servers.example.com 或 192.168.1.100:8300">
                        </div>
                        <div class="form-tip">
                            💡 输入网络中任意一个节点的访问地址。
                            提交后需要该网络中的管理员审批，审批通过后自动加入。
                        </div>
                        <div class="form-actions">
                            <button class="btn btn-secondary" onclick="NodesPage._hideJoinDialog()">取消</button>
                            <button class="btn btn-primary" id="join-submit-btn" onclick="NodesPage._joinNetwork()">
                                发送申请
                            </button>
                        </div>
                        <div class="form-message" id="join-message"></div>
                    </div>
                </div>
            </div>

            <!-- 节点详情对话框 -->
            <div class="dialog-overlay" id="node-detail-dialog" style="display:none">
                <div class="dialog">
                    <div class="dialog-header">
                        <span class="dialog-title" id="node-detail-title">节点详情</span>
                        <button class="dialog-close" onclick="NodesPage._hideDetailDialog()">✕</button>
                    </div>
                    <div class="dialog-body" id="node-detail-body"></div>
                </div>
            </div>
        `;
    },

    mount() {
        this._fetchNodes();
        this._fetchJoinStatus();
        this._refreshTimer = setInterval(() => this._fetchNodes(), 5000);
        this._joinPollTimer = setInterval(() => this._fetchJoinStatus(), 5000);
    },

    destroy() {
        if (this._refreshTimer) {
            clearInterval(this._refreshTimer);
            this._refreshTimer = null;
        }
        if (this._joinPollTimer) {
            clearInterval(this._joinPollTimer);
            this._joinPollTimer = null;
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

    async _fetchJoinStatus() {
        try {
            const data = await API.get('/api/v1/nodes/join-status');
            this._updateJoinStatus(data);
        } catch (err) {
            // 忽略
        }
    },

    _updateJoinStatus(data) {
        const panel = document.getElementById('join-status-panel');
        const content = document.getElementById('join-status-content');
        if (!panel || !content) return;

        const status = data.status || 'none';

        if (status === 'none' || status === 'trusted') {
            // 已加入或未申请，隐藏
            if (status === 'trusted') {
                panel.style.display = 'block';
                content.innerHTML = `
                    <div style="display:flex; align-items:center; gap:8px; color:var(--success)">
                        <span style="font-size:20px">✅</span>
                        <span>已成功加入网络</span>
                    </div>
                `;
                // 3 秒后隐藏
                setTimeout(() => { panel.style.display = 'none'; }, 3000);
            } else {
                panel.style.display = 'none';
            }
            return;
        }

        panel.style.display = 'block';

        if (status === 'polling') {
            content.innerHTML = `
                <div style="display:flex; align-items:center; gap:8px; color:var(--warning)">
                    <div class="loading-spinner" style="width:16px;height:16px;border-width:2px"></div>
                    <span>正在等待 <strong>${data.target_id || ''}</strong> 的管理员审批...</span>
                    <span class="mono" style="font-size:12px; opacity:0.6">${data.target_url || ''}</span>
                </div>
            `;
        } else if (status === 'kicked') {
            content.innerHTML = `
                <div style="display:flex; align-items:center; gap:8px; color:var(--danger)">
                    <span style="font-size:20px">🚫</span>
                    <span>加入申请被拒绝或已被踢出网络</span>
                </div>
            `;
        } else if (status === 'failed') {
            content.innerHTML = `
                <div style="display:flex; align-items:center; gap:8px; color:var(--danger)">
                    <span style="font-size:20px">❌</span>
                    <span>加入网络失败</span>
                </div>
            `;
        }
    },

    _updateUI(data) {
        const nodes = data.nodes || [];

        // 统计
        const total = nodes.length;
        const trusted = nodes.filter(n => ['trusted', 'self'].includes(n.trust_status)).length;
        const pending = nodes.filter(n => n.trust_status === 'pending').length;
        const offline = nodes.filter(n => n.status !== 'online' && ['trusted', 'self'].includes(n.trust_status)).length;

        const setEl = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
        setEl('nodes-total', total);
        setEl('nodes-trusted', trusted);
        setEl('nodes-pending', pending);
        setEl('nodes-offline', offline);

        // 节点表
        const body = document.getElementById('nodes-table-body');
        if (!body) return;

        if (nodes.length === 0) {
            body.innerHTML = `
                <div class="placeholder-page" style="padding:40px">
                    <div class="placeholder-icon">⊡</div>
                    <div class="placeholder-title">暂无其他节点</div>
                    <div class="placeholder-desc">点击 "加入网络" 来连接远程节点，或等待其他节点申请加入</div>
                </div>
            `;
            return;
        }

        body.innerHTML = `
            <table class="data-table">
                <thead>
                    <tr>
                        <th>名称</th>
                        <th>信任</th>
                        <th>状态</th>
                        <th>模式</th>
                        <th>公网</th>
                        <th>公钥指纹</th>
                        <th>地址</th>
                        <th>CPU</th>
                        <th>内存</th>
                        <th>最后心跳</th>
                        <th>操作</th>
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

        // 信任状态
        const trustMap = {
            self: { text: '本机', class: 'blue', icon: '🏠' },
            trusted: { text: '已信任', class: 'green', icon: '✓' },
            pending: { text: '待审批', class: 'yellow', icon: '⏳' },
            waiting_approval: { text: '等待中', class: 'blue', icon: '⏳' },
            kicked: { text: '已踢出', class: 'red', icon: '🚫' },
        };
        const trust = trustMap[node.trust_status] || { text: node.trust_status || '未知', class: 'yellow', icon: '?' };

        const sys = node.system_info || {};
        const cpu = sys.cpu ? `${sys.cpu.percent}%` : '--';
        const mem = sys.memory ? `${sys.memory.percent}%` : '--';

        const selfBadge = node.is_self ? ' <span class="tag blue" style="margin-left:4px">本机</span>' : '';

        const lastSeen = node.last_seen ?
            this._formatTimeAgo(node.last_seen) : '--';

        const addr = node.public_url || (node.host && node.port ? `${node.host}:${node.port}` : '--');

        const fingerprint = node.public_key_fingerprint || '--';

        // 操作按钮
        let actions = '';
        if (node.is_self) {
            actions = '<span style="opacity:0.4">—</span>';
        } else if (node.trust_status === 'pending') {
            actions = `
                <button class="btn btn-small btn-success" onclick="NodesPage._approveNode('${node.node_id}')">批准</button>
                <button class="btn btn-small btn-danger" onclick="NodesPage._rejectNode('${node.node_id}')">拒绝</button>
            `;
        } else if (node.trust_status === 'trusted') {
            actions = `
                <button class="btn btn-small btn-danger" onclick="NodesPage._kickNode('${node.node_id}', '${node.name || node.node_id}')">踢出</button>
            `;
        } else if (node.trust_status === 'kicked') {
            actions = `
                <button class="btn btn-small btn-secondary" onclick="NodesPage._removeNode('${node.node_id}')">删除</button>
            `;
        } else if (node.trust_status === 'waiting_approval') {
            actions = '<span style="opacity:0.6; font-size:12px">等待审批...</span>';
        } else {
            actions = '<span style="opacity:0.4">—</span>';
        }

        return `
            <tr style="${node.trust_status === 'kicked' ? 'opacity:0.5' : ''}">
                <td>${node.name || node.node_id}${selfBadge}</td>
                <td><span class="tag ${trust.class}">${trust.icon} ${trust.text}</span></td>
                <td><span class="tag ${statusClass}">${statusText}</span></td>
                <td><span class="tag ${modeClass}">${modeMap[node.mode] || node.mode}</span></td>
                <td><span class="tag ${node.connectable ? 'green' : 'red'}">${node.connectable ? '✓ 有' : '✗ 无'}</span></td>
                <td class="mono" style="font-size:11px">${fingerprint}</td>
                <td class="mono" style="font-size:12px">${addr}</td>
                <td class="mono">${cpu}</td>
                <td class="mono">${mem}</td>
                <td>${lastSeen}</td>
                <td style="white-space:nowrap">${actions}</td>
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

    // ── 加入网络 ──

    _showJoinDialog() {
        const dialog = document.getElementById('join-network-dialog');
        if (dialog) dialog.style.display = 'flex';
    },

    _hideJoinDialog() {
        const dialog = document.getElementById('join-network-dialog');
        if (dialog) dialog.style.display = 'none';
        const msg = document.getElementById('join-message');
        if (msg) msg.textContent = '';
    },

    async _joinNetwork() {
        const host = document.getElementById('join-node-host')?.value?.trim();
        const msgEl = document.getElementById('join-message');
        const btn = document.getElementById('join-submit-btn');

        if (!host) {
            if (msgEl) { msgEl.textContent = '请输入目标节点地址'; msgEl.className = 'form-message error'; }
            return;
        }

        if (btn) btn.disabled = true;
        if (msgEl) { msgEl.textContent = '正在连接...'; msgEl.className = 'form-message'; }

        try {
            const result = await API.post('/api/v1/nodes/join', { host });
            if (result.error) {
                if (msgEl) { msgEl.textContent = result.error; msgEl.className = 'form-message error'; }
            } else if (result.status === 'trusted') {
                if (msgEl) { msgEl.textContent = '✓ 已成功加入网络！'; msgEl.className = 'form-message success'; }
                setTimeout(() => {
                    this._hideJoinDialog();
                    this._fetchNodes();
                }, 1000);
            } else {
                if (msgEl) { msgEl.textContent = '✓ 申请已提交，等待管理员审批'; msgEl.className = 'form-message success'; }
                setTimeout(() => {
                    this._hideJoinDialog();
                    this._fetchNodes();
                    this._fetchJoinStatus();
                }, 1500);
            }
        } catch (err) {
            if (msgEl) { msgEl.textContent = `连接失败: ${err.message}`; msgEl.className = 'form-message error'; }
        } finally {
            if (btn) btn.disabled = false;
        }
    },

    // ── 审批操作 ──

    async _approveNode(nodeId) {
        try {
            const result = await API.post(`/api/v1/nodes/${nodeId}/approve`);
            if (result.error) {
                alert(result.error);
            } else {
                this._fetchNodes();
            }
        } catch (err) {
            alert(`操作失败: ${err.message}`);
        }
    },

    async _rejectNode(nodeId) {
        if (!confirm('确定拒绝该节点的加入申请？')) return;

        try {
            const result = await API.post(`/api/v1/nodes/${nodeId}/reject`);
            if (result.error) {
                alert(result.error);
            } else {
                this._fetchNodes();
            }
        } catch (err) {
            alert(`操作失败: ${err.message}`);
        }
    },

    async _kickNode(nodeId, name) {
        if (!confirm(`确定将节点 "${name}" 踢出网络？\n\n该操作会传播到整个网络，所有节点都将拒绝与该节点通信。`)) return;

        try {
            const result = await API.post(`/api/v1/nodes/${nodeId}/kick`);
            if (result.error) {
                alert(result.error);
            } else {
                this._fetchNodes();
            }
        } catch (err) {
            alert(`操作失败: ${err.message}`);
        }
    },

    async _removeNode(nodeId) {
        if (!confirm('确定从本地删除该节点记录？')) return;

        try {
            const result = await API.delete(`/api/v1/nodes/${nodeId}`);
            if (result.error) {
                alert(result.error);
            } else {
                this._fetchNodes();
            }
        } catch (err) {
            alert(`操作失败: ${err.message}`);
        }
    },

    // ── 详情弹窗（保留扩展性） ──

    _hideDetailDialog() {
        const dialog = document.getElementById('node-detail-dialog');
        if (dialog) dialog.style.display = 'none';
    },
};
