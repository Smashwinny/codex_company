# codex-quota

Linux 桌面端 AI 编程工具额度实时监控（**Codex + Kimi**），悬浮窗 + 系统托盘。

- **Codex 数据源**：本地 `codex app-server` 的只读 JSON-RPC 方法 `account/rateLimits/read`，
  不读取 `auth.json`、不接触登录凭证、网络零外发。
- **Kimi 数据源**：本地 `kimi web` 服务器的只读接口 `GET /api/v1/oauth/usage`
  （Bearer token 从其 stdout 解析，服务器随应用退出自动回收）。
- **隐私**：所有数据仅保留在本机。

> 设计调研与整体规划见 [DESIGN.md](DESIGN.md)。

## 快速开始

```bash
./install.sh          # 一次性：准备 venv + 依赖 + 桌面入口
./bin/codex-quota     # 启动（脱离终端，终端关闭不影响）
```

之后可以在应用菜单搜索 **Codex Quota** 直接点击启动；开机自启在托盘菜单勾选。
日志：`~/.cache/codex-quota/hud.log`。

## 功能

### 悬浮窗（默认模式）

- 无边框、置顶、半透明圆角悬浮窗，左键拖动移动位置（**位置记忆**）
- **多 provider 分区**：● Codex（绿）/ ● Kimi（紫），各自显示套餐/模型与限流窗口；
  单个 provider 失败只影响自己的分区（显示内联错误），其余正常
- **模型徽章**：标题栏显示当前模型与推理强度（读 `~/.codex/config.toml`，每次刷新重读），
  fast 模型（Spark / fast tier）显示 ⚡ 橙色实心徽章，effort 分级着色（low 绿 / medium 黄 / high 红）
- 每个限流窗口一行：进度条 + 剩余百分比 + 重置倒计时（显示**剩余**额度；绿 >30% / 黄 ≤30% / 红 ≤10%）
- 附加限额桶（如 GPT-5.3-Codex-Spark）自动列出
- **滚轮调透明度**（0.3–1.0，持久化）；**双击切换紧凑模式**（只留主限额行）
- 取数在后台线程，界面不卡；⟳ 按钮手动刷新
- **智能刷新**：窗口可见 60s / 隐藏 180s；检测到 codex 会话活跃（正在跑任务）加速到 30s；
  连续失败指数退避（30s→5min 封顶），成功后恢复
- **缓存降级**：成功快照存 `~/.cache/codex-quota/last-good.json`（24h 有效）；
  启动即显示缓存，查询失败时展示陈旧数据并标注"数据陈旧"
- **错误引导**：未安装 CLI / 未登录 / 超时分别给出可操作的提示
- **中英双语**：跟随系统语言（`LANG`），`CODEX_QUOTA_LANG=zh|en` 可强制指定
- 底部显示数据新鲜度（"更新于 x 前"），每 30s 重排倒计时

> 注意：Qt 6.5+ 的 xcb 插件依赖 `libxcb-cursor0`（`sudo apt install libxcb-cursor0`）。

### 系统托盘

托盘可用时（KDE 开箱即用；GNOME 需 AppIndicator 类扩展）自动启用：

- 彩色圆点图标反映**最低剩余量**（绿 >30% / 黄 ≤30% / 红 ≤10%，无数据灰）
- 左键单击：显示/隐藏悬浮窗
- 右键菜单：显隐悬浮窗 / 立即刷新 / 开机自启（勾选，写 `~/.config/autostart/`）/ 额度摘要 / 退出
- 悬浮窗的 × 变为"隐藏到托盘"，从托盘菜单退出应用
- 托盘不可用（如未装扩展的 GNOME）时自动回退：仅悬浮窗，关窗即退出

### CLI 模式

```bash
python -m codex_quota --cli          # 人类可读输出
python -m codex_quota --cli --json   # JSON 输出（供脚本/其他 UI 消费）
```

输出示例：

```
Codex 额度（套餐: prolite）
  本周     ██░░░░░░░░░░░░░░░░░░   剩 9%  🔴 5 天 13 小时后重置（08-18 10:10）
  ── GPT-5.3-Codex-Spark ──
  本周     ████████████████████  剩 98%  🟢 5 天 14 小时后重置（08-18 11:31）
更新于 20:15:33
```

退出码：`0` 成功；`2` 未安装 codex CLI；`3` 查询失败（未登录/超时/协议错误）。

环境变量 `CODEX_BIN` 可指定 codex 可执行文件路径。

## 前提

- 已安装并登录 Codex CLI（`codex login`）
- Python ≥ 3.10（CLI 模式仅用标准库，零依赖）

## 测试

```bash
pip install -e ".[test]"
pytest
```

## 路线图

- [x] M1 数据源 + CLI
- [x] M2 PyQt6 悬浮窗（进度条、倒计时、拖动、置顶、60s 自动刷新）
- [x] M3 缓存降级、失败退避、未登录引导
- [x] M4 系统托盘（GNOME 需 AppIndicator 扩展）
- [x] M5 透明度/紧凑展开/开机自启/i18n/位置记忆
- [x] M6 多模型用量（Codex + Kimi 双 provider 分区显示）

## 环境变量

| 变量 | 作用 |
|------|------|
| `CODEX_BIN` | 指定 codex 可执行文件路径 |
| `CODEX_HOME` | 指定 codex 数据目录（默认 `~/.codex`） |
| `KIMI_BIN` | 指定 kimi 可执行文件路径（默认自动探测 `~/.kimi-code/bin/kimi`） |
| `CODEX_QUOTA_PROVIDERS` | 启用的 provider，逗号分隔（如 `codex,kimi`，默认全部可用者） |
| `CODEX_QUOTA_LANG` | 强制界面语言 `zh` / `en`（默认跟随 `LANG`） |

设置存于 `~/.config/codex-quota/settings.json`（透明度/紧凑模式/位置），
缓存存于 `~/.cache/codex-quota/last-good.json`。

## License

MIT
