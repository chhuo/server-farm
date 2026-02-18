/**
 * Hash 路由器
 * 监听 hashchange 事件，渲染对应页面组件。
 */

const Router = {
    _routes: {},
    _currentPage: null,
    _container: null,

    /**
     * 注册路由
     * @param {string} path - 路由路径，如 '/dashboard'
     * @param {object} page - 页面对象，需实现 render() 和可选 mount()/destroy()
     */
    register(path, page) {
        this._routes[path] = page;
    },

    /**
     * 初始化路由
     */
    init() {
        this._container = document.getElementById('page-container');

        // 监听 hash 变化
        window.addEventListener('hashchange', () => this._onHashChange());

        // 初始导航
        if (!window.location.hash) {
            window.location.hash = '#/dashboard';
        } else {
            this._onHashChange();
        }
    },

    /**
     * 导航到指定路由
     */
    navigate(path) {
        window.location.hash = `#${path}`;
    },

    _onHashChange() {
        const hash = window.location.hash.slice(1) || '/dashboard';  // 去掉 #
        const path = '/' + hash.split('/').filter(Boolean)[0];       // 取一级路径

        const page = this._routes[path];
        if (!page) {
            this._container.innerHTML = `
                <div class="placeholder-page">
                    <div class="placeholder-icon">?</div>
                    <div class="placeholder-title">页面未找到</div>
                    <div class="placeholder-desc">路径 "${hash}" 不存在</div>
                </div>
            `;
            return;
        }

        // 销毁旧页面
        if (this._currentPage && this._currentPage.destroy) {
            this._currentPage.destroy();
        }

        // 渲染新页面
        this._currentPage = page;
        this._container.innerHTML = page.render();

        // 执行挂载逻辑（绑定事件等）
        if (page.mount) {
            page.mount();
        }

        // 更新导航高亮
        this._updateNav(path);

        // 更新页面标题
        this._updateTitle(page);
    },

    _updateNav(activePath) {
        document.querySelectorAll('.nav-item').forEach(item => {
            const page = item.getAttribute('data-page');
            if (`/${page}` === activePath) {
                item.classList.add('active');
            } else {
                item.classList.remove('active');
            }
        });
    },

    _updateTitle(page) {
        const titleEl = document.getElementById('page-title');
        if (titleEl && page.title) {
            titleEl.textContent = page.title;
        }
    },
};
