/**
 * 远程终端页面
 * 在指定节点上执行命令，支持本机和远程节点。
 * NAT 后的 Relay 节点通过 Full 节点心跳队列转发命令。
 */

const TerminalPage = {
    title: '远程终端',
    _history: [],
    _historyIndex: -1,

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
                </div>
            </div>

            <div class="panel terminal-panel">
                <div class="terminal-output" id="term-output">
                    <div class="terminal-welcome">
                        <span style="color:var(--accent-green)">NodePanel Terminal</span>
                        <br>输入命令并按 Enter 执行，支持远程节点。
                        <br><span style="color:var(--text-muted)">提示：NAT 后的 Relay 节点命令会通过心跳队列异步执行。</span>
                    </div>
                </div>
                <div class="terminal-input-row">
                    <span class="terminal-prompt">$</span>
                    <input type="text" class="terminal-input" id="term-input"
                           placeholder="输入命令..." autocomplete="off"
                           spellcheck="false">
                </div>
            </div>
        `;
    },

    mount() {
        this._loadNodes();

        const input = document.getElementById('term-input');
        if (input) {
            input.addEventListener('keydown', (e) => this._handleKey(e));
            input.focus();
        }
    },

    destroy() { },

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

            select.addEventListener('change', () => this._updateTargetInfo(nodes));
            this._updateTargetInfo(nodes);
        } catch (err) {
            console.error('加载节点失败:', err);
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

    _handleKey(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            const input = document.getElementById('term-input');
            const command = input.value.trim();
            if (command) {
                this._history.push(command);
                this._historyIndex = this._history.length;
                input.value = '';
                this._executeCommand(command);
            }
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (this._historyIndex > 0) {
                this._historyIndex--;
                document.getElementById('term-input').value = this._history[this._historyIndex];
            }
        } else if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (this._historyIndex < this._history.length - 1) {
                this._historyIndex++;
                document.getElementById('term-input').value = this._history[this._historyIndex];
            } else {
                this._historyIndex = this._history.length;
                document.getElementById('term-input').value = '';
            }
        }
    },

    async _executeCommand(command) {
        const output = document.getElementById('term-output');
        const select = document.getElementById('term-target');
        const targetId = select ? select.value : '';

        // 显示命令
        this._appendOutput(`<span class="term-prompt-line">$ ${this._escapeHtml(command)}</span>`);

        // 特殊命令
        if (command === 'clear') {
            output.innerHTML = '';
            return;
        }

        try {
            const result = await API.post('/api/v1/tasks/execute', {
                command: command,
                target_node_id: targetId,
                timeout: 60,
            });

            if (result.queued) {
                // Relay 节点，命令排队
                this._appendOutput(
                    `<span class="term-info">⏳ ${result.message}</span>\n` +
                    `<span class="term-info">任务 ID: ${result.task_id}</span>`
                );
            } else if (result.error) {
                this._appendOutput(`<span class="term-error">错误: ${this._escapeHtml(result.error)}</span>`);
            } else {
                // 直接执行结果
                if (result.stdout) {
                    this._appendOutput(`<span class="term-stdout">${this._escapeHtml(result.stdout)}</span>`);
                }
                if (result.stderr) {
                    this._appendOutput(`<span class="term-stderr">${this._escapeHtml(result.stderr)}</span>`);
                }
                if (result.exit_code !== 0 && result.exit_code !== undefined) {
                    this._appendOutput(`<span class="term-error">退出码: ${result.exit_code}</span>`);
                }
            }
        } catch (err) {
            this._appendOutput(`<span class="term-error">请求失败: ${this._escapeHtml(err.message)}</span>`);
        }
    },

    _appendOutput(html) {
        const output = document.getElementById('term-output');
        if (!output) return;
        const div = document.createElement('div');
        div.className = 'term-line';
        div.innerHTML = html;
        output.appendChild(div);
        output.scrollTop = output.scrollHeight;
    },

    _escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },
};
