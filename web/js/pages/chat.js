/**
 * 聊天页面 — Telegram 风格
 *
 * 核心特性：
 * - WebSocket 优先，无 REST 轮询
 * - 乐观更新：发送后立即显示，带状态指示（✓ ✓✓）
 * - 指数退避智能重连（1s → 2s → 4s → 最大 30s）
 * - 重连后增量拉取（基于最新消息时间戳）
 * - 批量消息去重合并
 * - WebSocket 心跳保活（25s）
 */

const ChatPage = {
    title: '聊天',
    _ws: null,
    _messages: [],
    _messageIds: new Set(),
    _pendingMessages: new Map(),   // client_id → msg element
    _myNodeId: '',
    _reconnectDelay: 1000,
    _reconnectTimer: null,
    _heartbeatTimer: null,
    _isConnected: false,
    _lastMessageTs: 0,             // 最新消息时间戳，用于增量拉取
    _isAtBottom: true,             // 是否在底部（用于自动滚动）

    render() {
        return `
            <div class="chat-container">
                <div class="chat-header-bar">
                    <span class="chat-title">跨设备聊天</span>
                    <div class="chat-header-right">
                        <span class="chat-connection-count" id="chat-conn-count" title="WebSocket 连接数"></span>
                        <span class="chat-status" id="chat-status">
                            <span class="status-dot"></span>
                            <span class="status-label">连接中...</span>
                        </span>
                    </div>
                </div>
                <div class="chat-messages" id="chat-messages">
                    <div class="loading">
                        <div class="loading-spinner"></div>
                        加载消息中...
                    </div>
                </div>
                <div class="chat-unread-badge" id="chat-unread-badge" style="display:none">
                    <span id="chat-unread-count">0</span> 条新消息 ↓
                </div>
                <div class="chat-input-bar">
                    <input type="text" class="chat-input" id="chat-input"
                           placeholder="输入消息..." maxlength="2000"
                           autocomplete="off">
                    <button class="btn btn-primary chat-send-btn" id="chat-send-btn">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <line x1="22" y1="2" x2="11" y2="13"></line>
                            <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                        </svg>
                    </button>
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
        const container = document.getElementById('chat-messages');

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

        // 监听滚动位置
        if (container) {
            container.addEventListener('scroll', () => {
                const threshold = 60;
                this._isAtBottom = (container.scrollHeight - container.scrollTop - container.clientHeight) < threshold;
                if (this._isAtBottom) {
                    this._hideUnreadBadge();
                }
            });
        }

        // 未读消息提示点击
        const badge = document.getElementById('chat-unread-badge');
        if (badge) {
            badge.addEventListener('click', () => {
                this._scrollToBottom(true);
                this._hideUnreadBadge();
            });
        }
    },

    destroy() {
        this._closeWS();
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
        if (this._heartbeatTimer) {
            clearInterval(this._heartbeatTimer);
            this._heartbeatTimer = null;
        }
    },

    // ════════════════════════════════════════
    // 消息加载
    // ════════════════════════════════════════

    async _loadMessages() {
        try {
            const data = await API.get('/api/v1/chat/messages?limit=100');
            const messages = data.messages || [];
            this._messages = [];
            this._messageIds.clear();

            for (const msg of messages) {
                if (msg.id && !this._messageIds.has(msg.id)) {
                    this._messages.push(msg);
                    this._messageIds.add(msg.id);
                    if (msg.timestamp > this._lastMessageTs) {
                        this._lastMessageTs = msg.timestamp;
                    }
                }
            }

            this._renderMessages();
        } catch (err) {
            console.error('加载聊天消息失败:', err);
        }
    },

    async _loadIncrementalMessages() {
        /** 重连后增量拉取 */
        if (!this._lastMessageTs) return;

        try {
            const data = await API.get(`/api/v1/chat/messages?limit=100&after=${this._lastMessageTs}`);
            const messages = data.messages || [];
            let newCount = 0;

            for (const msg of messages) {
                if (msg.id && !this._messageIds.has(msg.id)) {
                    this._messages.push(msg);
                    this._messageIds.add(msg.id);
                    this._appendMessage(msg);
                    newCount++;
                    if (msg.timestamp > this._lastMessageTs) {
                        this._lastMessageTs = msg.timestamp;
                    }
                }
            }

            if (newCount > 0) {
                if (this._isAtBottom) {
                    this._scrollToBottom(true);
                } else {
                    this._showUnreadBadge(newCount);
                }
            }
        } catch (err) {
            console.error('增量拉取消息失败:', err);
        }
    },

    // ════════════════════════════════════════
    // WebSocket 管理
    // ════════════════════════════════════════

    _connectWS() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${proto}//${location.host}/api/v1/chat/ws`;

        try {
            this._ws = new WebSocket(url);

            this._ws.onopen = () => {
                this._isConnected = true;
                this._reconnectDelay = 1000; // 重置退避
                this._updateStatus('online', '已连接');

                // 重连后增量拉取
                if (this._lastMessageTs > 0) {
                    this._loadIncrementalMessages();
                }

                // 启动心跳
                this._startHeartbeat();
            };

            this._ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this._handleWSMessage(data);
                } catch (e) {
                    console.error('解析 WS 消息失败:', e);
                }
            };

            this._ws.onclose = (event) => {
                this._isConnected = false;
                this._stopHeartbeat();

                if (event.code === 4001) {
                    this._updateStatus('offline', '未登录');
                    return; // 不重连
                }

                this._updateStatus('reconnecting', '重连中...');
                this._scheduleReconnect();
            };

            this._ws.onerror = () => {
                // onclose 会紧随其后触发
            };
        } catch (e) {
            this._updateStatus('offline', '不支持 WebSocket');
        }
    },

    _closeWS() {
        if (this._ws) {
            this._ws.onclose = null; // 防止触发重连
            this._ws.close();
            this._ws = null;
        }
        this._isConnected = false;
        this._stopHeartbeat();
    },

    _scheduleReconnect() {
        if (this._reconnectTimer) return;

        this._reconnectTimer = setTimeout(() => {
            this._reconnectTimer = null;
            if (!this._isConnected) {
                this._connectWS();
            }
        }, this._reconnectDelay);

        // 指数退避，最大 30 秒
        this._reconnectDelay = Math.min(this._reconnectDelay * 2, 30000);
    },

    _startHeartbeat() {
        this._stopHeartbeat();
        this._heartbeatTimer = setInterval(() => {
            if (this._ws && this._ws.readyState === WebSocket.OPEN) {
                this._ws.send(JSON.stringify({ type: 'ping' }));
            }
        }, 25000);
    },

    _stopHeartbeat() {
        if (this._heartbeatTimer) {
            clearInterval(this._heartbeatTimer);
            this._heartbeatTimer = null;
        }
    },

    // ════════════════════════════════════════
    // WebSocket 消息处理
    // ════════════════════════════════════════

    _handleWSMessage(data) {
        if (data.type === 'message' && data.data) {
            this._onReceiveMessage(data.data);
        } else if (data.type === 'messages_batch' && Array.isArray(data.data)) {
            // 批量消息（来自 sync/heartbeat）
            let newCount = 0;
            for (const msg of data.data) {
                if (this._onReceiveMessage(msg, true)) {
                    newCount++;
                }
            }
            if (newCount > 0 && this._isAtBottom) {
                this._scrollToBottom(true);
            } else if (newCount > 0) {
                this._showUnreadBadge(newCount);
            }
        } else if (data.type === 'pong') {
            // 心跳响应，忽略
        }
    },

    /**
     * 处理收到的单条消息
     * @returns {boolean} 是否为新消息
     */
    _onReceiveMessage(msg, batch = false) {
        if (!msg || !msg.id) return false;

        // 去重
        if (this._messageIds.has(msg.id)) return false;

        // 检查是否是乐观更新的确认（通过 client_id）
        if (msg.client_id && this._pendingMessages.has(msg.client_id)) {
            const pendingEl = this._pendingMessages.get(msg.client_id);
            this._pendingMessages.delete(msg.client_id);
            // 更新状态为已发送
            if (pendingEl) {
                const statusEl = pendingEl.querySelector('.chat-msg-status');
                if (statusEl) {
                    statusEl.textContent = '✓';
                    statusEl.title = '已发送';
                    statusEl.classList.remove('sending');
                    statusEl.classList.add('sent');
                }
            }
            // 已经在列表中显示了，只需记录 id
            this._messageIds.add(msg.id);
            if (msg.timestamp > this._lastMessageTs) {
                this._lastMessageTs = msg.timestamp;
            }
            return false;
        }

        // 新消息
        this._messages.push(msg);
        this._messageIds.add(msg.id);
        if (msg.timestamp > this._lastMessageTs) {
            this._lastMessageTs = msg.timestamp;
        }

        this._appendMessage(msg);

        if (!batch) {
            if (this._isAtBottom) {
                this._scrollToBottom(true);
            } else {
                this._showUnreadBadge(1);
            }
        }

        return true;
    },

    // ════════════════════════════════════════
    // 发送消息
    // ════════════════════════════════════════

    _sendMessage() {
        const input = document.getElementById('chat-input');
        if (!input) return;

        const content = input.value.trim();
        if (!content) return;

        input.value = '';
        input.focus();

        // 生成 client_id 用于乐观更新
        const clientId = 'c_' + Date.now() + '_' + Math.random().toString(36).substr(2, 6);

        // 乐观更新：立即显示消息
        const optimisticMsg = {
            id: clientId,  // 临时 id
            client_id: clientId,
            node_id: this._myNodeId,
            node_name: '我',
            content: content,
            timestamp: Date.now() / 1000,
            status: 'sending',
        };

        this._appendMessage(optimisticMsg);
        this._scrollToBottom(true);

        // 记录 pending
        const container = document.getElementById('chat-messages');
        const lastBubble = container ? container.lastElementChild : null;
        this._pendingMessages.set(clientId, lastBubble);

        // 通过 WebSocket 发送
        if (this._ws && this._ws.readyState === WebSocket.OPEN) {
            this._ws.send(JSON.stringify({
                type: 'message',
                content: content,
                client_id: clientId,
            }));
        } else {
            // 回退到 REST
            API.post('/api/v1/chat/messages', { content }).then(resp => {
                if (resp.ok && resp.message) {
                    // 标记为已发送
                    if (lastBubble) {
                        const statusEl = lastBubble.querySelector('.chat-msg-status');
                        if (statusEl) {
                            statusEl.textContent = '✓';
                            statusEl.title = '已发送';
                            statusEl.classList.remove('sending');
                            statusEl.classList.add('sent');
                        }
                    }
                    this._messageIds.add(resp.message.id);
                    this._pendingMessages.delete(clientId);
                }
            }).catch(err => {
                console.error('发送消息失败:', err);
                // 标记发送失败
                if (lastBubble) {
                    const statusEl = lastBubble.querySelector('.chat-msg-status');
                    if (statusEl) {
                        statusEl.textContent = '✗';
                        statusEl.title = '发送失败';
                        statusEl.classList.remove('sending');
                        statusEl.classList.add('failed');
                    }
                }
            });
        }
    },

    // ════════════════════════════════════════
    // 渲染
    // ════════════════════════════════════════

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

        container.innerHTML = '';
        let lastDate = '';

        for (const msg of this._messages) {
            // 日期分隔线
            const msgDate = this._formatDate(msg.timestamp);
            if (msgDate !== lastDate) {
                lastDate = msgDate;
                const sep = document.createElement('div');
                sep.className = 'chat-date-separator';
                sep.innerHTML = `<span>${msgDate}</span>`;
                container.appendChild(sep);
            }
            container.insertAdjacentHTML('beforeend', this._renderBubble(msg));
        }

        this._scrollToBottom(false);
    },

    _appendMessage(msg) {
        const container = document.getElementById('chat-messages');
        if (!container) return;

        // 如果是空状态提示，先清除
        const empty = container.querySelector('.chat-empty');
        if (empty) empty.remove();

        // 日期分隔线
        const msgDate = this._formatDate(msg.timestamp);
        const lastSep = container.querySelector('.chat-date-separator:last-of-type');
        const lastDateText = lastSep ? lastSep.textContent.trim() : '';
        if (msgDate !== lastDateText) {
            const sep = document.createElement('div');
            sep.className = 'chat-date-separator';
            sep.innerHTML = `<span>${msgDate}</span>`;
            container.appendChild(sep);
        }

        container.insertAdjacentHTML('beforeend', this._renderBubble(msg));
    },

    _renderBubble(msg) {
        const isMine = msg.node_id === this._myNodeId;
        const time = this._formatTime(msg.timestamp);
        const cls = isMine ? 'chat-bubble mine' : 'chat-bubble other';

        // 消息状态指示（仅自己的消息）
        let statusHtml = '';
        if (isMine) {
            const status = msg.status || 'sent';
            if (status === 'sending') {
                statusHtml = '<span class="chat-msg-status sending" title="发送中">○</span>';
            } else if (status === 'sent') {
                statusHtml = '<span class="chat-msg-status sent" title="已发送">✓</span>';
            } else if (status === 'delivered') {
                statusHtml = '<span class="chat-msg-status delivered" title="已送达">✓✓</span>';
            } else if (status === 'failed') {
                statusHtml = '<span class="chat-msg-status failed" title="发送失败">✗</span>';
            }
        }

        const nodeName = isMine ? '' : `<span class="chat-node-name">${this._escapeHtml(msg.node_name || msg.node_id)}</span>`;

        return `
            <div class="${cls}">
                ${nodeName ? `<div class="chat-bubble-sender">${nodeName}</div>` : ''}
                <div class="chat-bubble-body">
                    <span class="chat-bubble-content">${this._escapeHtml(msg.content)}</span>
                    <span class="chat-bubble-meta">
                        <span class="chat-time">${time}</span>
                        ${statusHtml}
                    </span>
                </div>
            </div>
        `;
    },

    // ════════════════════════════════════════
    // UI 辅助
    // ════════════════════════════════════════

    _scrollToBottom(smooth) {
        const container = document.getElementById('chat-messages');
        if (container) {
            if (smooth) {
                container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
            } else {
                container.scrollTop = container.scrollHeight;
            }
            this._isAtBottom = true;
        }
    },

    _showUnreadBadge(count) {
        const badge = document.getElementById('chat-unread-badge');
        const countEl = document.getElementById('chat-unread-count');
        if (!badge || !countEl) return;

        const current = parseInt(countEl.textContent) || 0;
        countEl.textContent = current + count;
        badge.style.display = 'flex';
    },

    _hideUnreadBadge() {
        const badge = document.getElementById('chat-unread-badge');
        const countEl = document.getElementById('chat-unread-count');
        if (badge) badge.style.display = 'none';
        if (countEl) countEl.textContent = '0';
    },

    _updateStatus(status, label) {
        const el = document.getElementById('chat-status');
        if (!el) return;
        el.innerHTML = `
            <span class="status-dot ${status}"></span>
            <span class="status-label">${label}</span>
        `;
    },

    // ════════════════════════════════════════
    // 格式化
    // ════════════════════════════════════════

    _formatDate(ts) {
        if (!ts) return '';
        const d = new Date(ts * 1000);
        const now = new Date();

        if (d.toDateString() === now.toDateString()) return '今天';

        const yesterday = new Date(now);
        yesterday.setDate(yesterday.getDate() - 1);
        if (d.toDateString() === yesterday.toDateString()) return '昨天';

        const MM = String(d.getMonth() + 1).padStart(2, '0');
        const DD = String(d.getDate()).padStart(2, '0');

        if (d.getFullYear() === now.getFullYear()) {
            return `${MM}月${DD}日`;
        }
        return `${d.getFullYear()}年${MM}月${DD}日`;
    },

    _formatTime(ts) {
        if (!ts) return '';
        const d = new Date(ts * 1000);
        const hh = String(d.getHours()).padStart(2, '0');
        const mm = String(d.getMinutes()).padStart(2, '0');
        return `${hh}:${mm}`;
    },

    _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    },
};
