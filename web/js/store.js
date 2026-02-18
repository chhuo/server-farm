/**
 * 全局状态管理
 * 简单的发布-订阅 Store，存储系统数据。
 */

const Store = {
    _state: {
        systemInfo: null,
        lastUpdate: null,
    },
    _listeners: [],

    get(key) {
        return this._state[key];
    },

    set(key, value) {
        this._state[key] = value;
        this._notify(key, value);
    },

    /**
     * 订阅状态变化
     * @param {function} fn - 回调函数 (key, value)
     * @returns {function} 取消订阅函数
     */
    subscribe(fn) {
        this._listeners.push(fn);
        return () => {
            this._listeners = this._listeners.filter(l => l !== fn);
        };
    },

    _notify(key, value) {
        this._listeners.forEach(fn => fn(key, value));
    },
};
