/**
 * 聊天页面
 * 跨设备实时聊天，支持 WebSocket 实时推送 + REST 轮询备用
 */

const ChatPage = {
    title: '聊天',
    _ws: null,
    _pollTimer: null,
    _messages: [],
    _myNodeId: '',

    render() {
        return `
            <div class="chat-container">
                <div class="chat-header-bar">
                    <span class="chat-title">跨设备聊天</span>
                    <span class="chat-status" id="chat-status">
                        <span class="status-dot"></span>
                        <span class="status-label">连接中...</span>
                    </span>
                </div>
                <div class="chat-messages" id="chat-messages">
                    <div class="loading">
                        <div class="loading-spinner"></div>
                        加载消息中...
                    </div>
                </div>
                <div class="chat-input-bar">
                    <input type="text" class="chat-input" id="chat-input"
                           placeholder="输入消息..." maxlength="2000"
                           autocomplete="off">
                    <button class="btn btn-primary chat-send-btn" id="chat-send-btn">发送</button>
                </div>
            </div>
        `;
    },

    async mount() {
        // 获取本机节点 ID
        try {
            const info = await API.get('/api/v1/nodes/self');
            this._myNodeId = info.node_id || '';
        } catch (e) {
            this._myNodeId = '';
        }

        // 加载历史消息
        await this._loadMessages();

        // 连接 WebSocket
        this._connectWS();

        // 绑定事件
        const input = document.getElementById('chat-input');
        const sendBtn = document.getElementById('chat-send-btn');

        if (input) {
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this._sendMessage();
                }
            });
            input.focus();
        }

        if (sendBtn) {
            sendBtn.addEventListener('click', () => this._sendMessage());
        }

        // REST 轮询备用（每 5 秒）
        this._pollTimer = setInterval(() => this._pollMessages(), 5000);
    },

    destroy() {
        if (this._ws) {
            this._ws.close();
            this._ws = null;
        }
        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
    },

    async _loadMessages() {
        try {
            const data = await API.get('/api/v1/chat/messages?limit=100');
            this._messages = data.messages || [];
            this._renderMessages();
        } catch (err) {
            console.error('加载聊天消息失败:', err);
        }
    },

    _connectWS() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${proto}//${location.host}/api/v1/chat/ws`;

        try {
            this._ws = new WebSocket(url);

            this._ws.onopen = () => {
                this._updateStatus('online', '已连接');
            };

            this._ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'message' && data.data) {
                        // 去重
                        const exists = this._messages.some(m => m.id === data.data.id);
                        if (!exists) {
                            this._messages.push(data.data);
                            this._appendMessage(data.data);
                            this._scrollToBottom();
                        }
                    }
                } catch (e) {
                    console.error('解析 WS 消息失败:', e);
                }
            };

            this._ws.onclose = () => {
                this._updateStatus('offline', '已断开');
                // 5 秒后重连
                setTimeout(() => {
                    if (this._pollTimer) {  // 页面仍然活跃
                        this._connectWS();
                    }
                }, 5000);
            };

            this._ws.onerror = () => {
                this._updateStatus('offline', '连接失败');
            };
        } catch (e) {
            this._updateStatus('offline', '不支持 WebSocket');
        }
    },

    async _sendMessage() {
        const input = document.getElementById('chat-input');
        if (!input) return;

        const content = input.value.trim();
        if (!content) return;

        input.value = '';
        input.focus();

        // 优先通过 WebSocket 发送
        if (this._ws && this._ws.readyState === WebSocket.OPEN) {
            this._ws.send(JSON.stringify({ type: 'message', content }));
        } else {
            // 回退到 REST
            try {
                await API.post('/api/v1/chat/messages', { content });
            } catch (err) {
                console.error('发送消息失败:', err);
            }
        }
    },

    async _pollMessages() {
        try {
            const data = await API.get('/api/v1/chat/messages?limit=100');
            const newMessages = data.messages || [];

            if (newMessages.length !== this._messages.length) {
                this._messages = newMessages;
                this._renderMessages();
            }
        } catch (err) {
            // 静默失败
        }
    },

    _renderMessages() {
        const container = document.getElementById('chat-messages');
        if (!container) return;

        if (this._messages.length === 0) {
            container.innerHTML = `
                <div class="chat-empty">
                    <div class="chat-empty-icon">💬</div>
                    <div class="chat-empty-text">暂无消息，开始聊天吧</div>
                </div>
            `;
            return;
        }

        container.innerHTML = this._messages.map(msg => this._renderBubble(msg)).join('');
        this._scrollToBottom();
    },

    _appendMessage(msg) {
        const container = document.getElementById('chat-messages');
        if (!container) return;

        // 如果是空状态提示，先清除
        const empty = container.querySelector('.chat-empty');
        if (empty) empty.remove();

        container.insertAdjacentHTML('beforeend', this._renderBubble(msg));
    },

    _renderBubble(msg) {
        const isMine = msg.node_id === this._myNodeId;
        const time = this._formatTime(msg.timestamp);
        const cls = isMine ? 'chat-bubble mine' : 'chat-bubble other';

        return `
            <div class="${cls}">
                <div class="chat-bubble-header">
                    <span class="chat-node-name">${this._escapeHtml(msg.node_name || msg.node_id)}</span>
                    <span class="chat-time">${time}</span>
                </div>
                <div class="chat-bubble-content">${this._escapeHtml(msg.content)}</div>
            </div>
        `;
    },

    _scrollToBottom() {
        const container = document.getElementById('chat-messages');
        if (container) {
            container.scrollTop = container.scrollHeight;
        }
    },

    _updateStatus(status, label) {
        const el = document.getElementById('chat-status');
        if (!el) return;
        el.innerHTML = `
            <span class="status-dot ${status}"></span>
            <span class="status-label">${label}</span>
        `;
    },

    _formatTime(ts) {
        if (!ts) return '';
        const d = new Date(ts * 1000);
        const now = new Date();
        const isToday = d.toDateString() === now.toDateString();

        const hh = String(d.getHours()).padStart(2, '0');
        const mm = String(d.getMinutes()).padStart(2, '0');

        if (isToday) return `${hh}:${mm}`;

        const MM = String(d.getMonth() + 1).padStart(2, '0');
        const DD = String(d.getDate()).padStart(2, '0');
        return `${MM}-${DD} ${hh}:${mm}`;
    },

    _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    },
};
