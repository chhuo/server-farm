/**
 * 登录页面
 * 全屏居中的登录表单，暗色主题，带 NodePanel 品牌。
 */

const LoginPage = {
    title: '登录',
    _isFullScreen: true,

    render() {
        return `
            <div class="login-container">
                <div class="login-card">
                    <div class="login-logo">
                        <div class="login-logo-icon">⬡</div>
                        <h1 class="login-title">NodePanel</h1>
                        <p class="login-subtitle">分布式服务器控制面板</p>
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

                    <div class="login-footer">
                        <span id="setup-hint" style="display:none">
                            首次启动？请查看终端输出获取初始密码
                        </span>
                    </div>
                </div>
            </div>
        `;
    },

    mount() {
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
                const hint = document.getElementById('setup-hint');
                if (hint) hint.style.display = 'block';
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
                // 登录成功，跳转到面板
                window.location.hash = '#/dashboard';
                // 需要刷新以加载侧边栏
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
