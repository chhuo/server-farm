/**
 * API 请求封装
 * 统一的 HTTP 请求工具，自动处理错误和 JSON 解析。
 */

const API = {
    baseURL: '',  // 同源，无需前缀

    /**
     * 发起 GET 请求
     */
    async get(path) {
        try {
            const res = await fetch(`${this.baseURL}${path}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
            return await res.json();
        } catch (err) {
            console.error(`[API] GET ${path} 失败:`, err);
            throw err;
        }
    },

    /**
     * 发起 POST 请求
     */
    async post(path, data = {}) {
        try {
            const res = await fetch(`${this.baseURL}${path}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data),
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
            return await res.json();
        } catch (err) {
            console.error(`[API] POST ${path} 失败:`, err);
            throw err;
        }
    },

    // ── 业务 API ──

    /** 获取本机系统信息 */
    getSystemInfo() {
        return this.get('/api/v1/system/info');
    },
};
