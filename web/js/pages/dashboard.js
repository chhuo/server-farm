/**
 * 仪表盘页面
 * 展示本机系统状态：CPU、内存、磁盘、网络、系统信息
 */

const DashboardPage = {
    title: '仪表盘',
    _refreshTimer: null,

    render() {
        return `
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
        this._fetchData();
        // 每 3 秒刷新
        this._refreshTimer = setInterval(() => this._fetchData(), 3000);
    },

    destroy() {
        if (this._refreshTimer) {
            clearInterval(this._refreshTimer);
            this._refreshTimer = null;
        }
    },

    async _fetchData() {
        try {
            const data = await API.getSystemInfo();
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
        }

        // 磁盘（取第一个分区的概要）
        const diskEl = document.getElementById('disk-percent');
        const diskInfoEl = document.getElementById('disk-info');
        const diskBar = document.getElementById('disk-bar');
        if (diskEl && data.disk && data.disk.partitions.length > 0) {
            const mainDisk = data.disk.partitions[0];
            diskEl.textContent = `${mainDisk.percent}%`;
            diskInfoEl.textContent = `${mainDisk.used_gb} / ${mainDisk.total_gb} GB`;
            if (diskBar) {
                diskBar.style.width = `${mainDisk.percent}%`;
                diskBar.className = `progress-bar-fill ${this._getColorClass(mainDisk.percent)}`;
            }
        }

        // 网络
        const netSentEl = document.getElementById('net-sent');
        const netRecvEl = document.getElementById('net-recv');
        if (netSentEl && data.network) {
            netSentEl.textContent = `↑ ${this._formatBytes(data.network.bytes_sent)}`;
            netRecvEl.textContent = `↓ ${this._formatBytes(data.network.bytes_recv)}`;
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
        }

        // 运行时间
        const uptimeEl = document.getElementById('sys-uptime');
        if (uptimeEl && data.uptime) {
            uptimeEl.textContent = this._formatUptime(data.uptime);
        }

        // 磁盘分区表
        const diskBody = document.getElementById('disk-partitions-body');
        if (diskBody && data.disk) {
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
        }
    },

    _getColorClass(percent) {
        if (percent >= 90) return 'red';
        if (percent >= 70) return 'yellow';
        return 'green';
    },

    _formatBytes(bytes) {
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
};
