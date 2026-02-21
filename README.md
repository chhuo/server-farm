# NodePanel

分布式服务器管理面板。支持多节点拓扑、远程命令执行、系统监控、跨设备聊天与审计日志。

## 功能概览

- **多节点管理**：Full / Relay / Temp-Full 三种节点模式，自动故障转移
- **远程终端**：在任意节点上执行命令，支持 NAT 穿透（Relay 心跳转发）
- **系统监控**：实时采集 CPU、内存、磁盘、网络信息
- **任务队列**：异步任务创建、分发、状态追踪
- **跨设备聊天**：内置实时聊天，支持 WebSocket 推送，消息跨节点同步
- **复制中心**：信息片段管理（账号密码、服务器凭据、常用命令、笔记），支持敏感字段遮罩
- **审计日志**：所有命令执行均记录审计
- **增量同步**：节点间仅传输变更数据，降低带宽消耗
- **Web 控制台**：内置 SPA 前端，无需额外部署

---

## 技术栈

| 层 | 技术 |
|---|---|
| Web 框架 | FastAPI + Uvicorn |
| 节点通信 | httpx（异步 HTTP） |
| 系统采集 | psutil |
| 配置 | PyYAML |
| 前端 | 原生 JS SPA |

---

## 节点模式

### Full 模式（Hub）
- 完整数据存储节点
- `connectable=true` 时运行 **Gossip 协议**与其他可直连 Full 节点同步（有界扇出，默认 fan-out=3）
- 接收 Relay 节点心跳，下发任务
- 适合有公网 IP 的服务器

### Full 模式（内网）
- `connectable=false` 的 Full 节点
- 自动发现本地节点表中的可连接 Hub 节点，主动发起双向同步
- 适合无公网 IP 但有完整数据需求的服务器

### Relay 模式
- 轻量节点，无需公网 IP
- 自动发现可连接的 Full 节点，定期发送**心跳**上报系统信息和任务结果
- 通过心跳响应接收待执行任务和全局数据（NAT 友好）

### Temp-Full 模式
- 自动故障转移：当所有可连接 Full 节点不可达时，Relay 节点临时升级为 Full
- Full 节点恢复后自动降级回 Relay

---

## 快速开始

### 安装依赖

```bash
# Linux / macOS
bash install.sh

# Windows
install.bat
```

或手动安装：

```bash
pip install -r requirements.txt
```

### 启动

```bash
# Linux / macOS
bash start.sh

# Windows
start.bat

# 直接运行
python main.py
```

默认监听 `0.0.0.0:8300`，启动后终端会打印访问地址和初始密码。

### 首次登录

启动日志中会显示初始账号和随机密码，登录后请及时修改。

```
══════════════════════════════════════════════════════
  NodePanel v0.1.0  已就绪
────────────────────────────────────────────────────
  访问地址  http://192.168.x.x:8300
  本机回环  http://127.0.0.1:8300
  节点模式  full
────────────────────────────────────────────────────
  ⚠ 初始账号  admin
  ⚠ 初始密码  xxxxxxxxxxxxxxxx
    请登录后及时修改密码！
══════════════════════════════════════════════════════
```

---

## 配置文件

首次启动自动生成 `config.yaml`，主要配置项：

```yaml
app:
  name: NodePanel
  version: 0.1.0
  debug: true

server:
  host: 0.0.0.0
  port: 8300

node:
  id: ""              # 留空则自动生成（hostname-xxxx）
  name: ""            # 显示名称，留空取主机名
  mode: auto          # auto / full / relay
  primary_server: ""  # Relay 模式必填，如 http://1.2.3.4:8300
  public_url: ""      # 本节点对外地址（可选）
  connectable: false  # 是否有公网 IP 可被其他节点直连

peer:
  sync_interval: 30         # Gossip/主动同步基础间隔（秒）
  heartbeat_interval: 10    # Relay 心跳间隔（秒）
  timeout: 10               # 请求超时（秒）
  max_fanout: 3             # Gossip 最大扇出
  max_heartbeat_failures: 3 # 连续失败触发故障转移阈值

security:
  node_key: ""          # 节点通信密钥，留空自动生成
  admin_user: admin
  admin_password: ""    # 留空则随机生成初始密码
  command_blacklist:
    - "rm -rf /"
    - "mkfs"
    - "dd if=/dev/zero"
```

也可通过环境变量覆盖，前缀 `APP_`，双下划线表示层级：

```bash
APP_SERVER__PORT=9000
APP_NODE__MODE=relay
APP_NODE__PRIMARY_SERVER=http://1.2.3.4:8300
```

---

## 项目结构

```
server/
├── main.py                  # 入口，FastAPI 应用创建与生命周期
├── config.yaml              # 配置文件（首次启动自动生成）
├── requirements.txt
│
├── core/
│   ├── bootstrap.py         # 启动引导（Config + Logger 初始化）
│   ├── config.py            # 配置管理器（YAML + 环境变量 + CLI）
│   ├── logger.py            # 日志系统
│   └── node.py              # 节点身份管理（ID、模式、故障转移）
│
├── services/
│   ├── peer_service.py      # Peer 同步（Gossip / 心跳 / 增量同步）
│   ├── task_service.py      # 任务管理与分发
│   ├── executor.py          # 命令执行器（黑名单、超时、编码）
│   ├── collector.py         # 系统信息采集（psutil）
│   ├── auth.py              # 认证与会话管理
│   ├── audit.py             # 审计日志
│   └── storage.py           # 文件存储引擎（JSON 原子写入）
│
├── api/
│   ├── deps.py              # 依赖注入辅助
│   └── v1/
│       ├── router.py        # 路由汇总
│       ├── auth.py          # 登录/注销/改密
│       ├── nodes.py         # 节点列表与状态
│       ├── tasks.py         # 任务执行与查询
│       ├── peer.py          # Peer 同步/心跳接口（含密钥验证）
│       ├── system.py        # 系统信息
│       ├── config_api.py    # 配置读写
│       ├── chat.py          # 聊天 REST + WebSocket
│       └── snippets.py      # 复制中心（信息片段管理）
│
├── models/
│   ├── node.py              # NodeInfo、NodeMode 数据模型
│   └── task.py              # TaskInfo、TaskStatus 数据模型
│
├── web/                     # 前端 SPA
│   ├── index.html
│   ├── css/
│   │   ├── variables.css    # CSS 变量（主题色）
│   │   ├── base.css         # 基础样式
│   │   ├── layout.css       # 布局样式
│   │   └── components.css   # 组件样式
│   └── js/
│       ├── app.js           # 应用入口（认证守卫）
│       ├── router.js        # 前端路由
│       ├── store.js         # 全局状态
│       ├── api.js           # API 封装
│       └── pages/
│           ├── dashboard.js # 仪表盘（系统监控）
│           ├── nodes.js     # 节点管理
│           ├── tasks.js     # 任务管理
│           ├── terminal.js  # 远程终端
│           ├── chat.js      # 聊天
│           ├── snippets.js  # 复制中心
│           ├── settings.js  # 设置
│           └── login.js     # 登录页
│
└── data/                    # 运行时数据（自动创建）
    ├── identity.json        # 节点 ID 和密钥持久化
    ├── nodes.json           # 节点注册表
    ├── states.json          # 节点状态表
    ├── auth.json            # 认证数据
    ├── chat.json            # 聊天消息
    ├── snippets.json        # 信息片段
    ├── sync_meta.json       # 增量同步元数据（per-peer 时间戳）
    ├── tasks/               # 任务文件（每个任务一个 JSON）
    └── audit/               # 审计日志（按天分割的 JSON 文件）
```

---

## API 接口

所有接口前缀 `/api/v1`，除以下路径外均需登录（Cookie `token`）：

### 认证

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| `POST` | `/auth/login` | 登录，返回 token（写入 Cookie） | 免认证 |
| `POST` | `/auth/logout` | 注销 | 免认证 |
| `GET` | `/auth/status` | 检查登录状态 | 免认证 |
| `POST` | `/auth/setup-password` | 首次启动设置新密码 | 需登录 |
| `POST` | `/auth/change-password` | 修改密码 | 需登录 |

### 节点

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| `GET` | `/nodes` | 所有节点列表（含实时状态） | 需登录 |
| `GET` | `/nodes/self` | 本节点信息 | 免认证 |
| `GET` | `/nodes/{id}` | 指定节点详情 | 需登录 |
| `POST` | `/nodes/add` | 手动添加节点（握手验证） | 需登录 |

### 任务

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| `POST` | `/tasks/execute` | 在指定节点执行命令 | 需登录 |
| `POST` | `/tasks/create` | 创建任务 | 需登录 |
| `GET` | `/tasks` | 任务列表 | 需登录 |
| `GET` | `/tasks/audit` | 审计日志 | 需登录 |
| `GET` | `/tasks/{id}` | 任务详情 | 需登录 |

### 系统

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| `GET` | `/system/info` | 本节点系统信息 | 免认证 |

### 聊天

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| `GET` | `/chat/messages` | 获取聊天历史消息 | 需登录 |
| `POST` | `/chat/messages` | 发送聊天消息 | 需登录 |
| `WS` | `/chat/ws` | WebSocket 实时推送 | Cookie 认证 |

### 复制中心（信息片段）

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| `GET` | `/snippets/` | 获取所有片段（可按分类过滤） | 需登录 |
| `POST` | `/snippets/` | 创建片段 | 需登录 |
| `PUT` | `/snippets/{id}` | 更新片段 | 需登录 |
| `DELETE` | `/snippets/{id}` | 删除片段（软删除） | 需登录 |

### Peer 通信（节点间调用）

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| `POST` | `/peer/sync` | Full 节点 Gossip/双向同步 | node_key |
| `POST` | `/peer/heartbeat` | Relay 心跳上报 | node_key |
| `POST` | `/peer/trigger-sync` | 手动触发同步 | 需登录 |

### 配置

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| `GET` | `/config` | 读取配置（敏感字段脱敏） | 需登录 |
| `POST` | `/config/update` | 更新配置（白名单字段） | 需登录 |
| `GET` | `/config/blacklist` | 获取命令黑名单 | 需登录 |
| `POST` | `/config/blacklist` | 更新命令黑名单 | 需登录 |

---

## 增量同步机制

NodePanel 使用 per-peer 增量同步策略，最大限度降低数据传输量：

1. 每个节点为每个 peer 维护一个 `last_sync_time` 时间戳（存储在 `sync_meta.json`）
2. 发送端只发送 `last_sync_time` 之后变更的数据
3. 接收端根据请求中的 `since` 参数过滤返回数据
4. 首次连接时 `last_sync_time=0`，执行全量同步
5. 同步成功后更新 `last_sync_time`

同步的数据包括：节点注册表、节点状态表、聊天消息、信息片段。

---

## 数据持久化

使用基于 JSON 文件的轻量存储引擎（`services/storage.py`），所有数据存放在 `data/` 目录：

- `identity.json`：节点 ID 和通信密钥（首次启动生成，持久不变）
- `nodes.json`：全网节点注册表，Gossip 同步传播
- `states.json`：各节点最新状态（在线/离线、系统信息）
- `auth.json`：管理员账户信息
- `chat.json`：聊天消息记录（跨节点同步，最多保留 500 条）
- `snippets.json`：信息片段数据（跨节点同步，支持软删除）
- `sync_meta.json`：增量同步元数据（每个 peer 的上次同步时间）
- `tasks/*.json`：每个任务独立文件
- `audit/audit_YYYY-MM-DD.json`：按天分割的审计日志

存储引擎特性：
- **原子写入**：先写临时文件再重命名，防止写入中断导致数据损坏
- **线程锁**：per-file 锁防止并发写入冲突
- **读-改-写原子操作**：`update()` 方法支持安全的并发修改

---

## 安全说明

- 密码使用 SHA-256 + 随机 salt 哈希存储
- 会话 Token 有效期 24 小时，内存存储
- 命令执行支持黑名单过滤（配置 `security.command_blacklist`）
- 节点间通信携带 `node_key` 做身份验证（密钥不匹配时拒绝同步）
- 调试模式（`app.debug: true`）下开放 `/api/docs` Swagger 文档
- 初始密码仅在终端 banner 中显示，不通过 API 暴露
