/**
 * 仪表盘页面
 * 展示系统状态：CPU、内存、磁盘、网络、系统信息
 * 支持切换查看任意节点的仪表盘数据
 */

const DashboardPage = {
    title: '仪表盘',
    _refreshTimer: null,
    _selectedNodeId: null,  // null = 本机
    _nodesList: [],

    render() {
        return `
            <div class="node-selector-bar" id="node-selector-bar">
                <span class="node-selector-label">查看节点：</span>
                <select class="node-selector-select" id="node-selector">
                    <option value="">本机 (实时)</option>
                </select>
                <span class="node-selector-hint" id="node-selector-hint"></span>
            </div>

            <div class="stats-grid" id="stats-grid">
                <div class="stat-card blue" id="card-cpu">
                    <div class="stat-card-header">
                        <span class="stat-card-title">CPU 使用率</span>
                        <span class="stat-card-icon">⊞</span>
                    </div>
                    <div class="stat-card-value" id="cpu-percent">--</div>
                    <div class="stat-card-sub" id="cpu-info">加载中...</div>
                    <div class="core-bars" id="cpu-cores"></div>
                </div>

                <div class="stat-card green" id="card-memory">
                    <div class="stat-card-header">
                        <span class="stat-card-title">内存使用</span>
                        <span class="stat-card-icon">⊡</span>
                    </div>
                    <div class="stat-card-value" id="mem-percent">--</div>
                    <div class="stat-card-sub" id="mem-info">加载中...</div>
                    <div class="progress-bar">
                        <div class="progress-bar-fill green" id="mem-bar" style="width:0%"></div>
                    </div>
                </div>

                <div class="stat-card purple" id="card-disk">
                    <div class="stat-card-header">
                        <span class="stat-card-title">磁盘使用</span>
                        <span class="stat-card-icon">⊟</span>
                    </div>
                    <div class="stat-card-value" id="disk-percent">--</div>
                    <div class="stat-card-sub" id="disk-info">加载中...</div>
                    <div class="progress-bar">
                        <div class="progress-bar-fill blue" id="disk-bar" style="width:0%"></div>
                    </div>
                </div>

                <div class="stat-card red" id="card-network">
                    <div class="stat-card-header">
                        <span class="stat-card-title">网络流量</span>
                        <span class="stat-card-icon">⊠</span>
                    </div>
                    <div class="stat-card-value" id="net-sent">--</div>
                    <div class="stat-card-sub" id="net-recv">加载中...</div>
                </div>
            </div>

            <div class="info-grid">
                <div class="panel">
                    <div class="panel-header">
                        <span class="panel-title">系统信息</span>
                        <span class="tag blue" id="sys-uptime">--</span>
                    </div>
                    <div class="panel-body" id="system-info-body">
                        <div class="loading">
                            <div class="loading-spinner"></div>
                            正在采集系统信息...
                        </div>
                    </div>
                </div>

                <div class="panel">
                    <div class="panel-header">
                        <span class="panel-title">磁盘分区</span>
                    </div>
                    <div class="panel-body" id="disk-partitions-body">
                        <div class="loading">
                            <div class="loading-spinner"></div>
                            加载中...
                        </div>
                    </div>
                </div>
            </div>
        `;
    },

    mount() {
        this._selectedNodeId = null;
        this._loadNodeList();
        this._fetchData();
        // 每 3 秒刷新
        this._refreshTimer = setInterval(() => this._fetchData(), 3000);

        // 绑定节点选择事件
        const selector = document.getElementById('node-selector');
        if (selector) {
            selector.addEventListener('change', (e) => {
                this._selectedNodeId = e.target.value || null;
                this._updateHint();
                this._fetchData();
            });
        }
    },

    destroy() {
        if (this._refreshTimer) {
            clearInterval(this._refreshTimer);
            this._refreshTimer = null;
        }
    },

    async _loadNodeList() {
        try {
            const data = await API.get('/api/v1/nodes');
            this._nodesList = data.nodes || [];
            this._populateSelector();
        } catch (err) {
            console.error('节点列表加载失败:', err);
        }
    },

    _populateSelector() {
        const selector = document.getElementById('node-selector');
        if (!selector) return;

        // 保留第一个"本机"选项
        selector.innerHTML = '<option value="">本机 (实时)</option>';

        for (const node of this._nodesList) {
            if (node.is_self) continue;
            const name = node.name || node.node_id;
            const status = node.status === 'online' ? '🟢' : '🔴';
            const opt = document.createElement('option');
            opt.value = node.node_id;
            opt.textContent = `${status} ${name}`;
            selector.appendChild(opt);
        }

        // 恢复选中状态
        if (this._selectedNodeId) {
            selector.value = this._selectedNodeId;
        }
        this._updateHint();
    },

    _updateHint() {
        const hintEl = document.getElementById('node-selector-hint');
        if (!hintEl) return;

        if (this._selectedNodeId) {
            const node = this._nodesList.find(n => n.node_id === this._selectedNodeId);
            if (node) {
                const lastSeen = node.last_seen ? this._formatTimeAgo(node.last_seen) : '未知';
                hintEl.textContent = `📡 同步数据 · 最后更新: ${lastSeen}`;
                hintEl.className = 'node-selector-hint remote';
            }
        } else {
            hintEl.textContent = '';
            hintEl.className = 'node-selector-hint';
        }
    },

    async _fetchData() {
        try {
            let data;

            if (this._selectedNodeId) {
                // 远程节点：从 nodes API 获取同步的 system_info
                const nodeData = await API.get(`/api/v1/nodes/${this._selectedNodeId}`);
                data = nodeData.system_info || {};
                // 同时刷新节点列表以获取最新状态
                this._loadNodeList();
            } else {
                // 本机：直接获取实时系统信息
                data = await API.getSystemInfo();
            }

            Store.set('systemInfo', data);
            this._updateUI(data);
        } catch (err) {
            console.error('仪表盘数据加载失败:', err);
        }
    },

    _updateUI(data) {
        // CPU
        const cpuEl = document.getElementById('cpu-percent');
        const cpuInfoEl = document.getElementById('cpu-info');
        const cpuCoresEl = document.getElementById('cpu-cores');
        if (cpuEl && data.cpu) {
            cpuEl.textContent = `${data.cpu.percent}%`;
            cpuInfoEl.textContent = `${data.cpu.count_logical} 核 / ${data.cpu.frequency_mhz} MHz`;

            // CPU 核心条
            if (cpuCoresEl && data.cpu.percent_per_core) {
                cpuCoresEl.innerHTML = data.cpu.percent_per_core.map(p => {
                    const color = p > 80 ? 'var(--accent-red)' :
                        p > 50 ? 'var(--accent-yellow)' :
                            'var(--accent-cyan)';
                    return `<div class="core-bar" style="height:${Math.max(p, 5)}%;background:${color}"></div>`;
                }).join('');
            }
        } else if (cpuEl) {
            cpuEl.textContent = '--';
            if (cpuInfoEl) cpuInfoEl.textContent = '暂无数据';
            if (cpuCoresEl) cpuCoresEl.innerHTML = '';
        }

        // 内存
        const memEl = document.getElementById('mem-percent');
        const memInfoEl = document.getElementById('mem-info');
        const memBar = document.getElementById('mem-bar');
        if (memEl && data.memory) {
            memEl.textContent = `${data.memory.percent}%`;
            memInfoEl.textContent = `${data.memory.used_mb} / ${data.memory.total_mb} MB`;
            if (memBar) {
                memBar.style.width = `${data.memory.percent}%`;
                memBar.className = `progress-bar-fill ${this._getColorClass(data.memory.percent)}`;
            }
        } else if (memEl) {
            memEl.textContent = '--';
            if (memInfoEl) memInfoEl.textContent = '暂无数据';
            if (memBar) memBar.style.width = '0%';
        }

        // 磁盘（取第一个分区的概要）
        const diskEl = document.getElementById('disk-percent');
        const diskInfoEl = document.getElementById('disk-info');
        const diskBar = document.getElementById('disk-bar');
        if (diskEl && data.disk && data.disk.partitions && data.disk.partitions.length > 0) {
            const mainDisk = data.disk.partitions[0];
            diskEl.textContent = `${mainDisk.percent}%`;
            diskInfoEl.textContent = `${mainDisk.used_gb} / ${mainDisk.total_gb} GB`;
            if (diskBar) {
                diskBar.style.width = `${mainDisk.percent}%`;
                diskBar.className = `progress-bar-fill ${this._getColorClass(mainDisk.percent)}`;
            }
        } else if (diskEl) {
            diskEl.textContent = '--';
            if (diskInfoEl) diskInfoEl.textContent = '暂无数据';
            if (diskBar) diskBar.style.width = '0%';
        }

        // 网络
        const netSentEl = document.getElementById('net-sent');
        const netRecvEl = document.getElementById('net-recv');
        if (netSentEl && data.network) {
            netSentEl.textContent = `↑ ${this._formatBytes(data.network.bytes_sent)}`;
            netRecvEl.textContent = `↓ ${this._formatBytes(data.network.bytes_recv)}`;
        } else if (netSentEl) {
            netSentEl.textContent = '↑ --';
            if (netRecvEl) netRecvEl.textContent = '↓ --';
        }

        // 系统信息
        const sysBody = document.getElementById('system-info-body');
        if (sysBody && data.system) {
            sysBody.innerHTML = `
                <div class="info-row">
                    <span class="info-label">主机名</span>
                    <span class="info-value">${data.system.hostname}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">操作系统</span>
                    <span class="info-value">${data.system.os}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">架构</span>
                    <span class="info-value">${data.system.architecture}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Python</span>
                    <span class="info-value">${data.system.python_version}</span>
                </div>
            `;
        } else if (sysBody) {
            sysBody.innerHTML = `
                <div class="placeholder-page" style="padding:20px">
                    <div class="placeholder-icon">⊡</div>
                    <div class="placeholder-desc">暂无系统信息（节点可能离线）</div>
                </div>
            `;
        }

        // 运行时间
        const uptimeEl = document.getElementById('sys-uptime');
        if (uptimeEl && data.uptime) {
            uptimeEl.textContent = this._formatUptime(data.uptime);
        } else if (uptimeEl) {
            uptimeEl.textContent = '--';
        }

        // 磁盘分区表
        const diskBody = document.getElementById('disk-partitions-body');
        if (diskBody && data.disk && data.disk.partitions && data.disk.partitions.length > 0) {
            diskBody.innerHTML = `
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>设备</th>
                            <th>挂载点</th>
                            <th>类型</th>
                            <th>已用</th>
                            <th>总计</th>
                            <th>使用率</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${data.disk.partitions.map(p => `
                            <tr>
                                <td>${p.device}</td>
                                <td>${p.mountpoint}</td>
                                <td>${p.fstype}</td>
                                <td>${p.used_gb} GB</td>
                                <td>${p.total_gb} GB</td>
                                <td><span class="tag ${this._getColorClass(p.percent)}">${p.percent}%</span></td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
        } else if (diskBody) {
            diskBody.innerHTML = `
                <div class="placeholder-page" style="padding:20px">
                    <div class="placeholder-desc">暂无磁盘分区数据</div>
                </div>
            `;
        }
    },

    _getColorClass(percent) {
        if (percent >= 90) return 'red';
        if (percent >= 70) return 'yellow';
        return 'green';
    },

    _formatBytes(bytes) {
        if (bytes == null) return '--';
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
        return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
    },

    _formatUptime(seconds) {
        const days = Math.floor(seconds / 86400);
        const hours = Math.floor((seconds % 86400) / 3600);
        const mins = Math.floor((seconds % 3600) / 60);
        if (days > 0) return `运行 ${days}天 ${hours}时`;
        if (hours > 0) return `运行 ${hours}时 ${mins}分`;
        return `运行 ${mins}分`;
    },

    _formatTimeAgo(timestamp) {
        const diff = Date.now() / 1000 - timestamp;
        if (diff < 10) return '刚刚';
        if (diff < 60) return `${Math.floor(diff)}秒前`;
        if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`;
        return `${Math.floor(diff / 86400)}天前`;
    },
};
