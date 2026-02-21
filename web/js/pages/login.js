/**
 * 登录页面
 * 全屏居中的登录表单，暗色主题，品牌名称从配置动态获取。
 */

const LoginPage = {
    title: '登录',
    _isFullScreen: true,
    _setupRequired: false,

    render() {
        return `
            <div class="login-container">
                <div class="login-card">
                    <div class="login-logo">
                        <div class="login-logo-icon">⬡</div>
                        <h1 class="login-title" id="login-brand-title">NodePanel</h1>
                        <p class="login-subtitle">分布式服务器管理面板</p>
                    </div>

                    <div id="initial-creds-box" style="display:none"
                         class="initial-creds-box">
                        <div class="initial-creds-title">⚠️ 首次启动 — 默认管理员凭据</div>
                        <div class="initial-creds-row">
                            <span class="initial-creds-label">用户名</span>
                            <code id="initial-creds-user" class="initial-creds-value"></code>
                        </div>
                        <div class="initial-creds-row">
                            <span class="initial-creds-label">密码</span>
                            <code id="initial-creds-pass" class="initial-creds-value"></code>
                        </div>
                        <div class="initial-creds-tip">登录后建议立即修改密码</div>
                    </div>

                    <form class="login-form" id="login-form">
                        <div class="form-group">
                            <label class="form-label">用户名</label>
                            <input type="text" class="form-input" id="login-user"
                                   placeholder="admin" autocomplete="username" autofocus>
                        </div>
                        <div class="form-group">
                            <label class="form-label">密码</label>
                            <input type="password" class="form-input" id="login-pass"
                                   placeholder="请输入密码" autocomplete="current-password">
                        </div>

                        <div class="login-error" id="login-error" style="display:none"></div>

                        <button type="submit" class="btn-primary login-btn" id="login-btn">
                            登 录
                        </button>
                    </form>
                </div>
            </div>
        `;
    },

    mount() {
        // 应用品牌名称
        const brandTitle = document.getElementById('login-brand-title');
        if (brandTitle && window._branding) {
            brandTitle.textContent = window._branding.name;
        }

        const form = document.getElementById('login-form');
        if (form) {
            form.addEventListener('submit', (e) => {
                e.preventDefault();
                this._doLogin();
            });
        }

        // 检查是否首次设置
        this._checkSetup();
    },

    destroy() { },

    async _checkSetup() {
        try {
            const data = await API.get('/api/v1/auth/status');
            if (data.setup_required) {
                this._setupRequired = true;
                const box = document.getElementById('initial-creds-box');
                const userEl = document.getElementById('initial-creds-user');
                const passEl = document.getElementById('initial-creds-pass');
                if (box) box.style.display = 'block';
                if (userEl) userEl.textContent = data.initial_user || 'admin';
                if (passEl) passEl.textContent = data.initial_password || '';

                // 自动填入登录框
                const loginUser = document.getElementById('login-user');
                const loginPass = document.getElementById('login-pass');
                if (loginUser) loginUser.value = data.initial_user || 'admin';
                if (loginPass) loginPass.value = data.initial_password || '';
            }
        } catch { }
    },

    async _doLogin() {
        const userEl = document.getElementById('login-user');
        const passEl = document.getElementById('login-pass');
        const errorEl = document.getElementById('login-error');
        const btnEl = document.getElementById('login-btn');

        const username = userEl.value.trim();
        const password = passEl.value;

        if (!username || !password) {
            this._showError('请输入用户名和密码');
            return;
        }

        btnEl.disabled = true;
        btnEl.textContent = '登录中...';

        try {
            const result = await API.post('/api/v1/auth/login', {
                username, password,
            });

            if (result.error) {
                this._showError(result.error);
                btnEl.disabled = false;
                btnEl.textContent = '登 录';
            } else {
                // 登录成功，记录是否需要提示修改密码
                if (this._setupRequired) {
                    sessionStorage.setItem('showChangePassword', '1');
                }
                // 跳转到面板
                window.location.hash = '#/dashboard';
                window.location.reload();
            }
        } catch (err) {
            this._showError('网络错误: ' + err.message);
            btnEl.disabled = false;
            btnEl.textContent = '登 录';
        }
    },

    _showError(msg) {
        const errorEl = document.getElementById('login-error');
        if (errorEl) {
            errorEl.textContent = msg;
            errorEl.style.display = 'block';
            setTimeout(() => errorEl.style.display = 'none', 4000);
        }
    },
};
