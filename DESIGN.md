# Codex 用量监控 · Linux 桌面版 — 设计大纲

> 调研日期：2026-08-12。结论：目前**没有成熟的 Linux 专用 Codex 额度桌面监控**，
> 本设计基于对现有开源实现的调研（见文末参考），数据源方案已在本机实测验证。

## 1. 背景与调研结论

### 1.1 现有开源实现对比

| 项目 | Linux 支持 | 技术栈 | 数据源 | 形态 |
|------|-----------|--------|--------|------|
| qcodingdev/codex-usage-monitor | ❌ 仅 macOS | Swift 原生 | app-server JSON-RPC | 菜单栏 + 悬浮面板 |
| DiMY-CN/CodexQuotaMonitor | ❌ 仅 Windows | WPF / Python-Tk | app-server JSON-RPC | 任务栏贴靠悬浮窗 + 托盘 |
| k7631159/ai-fuelgauge | ⚠️ 未测试 | Python 标准库 + pystray | app-server JSON-RPC | CLI + 托盘 + 浮动 HUD |
| xiufengsun/TokenTracker | ✅ AppImage | Node/TS + WebKitGTK | notify hook + 本地日志 | 托盘 + Web dashboard |
| prefect12/codex-token-meter | ❌ 仅 macOS | Swift | 本地日志 | 菜单栏 |

**空白点**：Linux 上唯一可用的 TokenTracker 是重型多工具聚合方案（SQLite + Web 服务 + webview），
没有"轻量、Codex 专用、实时显示余量"的桌面应用。

### 1.2 数据源方案（已在本机验证 ✅）

主流项目共识：启动 `codex app-server` 子进程，通过 stdin/stdout 走 JSON-RPC：

```
→ {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{...}}}
→ {"jsonrpc":"2.0","id":2,"method":"account/rateLimits/read","params":{}}
```

本机（codex-cli 0.147.0）实测返回：

- `rateLimits.primary`：`usedPercent`（已用百分比）、`windowDurationMins`（窗口分钟数，
  ~300=5小时窗，~10080=周窗）、`resetsAt`（重置时间戳）
- `rateLimits.secondary`：第二窗口（可能为 null）
- `rateLimitsByLimitId`：多限额桶（如 `codex_bengalfox` = GPT-5.3-Codex-Spark 独立额度）
- `credits`：Business/Enterprise 信用额度（hasCredits / unlimited / balance）
- `planType`：套餐类型（本机为 `prolite`）

优点：不读 `auth.json`、不碰凭证，认证由 codex CLI 自己完成；只读、隐私安全。
注意：该协议标记为 experimental，CLI 升级可能变更，需做容错。

### 1.3 本机环境约束

- Ubuntu GNOME (X11)。**GNOME 默认不显示系统托盘**，需用户安装 AppIndicator 扩展 →
  托盘只能作为辅助入口，**悬浮窗必须是主形态**。
- 系统 Python 无 PyQt6 / pystray / AyatanaAppIndicator，需 venv 安装。

## 2. 产品目标

1. 桌面悬浮窗实时显示 Codex 额度余量（5 小时窗 + 周窗的百分比、剩余量、重置倒计时）
2. 轻量：单进程、低内存、秒级启动，不依赖 Node/浏览器
3. 隐私：只读本地 app-server，网络零外发
4. 容错：CLI 升级 / 未登录 / 查询失败时优雅降级（显示陈旧数据 + 状态标识）

非目标（v1 不做）：历史统计/图表、多账号切换、成本估算、云同步。

## 3. 技术选型

**Python 3 + PyQt6**，理由：

| 候选 | 评估 |
|------|------|
| **PyQt6** ✅ | 无边框置顶透明悬浮窗、托盘（QSystemTrayIcon）、绘图（进度环）一套全包；X11/Wayland 都稳 |
| pystray + Tkinter | ai-fuelgauge 同款，但 Tk 悬浮窗观感差，pystray 在 GNOME 依赖 AppIndicator |
| Tauri/Electron | 界面最漂亮，但引入 Node/Rust 工具链和百倍体积，违背"轻量"目标 |

核心依赖：`PyQt6`（GUI + 托盘）。仅此一个，数据源用标准库 subprocess 实现。

## 4. 架构设计

```
┌─────────────────────────────────────────────┐
│                UI 层 (PyQt6)                 │
│  ┌──────────────┐      ┌─────────────────┐  │
│  │  FloatingHud │      │ TrayIcon (辅助)  │  │
│  │  悬浮窗主形态 │      │ 菜单/状态摘要    │  │
│  └──────▲───────┘      └────────▲────────┘  │
└─────────┼───────────────────────┼───────────┘
          │      Qt Signal/Slot   │
┌─────────┴───────────────────────┴───────────┐
│              StateStore (内存态)             │
│  当前快照 / 上次成功快照(24h) / 错误状态      │
├─────────────────────────────────────────────┤
│              QuotaFetcher (QThread)          │
│  轮询调度: 活跃 60s / 空闲 180s / 指数退避    │
├─────────────────────────────────────────────┤
│              AppServerClient                 │
│  启动 codex app-server → JSON-RPC → 解析     │
│  每次查询独立进程，查完即 terminate（防泄漏）  │
└─────────────────────────────────────────────┘
```

关键设计点：

- **线程模型**：fetch 放 QThread，结果经 signal 回主线程刷 UI，界面永不卡死。
- **进程管理**：每次查询 spawn 新 app-server（参考 codex-usage-monitor "查完即释放"），
  8s 超时 kill；避免长驻进程泄漏。
- **刷新策略**：窗口可见 60s；最小化/隐藏 180s；连续失败指数退避（30s→5min 封顶）；
  检测到 codex 会话活跃（`~/.codex/sessions` 最新文件 mtime < 5min）时加速到 30s。
- **缓存降级**：成功快照写 `~/.cache/codex-quota/last-good.json`（保留 24h），
  查询失败时展示陈旧数据 + 灰色"更新于 x 分钟前"标识。
- **窗口分类**：按 `windowDurationMins` 归一化：≤360→"5小时"，≥5000→"本周"，其他→"自定义(N小时)"。

## 5. UI 设计

### 悬浮窗（主形态，~260×120px，可拖、置顶、半透明）

```
┌──────────────────────────────┐
│ ⚡ Codex 额度    prolite  ⟳ │  ← 标题栏：套餐 + 手动刷新
│ 本周    ████████░░ 91%       │  ← 进度条：绿<70 / 黄<90 / 红≥90
│         重置于 08-18 21:30   │
│ 5小时   ██░░░░░░░░ 18%       │
│         3 小时 12 分后重置    │
│ ─────────────────────────── │
│ Spark 2% · 更新于 30 秒前    │  ← 附加限额桶 + 数据新鲜度
└──────────────────────────────┘
```

- 紧凑/展开两态：单击切换（展开态附加额度明细列表）
- 滚轮调透明度；位置记忆；≥90% 时边框红色呼吸提醒
- 右键菜单：立即刷新 / 置顶开关 / 透明度 / 开机自启 / 退出

### 托盘（辅助形态）

- 彩色圆点图标反映最高使用率（绿/黄/红）
- 菜单：显示/隐藏悬浮窗、立即刷新、额度摘要、退出
- README 注明 GNOME 需 AppIndicator 扩展；KDE 开箱即用

## 6. 目录结构

```
codex_company/
├── pyproject.toml
├── README.md
├── codex_quota/
│   ├── __main__.py          # 入口: python -m codex_quota [--cli|--hud]
│   ├── cli.py               # CLI 模式: 一次性输出百分比+进度条, --json
│   ├── app_server.py        # AppServerClient: 进程管理 + JSON-RPC + 解析
│   ├── fetcher.py           # QuotaFetcher: 轮询调度 + 退避 + 缓存
│   ├── state.py             # StateStore: 快照模型 + 磁盘缓存
│   ├── ui/
│   │   ├── hud.py           # 悬浮窗
│   │   ├── tray.py          # 托盘
│   │   └── widgets.py       # 进度条/进度环自绘组件
│   └── assets/              # 图标 svg
├── packaging/
│   └── codex-quota.desktop  # autostart / 应用菜单条目
└── tests/
    ├── test_parse.py        # JSON-RPC 响应解析(用本机实测样本做 fixture)
    └── test_fetcher.py      # 退避/缓存逻辑
```

## 7. 里程碑

| 阶段 | 内容 | 验收标准 |
|------|------|---------|
| M1 数据源 | AppServerClient + CLI 模式 | `python -m codex_quota --cli` 输出本机额度（本设计已验证可行性） |
| M2 悬浮窗 | PyQt6 HUD：进度条、倒计时、拖动、置顶 | 60s 自动刷新，手动刷新可用 |
| M3 健壮性 | 缓存降级、退避、未登录/CLI 缺失提示 | 断网/kill codex 后 UI 不崩、显示陈旧数据 |
| M4 托盘 | QSystemTrayIcon + 右键菜单 + 阈值颜色 | KDE 可用；GNOME 文档说明扩展依赖 |
| M5 打磨 | 透明度、紧凑/展开、开机自启、i18n(中/英) | 连续运行 24h 无内存/进程泄漏 |

## 8. 风险与对策

| 风险 | 对策 |
|------|------|
| app-server 协议变更（experimental） | 解析层字段全做可选 + 默认值；解析失败整体降级到缓存；CLI 版本探测 |
| GNOME 无托盘 | 悬浮窗为主形态，托盘仅辅助；README 写明扩展依赖 |
| codex 未登录/未安装 | 启动探测 `codex` 可执行文件；缺失时 UI 显示引导提示而非报错 |
| 高频轮询被限速 | 60s 起步 + 退避；进程每次即查即毁 |

## 9. 参考项目

- 数据源协议参考：https://github.com/k7631159/ai-fuelgauge （JSON-RPC 交互、字段归一化）
- 刷新/进程管理参考：https://github.com/qcodingdev/codex-usage-monitor （查完即释放、活跃/空闲双频）
- UI 形态参考：https://github.com/DiMY-CN/CodexQuotaMonitor （紧凑悬浮窗 + 窗口分类逻辑）
- Linux 打包参考：https://github.com/mm7894215/TokenTracker （AppImage、ayatana 依赖清单）
