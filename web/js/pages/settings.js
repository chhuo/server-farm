/**
 * 系统设置页面
 * 提供配置管理、密码修改、命令黑名单编辑功能。
 */

const SettingsPage = {
    title: '系统设置',

    render() {
        return `
            <div class="settings-grid">
                <!-- 节点信息 -->
                <div class="panel settings-section">
                    <h3 class="section-title">🖥️ 节点信息</h3>
                    <div id="settings-node-info" class="settings-items">
                        <div class="loading"><div class="loading-spinner"></div> 加载中...</div>
                    </div>
                </div>

                <!-- 网络配置 -->
                <div class="panel settings-section">
                    <h3 class="section-title">🌐 网络配置</h3>
                    <div id="settings-network" class="settings-items">
                        <div class="loading"><div class="loading-spinner"></div> 加载中...</div>
                    </div>
                </div>

                <!-- 安全设置 -->
                <div class="panel settings-section">
                    <h3 class="section-title">🔒 安全设置</h3>
                    <div class="settings-items">
                        <div class="setting-row">
                            <div class="setting-label">修改管理员密码</div>
                            <button class="btn-primary" onclick="SettingsPage._showPasswordDialog()"
                                    style="padding:6px 16px; font-size:0.85rem">修改密码</button>
                        </div>
                    </div>
                </div>

                <!-- 日志设置 -->
                <div class="panel settings-section">
                    <h3 class="section-title">📋 日志设置</h3>
                    <div id="settings-logging" class="settings-items">
                        <div class="loading"><div class="loading-spinner"></div> 加载中...</div>
                    </div>
                </div>

                <!-- 命令黑名单 -->
                <div class="panel settings-section">
                    <h3 class="section-title">🚫 命令黑名单</h3>
                    <div id="settings-blacklist" class="settings-items">
                        <div class="loading"><div class="loading-spinner"></div> 加载中...</div>
                    </div>
                </div>
            </div>

            <!-- 修改密码弹窗 -->
            <div class="dialog-overlay" id="password-dialog" style="display:none">
                <div class="dialog">
                    <div class="dialog-header">
                        <h3>修改密码</h3>
                        <button class="dialog-close" onclick="SettingsPage._closePasswordDialog()">×</button>
                    </div>
                    <div class="dialog-body">
                        <div class="form-group">
                            <label class="form-label">原密码</label>
                            <input type="password" class="form-input" id="old-password">
                        </div>
                        <div class="form-group">
                            <label class="form-label">新密码（至少 6 位）</label>
                            <input type="password" class="form-input" id="new-password">
                        </div>
                        <div class="form-group">
                            <label class="form-label">确认新密码</label>
                            <input type="password" class="form-input" id="confirm-password">
                        </div>
                        <div class="login-error" id="password-error" style="display:none"></div>
                    </div>
                    <div class="dialog-footer">
                        <button class="btn-secondary" onclick="SettingsPage._closePasswordDialog()">取消</button>
                        <button class="btn-primary" onclick="SettingsPage._changePassword()">确认修改</button>
                    </div>
                </div>
            </div>
        `;
    },

    mount() {
        this._loadConfig();
        this._loadBlacklist();
    },

    destroy() { },

    async _loadConfig() {
        try {
            const data = await API.get('/api/v1/config');
            const cfg = data.config || {};

            // 节点信息
            const nodeEl = document.getElementById('settings-node-info');
            if (nodeEl) {
                nodeEl.innerHTML = `
                    ${this._renderSetting('节点 ID', cfg.node?.id || '--', 'mono')}
                    ${this._renderEditableTextSetting('节点名称', cfg.node?.name || '', 'node.name', '留空使用主机名')}
                    ${this._renderSetting('运行模式', cfg.node?.mode || '--')}
                    ${this._renderToggleSetting('有公网 IP（可被其他节点直连）', cfg.node?.connectable ?? false, 'node.connectable')}
                    ${this._renderEditableTextSetting('公网地址', cfg.node?.public_url || '', 'node.public_url', 'https://your-server.example.com')}
                    ${this._renderEditableTextSetting('面板名称', cfg.app?.name || 'ServerFarm', 'app.name', '自定义面板名称')}
                    ${this._renderSetting('应用版本', `v${cfg.app?.version}`)}
                    ${this._renderSetting('运行环境', cfg.app?.env || '--')}
                `;
            }

            // 日志设置
            const logEl = document.getElementById('settings-logging');
            if (logEl) {
                const levelOptions = ['debug', 'info', 'warning', 'error'];
                const currentLevel = cfg.logging?.level || 'info';
                logEl.innerHTML = `
                    <div class="setting-row">
                        <div class="setting-label">日志级别</div>
                        <div class="setting-value-edit">
                            <select class="form-input setting-input" data-key="logging.level" style="width:auto">
                                ${levelOptions.map(l => `<option value="${l}" ${l === currentLevel ? 'selected' : ''}>${l}</option>`).join('')}
                            </select>
                            <button class="btn-sm" onclick="SettingsPage._saveSelectSetting('logging.level', this)">保存</button>
                        </div>
                    </div>
                    ${this._renderSetting('控制台输出', cfg.logging?.console_enabled ? '开启' : '关闭')}
                    ${this._renderSetting('文件输出', cfg.logging?.file_enabled ? '开启' : '关闭')}
                `;
            }

            // 网络配置
            const netEl = document.getElementById('settings-network');
            if (netEl) {
                netEl.innerHTML = `
                    ${this._renderEditableSetting('同步间隔', cfg.peer?.sync_interval, 'peer.sync_interval', '秒')}
                    ${this._renderEditableSetting('心跳间隔', cfg.peer?.heartbeat_interval, 'peer.heartbeat_interval', '秒')}
                    ${this._renderEditableSetting('请求超时', cfg.peer?.timeout, 'peer.timeout', '秒')}
                    ${this._renderEditableSetting('Gossip 扇出', cfg.peer?.max_fanout, 'peer.max_fanout', '')}
                    ${this._renderEditableSetting('故障转移阈值', cfg.peer?.max_heartbeat_failures, 'peer.max_heartbeat_failures', '次')}
                `;
            }
        } catch (err) {
            console.error('配置加载失败:', err);
        }
    },

    _renderSetting(label, value, cls = '') {
        return `
            <div class="setting-row">
                <div class="setting-label">${label}</div>
                <div class="setting-value ${cls}">${value}</div>
            </div>
        `;
    },

    _renderEditableTextSetting(label, value, configKey, placeholder = '') {
        return `
            <div class="setting-row">
                <div class="setting-label">${label}</div>
                <div class="setting-value-edit">
                    <input type="text" class="form-input setting-input"
                           value="${value}" placeholder="${placeholder}" data-key="${configKey}"
                           style="min-width:220px">
                    <button class="btn-sm" onclick="SettingsPage._saveTextSetting('${configKey}', this)">保存</button>
                </div>
            </div>
        `;
    },

    _renderToggleSetting(label, value, configKey) {
        const checked = value ? 'checked' : '';
        return `
            <div class="setting-row">
                <div class="setting-label">${label}</div>
                <div class="setting-value-edit">
                    <label class="toggle-switch">
                        <input type="checkbox" ${checked} onchange="SettingsPage._saveToggleSetting('${configKey}', this)">
                        <span class="toggle-slider"></span>
                    </label>
                    <span class="setting-unit" style="margin-left:8px">${value ? '是' : '否'}</span>
                </div>
            </div>
        `;
    },

    _renderEditableSetting(label, value, configKey, unit) {
        return `
            <div class="setting-row">
                <div class="setting-label">${label}</div>
                <div class="setting-value-edit">
                    <input type="number" class="form-input setting-input"
                           value="${value}" data-key="${configKey}">
                    <span class="setting-unit">${unit}</span>
                    <button class="btn-sm" onclick="SettingsPage._saveSetting('${configKey}', this)">保存</button>
                </div>
            </div>
        `;
    },

    async _saveTextSetting(key, btnEl) {
        const row = btnEl.closest('.setting-value-edit');
        const input = row.querySelector('input');
        const value = input.value.trim();

        btnEl.textContent = '...';
        try {
            await API.post('/api/v1/config/update', { updates: { [key]: value } });
            btnEl.textContent = '✓';
            setTimeout(() => btnEl.textContent = '保存', 1500);

            // 如果修改的是面板名称，实时更新页面品牌
            if (key === 'app.name' && value) {
                window._branding.name = value;
                document.title = value;
                const logoText = document.getElementById('logo-text');
                if (logoText) logoText.textContent = value;
            }
        } catch (err) {
            btnEl.textContent = '✗';
            setTimeout(() => btnEl.textContent = '保存', 1500);
        }
    },

    async _saveSelectSetting(key, btnEl) {
        const row = btnEl.closest('.setting-value-edit');
        const select = row.querySelector('select');
        const value = select.value;

        btnEl.textContent = '...';
        try {
            await API.post('/api/v1/config/update', { updates: { [key]: value } });
            btnEl.textContent = '✓';
            setTimeout(() => btnEl.textContent = '保存', 1500);
        } catch (err) {
            btnEl.textContent = '✗';
            setTimeout(() => btnEl.textContent = '保存', 1500);
        }
    },

    async _saveToggleSetting(key, checkboxEl) {
        const value = checkboxEl.checked;
        const unitEl = checkboxEl.closest('.setting-value-edit').querySelector('.setting-unit');
        try {
            await API.post('/api/v1/config/update', { updates: { [key]: value } });
            if (unitEl) unitEl.textContent = value ? '是' : '否';
        } catch (err) {
            // 回滚
            checkboxEl.checked = !value;
            console.error('保存失败:', err);
        }
    },

    async _saveSetting(key, btnEl) {
        const row = btnEl.closest('.setting-value-edit');
        const input = row.querySelector('input');
        const value = Number(input.value);

        btnEl.textContent = '...';
        try {
            await API.post('/api/v1/config/update', {
                updates: { [key]: value },
            });
            btnEl.textContent = '✓';
            setTimeout(() => btnEl.textContent = '保存', 1500);
        } catch (err) {
            btnEl.textContent = '✗';
            setTimeout(() => btnEl.textContent = '保存', 1500);
        }
    },

    async _loadBlacklist() {
        try {
            const data = await API.get('/api/v1/config/blacklist');
            const list = data.blacklist || [];
            const el = document.getElementById('settings-blacklist');
            if (!el) return;

            el.innerHTML = `
                <div class="blacklist-list">
                    ${list.map((item, i) => `
                        <div class="blacklist-item">
                            <code>${item}</code>
                            <button class="btn-sm btn-danger" onclick="SettingsPage._removeBlacklistItem(${i})">×</button>
                        </div>
                    `).join('')}
                </div>
                <div class="blacklist-add">
                    <input type="text" class="form-input" id="new-blacklist-item"
                           placeholder="输入要阻止的命令或关键词">
                    <button class="btn-primary btn-sm" onclick="SettingsPage._addBlacklistItem()">添加</button>
                </div>
            `;

            // 保存当前列表
            this._currentBlacklist = list;
        } catch (err) {
            console.error('黑名单加载失败:', err);
        }
    },

    _currentBlacklist: [],

    async _addBlacklistItem() {
        const input = document.getElementById('new-blacklist-item');
        const item = input.value.trim();
        if (!item) return;

        const newList = [...this._currentBlacklist, item];
        await this._saveBlacklist(newList);
        input.value = '';
    },

    async _removeBlacklistItem(index) {
        const newList = this._currentBlacklist.filter((_, i) => i !== index);
        await this._saveBlacklist(newList);
    },

    async _saveBlacklist(list) {
        try {
            await API.post('/api/v1/config/blacklist', { blacklist: list });
            this._loadBlacklist();
        } catch (err) {
            console.error('黑名单保存失败:', err);
        }
    },

    _showPasswordDialog() {
        document.getElementById('password-dialog').style.display = 'flex';
    },

    _closePasswordDialog() {
        document.getElementById('password-dialog').style.display = 'none';
        document.getElementById('old-password').value = '';
        document.getElementById('new-password').value = '';
        document.getElementById('confirm-password').value = '';
    },

    async _changePassword() {
        const oldPass = document.getElementById('old-password').value;
        const newPass = document.getElementById('new-password').value;
        const confirmPass = document.getElementById('confirm-password').value;
        const errorEl = document.getElementById('password-error');

        if (!oldPass || !newPass) {
            errorEl.textContent = '请填写所有字段';
            errorEl.style.display = 'block';
            return;
        }

        if (newPass !== confirmPass) {
            errorEl.textContent = '两次输入的新密码不一致';
            errorEl.style.display = 'block';
            return;
        }

        if (newPass.length < 6) {
            errorEl.textContent = '新密码至少 6 位';
            errorEl.style.display = 'block';
            return;
        }

        try {
            const result = await API.post('/api/v1/auth/change-password', {
                old_password: oldPass,
                new_password: newPass,
            });

            if (result.error) {
                errorEl.textContent = result.error;
                errorEl.style.display = 'block';
            } else {
                this._closePasswordDialog();
                alert('密码已修改成功！');
            }
        } catch (err) {
            errorEl.textContent = '请求失败: ' + err.message;
            errorEl.style.display = 'block';
        }
    },
};
