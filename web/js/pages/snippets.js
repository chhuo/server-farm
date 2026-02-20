/**
 * 信息复制中心页面
 * 存储和管理常用信息：账号密码、服务器凭据、常用命令、碎片笔记
 * 支持敏感字段遮罩、一键复制、隐藏/显示控制
 */

const SnippetsPage = {
    title: '复制中心',
    _snippets: [],
    _currentCategory: '',
    _revealedFields: new Set(),  // 已显示的敏感字段 key: snippetId-fieldIndex
    _expandedCards: new Set(),   // 已展开的隐藏卡片

    render() {
        return `
            <div class="snippets-page">
                <div class="snippets-toolbar">
                    <div class="tab-bar" id="snippets-tabs">
                        <button class="tab-btn active" data-cat="">全部</button>
                        <button class="tab-btn" data-cat="account">账号</button>
                        <button class="tab-btn" data-cat="server">服务器</button>
                        <button class="tab-btn" data-cat="command">命令</button>
                        <button class="tab-btn" data-cat="note">笔记</button>
                    </div>
                    <button class="btn btn-primary" id="snippets-add-btn">+ 添加</button>
                </div>
                <div class="snippets-list" id="snippets-list">
                    <div class="loading">
                        <div class="loading-spinner"></div>
                        加载中...
                    </div>
                </div>
            </div>

            <!-- 添加/编辑对话框 -->
            <div class="dialog-overlay" id="snippet-dialog" style="display:none">
                <div class="dialog" style="width:520px">
                    <div class="dialog-header">
                        <span class="dialog-title" id="snippet-dialog-title">添加信息片段</span>
                        <button class="dialog-close" id="snippet-dialog-close">✕</button>
                    </div>
                    <div class="dialog-body">
                        <div class="form-group">
                            <label class="form-label">分类</label>
                            <select class="form-input" id="snippet-category">
                                <option value="account">账号</option>
                                <option value="server">服务器</option>
                                <option value="command">命令</option>
                                <option value="note">笔记</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label class="form-label">标题</label>
                            <input type="text" class="form-input" id="snippet-title" placeholder="例如：Google 账号 #1">
                        </div>
                        <div class="form-group">
                            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                                <label class="form-label" style="margin:0">字段</label>
                                <button class="btn-sm" id="snippet-add-field">+ 添加字段</button>
                            </div>
                            <div id="snippet-fields"></div>
                        </div>
                        <div class="form-group">
                            <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:0.85rem;color:var(--text-secondary)">
                                <input type="checkbox" id="snippet-hidden"> 默认隐藏（仅显示标题）
                            </label>
                        </div>
                        <div id="snippet-error" style="display:none;color:var(--accent-red);font-size:0.85rem;margin-top:8px"></div>
                    </div>
                    <div class="dialog-footer" style="display:flex;gap:8px;justify-content:flex-end;padding:16px">
                        <button class="btn btn-secondary" id="snippet-cancel-btn">取消</button>
                        <button class="btn btn-primary" id="snippet-save-btn">保存</button>
                    </div>
                </div>
            </div>
        `;
    },

    async mount() {
        this._revealedFields.clear();
        this._expandedCards.clear();

        // 分类标签切换
        document.getElementById('snippets-tabs').addEventListener('click', (e) => {
            const btn = e.target.closest('.tab-btn');
            if (!btn) return;
            document.querySelectorAll('#snippets-tabs .tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            this._currentCategory = btn.dataset.cat || '';
            this._renderList();
        });

        // 添加按钮
        document.getElementById('snippets-add-btn').addEventListener('click', () => {
            this._openDialog();
        });

        // 对话框事件
        document.getElementById('snippet-dialog-close').addEventListener('click', () => this._closeDialog());
        document.getElementById('snippet-cancel-btn').addEventListener('click', () => this._closeDialog());
        document.getElementById('snippet-save-btn').addEventListener('click', () => this._saveSnippet());
        document.getElementById('snippet-add-field').addEventListener('click', () => this._addFieldRow());

        // 分类变化时自动填充默认字段
        document.getElementById('snippet-category').addEventListener('change', (e) => {
            const fields = document.getElementById('snippet-fields');
            if (fields && fields.children.length === 0) {
                this._fillDefaultFields(e.target.value);
            }
        });

        await this._loadSnippets();
    },

    destroy() {},

    async _loadSnippets() {
        try {
            const data = await API.get('/api/v1/snippets/');
            this._snippets = data.snippets || [];
            this._renderList();
        } catch (err) {
            console.error('加载信息片段失败:', err);
        }
    },

    _renderList() {
        const container = document.getElementById('snippets-list');
        if (!container) return;

        let items = this._snippets;
        if (this._currentCategory) {
            items = items.filter(s => s.category === this._currentCategory);
        }

        if (items.length === 0) {
            container.innerHTML = `
                <div class="snippets-empty">
                    <div class="placeholder-icon">📋</div>
                    <div class="placeholder-title">暂无信息片段</div>
                    <div class="placeholder-desc">点击"+ 添加"创建你的第一条信息</div>
                </div>
            `;
            return;
        }

        container.innerHTML = items.map(s => this._renderCard(s)).join('');

        // 绑定卡片事件
        container.querySelectorAll('.snippet-card').forEach(card => {
            const id = card.dataset.id;

            // 复制按钮
            card.querySelectorAll('.snippet-copy-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const val = btn.dataset.value;
                    this._copyToClipboard(val, btn);
                });
            });

            // 显示/隐藏敏感字段
            card.querySelectorAll('.snippet-reveal-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const key = btn.dataset.key;
                    if (this._revealedFields.has(key)) {
                        this._revealedFields.delete(key);
                    } else {
                        this._revealedFields.add(key);
                    }
                    this._renderList();
                });
            });

            // 展开隐藏的卡片
            card.querySelector('.snippet-expand-btn')?.addEventListener('click', (e) => {
                e.stopPropagation();
                if (this._expandedCards.has(id)) {
                    this._expandedCards.delete(id);
                } else {
                    this._expandedCards.add(id);
                }
                this._renderList();
            });

            // 编辑按钮
            card.querySelector('.snippet-edit-btn')?.addEventListener('click', (e) => {
                e.stopPropagation();
                const snippet = this._snippets.find(s => s.id === id);
                if (snippet) this._openDialog(snippet);
            });

            // 删除按钮
            card.querySelector('.snippet-delete-btn')?.addEventListener('click', (e) => {
                e.stopPropagation();
                this._deleteSnippet(id);
            });
        });
    },

    _renderCard(snippet) {
        const catIcons = { account: '👤', server: '🖥️', command: '⌨️', note: '📝' };
        const catLabels = { account: '账号', server: '服务器', command: '命令', note: '笔记' };
        const icon = catIcons[snippet.category] || '📋';
        const catLabel = catLabels[snippet.category] || snippet.category;

        const isHidden = snippet.hidden && !this._expandedCards.has(snippet.id);

        let fieldsHtml = '';
        if (!isHidden && snippet.fields && snippet.fields.length > 0) {
            fieldsHtml = '<div class="snippet-fields">' +
                snippet.fields.map((f, idx) => {
                    const fieldKey = `${snippet.id}-${idx}`;
                    const isRevealed = !f.sensitive || this._revealedFields.has(fieldKey);
                    const displayValue = isRevealed ? f.value : '••••••••';

                    return `
                        <div class="snippet-field-row">
                            <span class="snippet-field-key">${this._escapeHtml(f.key)}</span>
                            <span class="snippet-field-value ${f.sensitive && !isRevealed ? 'masked' : ''}">${this._escapeHtml(displayValue)}</span>
                            <div class="snippet-field-actions">
                                ${f.sensitive ? `<button class="snippet-reveal-btn" data-key="${fieldKey}" title="${isRevealed ? '隐藏' : '显示'}">${isRevealed ? '🙈' : '👁️'}</button>` : ''}
                                <button class="snippet-copy-btn" data-value="${this._escapeAttr(f.value)}" title="复制">📋</button>
                            </div>
                        </div>
                    `;
                }).join('') +
                '</div>';
        }

        return `
            <div class="snippet-card" data-id="${snippet.id}">
                <div class="snippet-card-header">
                    <div class="snippet-card-title-row">
                        <span class="snippet-icon">${icon}</span>
                        <span class="snippet-card-title">${this._escapeHtml(snippet.title)}</span>
                        <span class="tag blue">${catLabel}</span>
                        ${snippet.hidden ? '<span class="tag yellow">已隐藏</span>' : ''}
                    </div>
                    <div class="snippet-card-actions">
                        ${snippet.hidden ? `<button class="btn-sm snippet-expand-btn">${isHidden ? '展开' : '收起'}</button>` : ''}
                        <button class="btn-sm snippet-edit-btn">编辑</button>
                        <button class="btn-sm btn-danger snippet-delete-btn">删除</button>
                    </div>
                </div>
                ${fieldsHtml}
            </div>
        `;
    },

    // ── 对话框 ──

    _editingId: null,

    _openDialog(snippet = null) {
        this._editingId = snippet ? snippet.id : null;

        const dialog = document.getElementById('snippet-dialog');
        const title = document.getElementById('snippet-dialog-title');
        const category = document.getElementById('snippet-category');
        const titleInput = document.getElementById('snippet-title');
        const hidden = document.getElementById('snippet-hidden');
        const errorEl = document.getElementById('snippet-error');

        title.textContent = snippet ? '编辑信息片段' : '添加信息片段';
        errorEl.style.display = 'none';

        if (snippet) {
            category.value = snippet.category || 'note';
            titleInput.value = snippet.title || '';
            hidden.checked = snippet.hidden || false;
            this._renderFieldRows(snippet.fields || []);
        } else {
            category.value = 'account';
            titleInput.value = '';
            hidden.checked = false;
            this._fillDefaultFields('account');
        }

        dialog.style.display = 'flex';
        titleInput.focus();
    },

    _closeDialog() {
        document.getElementById('snippet-dialog').style.display = 'none';
        this._editingId = null;
    },

    _fillDefaultFields(category) {
        const defaults = {
            account: [
                { key: '账号', value: '', sensitive: false },
                { key: '密码', value: '', sensitive: true },
            ],
            server: [
                { key: '主机', value: '', sensitive: false },
                { key: '端口', value: '22', sensitive: false },
                { key: '用户名', value: '', sensitive: false },
                { key: '密码', value: '', sensitive: true },
            ],
            command: [
                { key: '命令', value: '', sensitive: false },
                { key: '说明', value: '', sensitive: false },
            ],
            note: [
                { key: '内容', value: '', sensitive: false },
            ],
        };
        this._renderFieldRows(defaults[category] || []);
    },

    _renderFieldRows(fields) {
        const container = document.getElementById('snippet-fields');
        container.innerHTML = fields.map((f, i) => `
            <div class="snippet-field-edit-row" data-idx="${i}">
                <input type="text" class="form-input snippet-field-key-input" value="${this._escapeAttr(f.key)}" placeholder="字段名" style="width:100px">
                <input type="text" class="form-input snippet-field-value-input" value="${this._escapeAttr(f.value)}" placeholder="值" style="flex:1">
                <label class="snippet-sensitive-label" title="敏感字段（默认遮罩）">
                    <input type="checkbox" class="snippet-field-sensitive" ${f.sensitive ? 'checked' : ''}> 🔒
                </label>
                <button class="btn-sm btn-danger snippet-remove-field-btn" title="删除字段">✕</button>
            </div>
        `).join('');

        // 绑定删除字段按钮
        container.querySelectorAll('.snippet-remove-field-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                btn.closest('.snippet-field-edit-row').remove();
            });
        });
    },

    _addFieldRow() {
        const container = document.getElementById('snippet-fields');
        const idx = container.children.length;
        const html = `
            <div class="snippet-field-edit-row" data-idx="${idx}">
                <input type="text" class="form-input snippet-field-key-input" value="" placeholder="字段名" style="width:100px">
                <input type="text" class="form-input snippet-field-value-input" value="" placeholder="值" style="flex:1">
                <label class="snippet-sensitive-label" title="敏感字段（默认遮罩）">
                    <input type="checkbox" class="snippet-field-sensitive"> 🔒
                </label>
                <button class="btn-sm btn-danger snippet-remove-field-btn" title="删除字段">✕</button>
            </div>
        `;
        container.insertAdjacentHTML('beforeend', html);

        // 绑定删除
        const newRow = container.lastElementChild;
        newRow.querySelector('.snippet-remove-field-btn').addEventListener('click', () => {
            newRow.remove();
        });

        // 聚焦新行
        newRow.querySelector('.snippet-field-key-input').focus();
    },

    async _saveSnippet() {
        const category = document.getElementById('snippet-category').value;
        const title = document.getElementById('snippet-title').value.trim();
        const hidden = document.getElementById('snippet-hidden').checked;
        const errorEl = document.getElementById('snippet-error');

        if (!title) {
            errorEl.textContent = '请输入标题';
            errorEl.style.display = 'block';
            return;
        }

        // 收集字段
        const fieldRows = document.querySelectorAll('.snippet-field-edit-row');
        const fields = [];
        fieldRows.forEach(row => {
            const key = row.querySelector('.snippet-field-key-input').value.trim();
            const value = row.querySelector('.snippet-field-value-input').value;
            const sensitive = row.querySelector('.snippet-field-sensitive').checked;
            if (key) {
                fields.push({ key, value, sensitive });
            }
        });

        const payload = { category, title, fields, hidden };

        const saveBtn = document.getElementById('snippet-save-btn');
        saveBtn.disabled = true;
        saveBtn.textContent = '保存中...';

        try {
            let result;
            if (this._editingId) {
                result = await API.put(`/api/v1/snippets/${this._editingId}`, payload);
            } else {
                result = await API.post('/api/v1/snippets/', payload);
            }

            if (result.error) {
                errorEl.textContent = result.error;
                errorEl.style.display = 'block';
            } else {
                this._closeDialog();
                await this._loadSnippets();
            }
        } catch (err) {
            errorEl.textContent = '保存失败: ' + err.message;
            errorEl.style.display = 'block';
        } finally {
            saveBtn.disabled = false;
            saveBtn.textContent = '保存';
        }
    },

    async _deleteSnippet(id) {
        if (!confirm('确认删除此信息片段？')) return;

        try {
            await API.delete(`/api/v1/snippets/${id}`);
            await this._loadSnippets();
        } catch (err) {
            console.error('删除失败:', err);
        }
    },

    _copyToClipboard(text, btn) {
        navigator.clipboard.writeText(text).then(() => {
            const orig = btn.textContent;
            btn.textContent = '✓';
            btn.classList.add('copied');
            setTimeout(() => {
                btn.textContent = orig;
                btn.classList.remove('copied');
            }, 1500);
        }).catch(() => {
            // 降级方案
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.cssText = 'position:fixed;left:-9999px';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);

            const orig = btn.textContent;
            btn.textContent = '✓';
            setTimeout(() => { btn.textContent = orig; }, 1500);
        });
    },

    _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    },

    _escapeAttr(str) {
        return (str || '').replace(/&/g, '&').replace(/"/g, '"').replace(/</g, '<').replace(/>/g, '>');
    },
};
