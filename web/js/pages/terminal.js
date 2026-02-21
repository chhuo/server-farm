/**
 * 远程终端页面
 * 使用 xterm.js + WebSocket 实现真实终端体验。
 * 支持持久 Shell 会话（cd 保持、环境变量保持、Tab 补全、Ctrl+C 等）。
 */

const TerminalPage = {
    title: '远程终端',
    _term: null,
    _ws: null,
    _fitAddon: null,
    _resizeObserver: null,
    _reconnectTimer: null,

    render() {
        return `
            <div class="terminal-header">
                <div class="terminal-target">
                    <label class="form-label">目标节点</label>
                    <select class="form-input terminal-select" id="term-target">
                        <option value="">加载中...</option>
                    </select>
                </div>
                <div class="terminal-info">
                    <span class="tag blue" id="term-mode">--</span>
                    <span class="tag green" id="term-status">--</span>
                    <span class="tag" id="term-conn-status" style="display:none">--</span>
                    <button class="btn-sm" id="term-reconnect-btn" style="display:none">重新连接</button>
                </div>
            </div>
            <div class="terminal-xterm-container" id="term-container"></div>
        `;
    },

    mount() {
        this._loadNodes();
        this._initTerminal();

        // 重新连接按钮
        const reconnBtn = document.getElementById('term-reconnect-btn');
        if (reconnBtn) {
            reconnBtn.addEventListener('click', () => this._connect());
        }
    },

    destroy() {
        this._disconnect();
        if (this._resizeObserver) {
            this._resizeObserver.disconnect();
            this._resizeObserver = null;
        }
        if (this._term) {
            this._term.dispose();
            this._term = null;
        }
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
    },

    _initTerminal() {
        const container = document.getElementById('term-container');
        if (!container || typeof Terminal === 'undefined') return;

        // 创建 xterm.js 实例
        this._term = new Terminal({
            cursorBlink: true,
            cursorStyle: 'bar',
            fontSize: 14,
            fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace",
            theme: {
                background: '#0a0e14',
                foreground: '#e0e0e0',
                cursor: '#10b981',
                cursorAccent: '#0a0e14',
                selectionBackground: 'rgba(59, 130, 246, 0.3)',
                black: '#0a0e14',
                red: '#ef4444',
                green: '#10b981',
                yellow: '#f59e0b',
                blue: '#3b82f6',
                magenta: '#a855f7',
                cyan: '#06b6d4',
                white: '#e0e0e0',
                brightBlack: '#6b7280',
                brightRed: '#f87171',
                brightGreen: '#34d399',
                brightYellow: '#fbbf24',
                brightBlue: '#60a5fa',
                brightMagenta: '#c084fc',
                brightCyan: '#22d3ee',
                brightWhite: '#ffffff',
            },
            allowProposedApi: true,
            scrollback: 5000,
        });

        // Fit addon — 自动适配容器尺寸
        if (typeof FitAddon !== 'undefined') {
            this._fitAddon = new FitAddon.FitAddon();
            this._term.loadAddon(this._fitAddon);
        }

        // 挂载到 DOM
        this._term.open(container);

        // 初始适配
        if (this._fitAddon) {
            setTimeout(() => this._fitAddon.fit(), 50);
        }

        // 监听容器尺寸变化
        this._resizeObserver = new ResizeObserver(() => {
            if (this._fitAddon) {
                this._fitAddon.fit();
            }
        });
        this._resizeObserver.observe(container);

        // 终端尺寸变化 → 通知后端 PTY resize
        this._term.onResize(({ cols, rows }) => {
            if (this._ws && this._ws.readyState === WebSocket.OPEN) {
                this._ws.send(JSON.stringify({ type: 'resize', cols, rows }));
            }
        });

        // 用户输入 → 发送到 WebSocket
        this._term.onData((data) => {
            if (this._ws && this._ws.readyState === WebSocket.OPEN) {
                this._ws.send(data);
            }
        });

        // 欢迎信息
        this._term.writeln('\x1b[32mNodePanel Terminal\x1b[0m');
        this._term.writeln('\x1b[90m正在连接...\x1b[0m');
        this._term.writeln('');
    },

    async _loadNodes() {
        try {
            const data = await API.get('/api/v1/nodes');
            const select = document.getElementById('term-target');
            if (!select) return;

            const nodes = data.nodes || [];
            select.innerHTML = nodes.map(n => {
                const label = n.is_self ? `${n.name} (本机)` : `${n.name} (${n.host}:${n.port})`;
                return `<option value="${n.node_id}" ${n.is_self ? 'selected' : ''}>${label}</option>`;
            }).join('');

            select.addEventListener('change', () => {
                this._updateTargetInfo(nodes);
                // 切换节点时重新连接
                this._connect();
            });
            this._updateTargetInfo(nodes);

            // 初始连接
            this._connect();
        } catch (err) {
            console.error('加载节点失败:', err);
            if (this._term) {
                this._term.writeln('\x1b[31m加载节点列表失败\x1b[0m');
            }
        }
    },

    _updateTargetInfo(nodes) {
        const select = document.getElementById('term-target');
        const modeEl = document.getElementById('term-mode');
        const statusEl = document.getElementById('term-status');
        if (!select) return;

        const node = nodes.find(n => n.node_id === select.value);
        if (node && modeEl) {
            modeEl.textContent = node.mode;
            modeEl.className = `tag ${node.mode === 'full' ? 'blue' : 'yellow'}`;
        }
        if (node && statusEl) {
            statusEl.textContent = node.status === 'online' ? '在线' : '离线';
            statusEl.className = `tag ${node.status === 'online' ? 'green' : 'red'}`;
        }
    },

    _connect() {
        // 先断开旧连接
        this._disconnect();

        const select = document.getElementById('term-target');
        const targetId = select ? select.value : '';

        if (!targetId) return;

        // 构建 WebSocket URL
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${proto}//${location.host}/api/v1/terminal/ws?node_id=${encodeURIComponent(targetId)}`;

        this._updateConnStatus('connecting', '连接中...');

        // 清屏并显示连接信息
        if (this._term) {
            this._term.clear();
            this._term.writeln('\x1b[32mNodePanel Terminal\x1b[0m');
            this._term.writeln(`\x1b[90m连接到 ${targetId}...\x1b[0m`);
            this._term.writeln('');
        }

        try {
            this._ws = new WebSocket(wsUrl);

            this._ws.onopen = () => {
                this._updateConnStatus('connected', '已连接');
                if (this._term) {
                    // 发送初始终端尺寸，让后端 PTY 匹配
                    const cols = this._term.cols;
                    const rows = this._term.rows;
                    this._ws.send(JSON.stringify({ type: 'resize', cols, rows }));
                    this._term.focus();
                }
            };

            this._ws.onmessage = (event) => {
                if (this._term && event.data) {
                    this._term.write(event.data);
                }
            };

            this._ws.onclose = (event) => {
                const reason = event.reason || '连接已关闭';
                this._updateConnStatus('disconnected', '已断开');
                if (this._term) {
                    this._term.writeln('');
                    this._term.writeln(`\x1b[31m[终端连接已断开: ${reason}]\x1b[0m`);
                    this._term.writeln('\x1b[90m点击"重新连接"按钮或切换节点重新连接\x1b[0m');
                }
            };

            this._ws.onerror = (error) => {
                console.error('WebSocket error:', error);
                this._updateConnStatus('error', '连接错误');
            };

        } catch (err) {
            console.error('WebSocket 创建失败:', err);
            this._updateConnStatus('error', '连接失败');
            if (this._term) {
                this._term.writeln(`\x1b[31m连接失败: ${err.message}\x1b[0m`);
            }
        }
    },

    _disconnect() {
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
        if (this._ws) {
            try {
                this._ws.close();
            } catch (e) { }
            this._ws = null;
        }
    },

    _updateConnStatus(state, text) {
        const el = document.getElementById('term-conn-status');
        const btn = document.getElementById('term-reconnect-btn');
        if (!el) return;

        el.style.display = 'inline-flex';
        el.textContent = text;

        switch (state) {
            case 'connecting':
                el.className = 'tag yellow';
                if (btn) btn.style.display = 'none';
                break;
            case 'connected':
                el.className = 'tag green';
                if (btn) btn.style.display = 'none';
                break;
            case 'disconnected':
            case 'error':
                el.className = 'tag red';
                if (btn) btn.style.display = 'inline-block';
                break;
        }
    },
};
