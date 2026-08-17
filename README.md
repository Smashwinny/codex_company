# codex-quota

Linux 桌面端 AI 编程工具额度实时监控（**Codex + Kimi**），悬浮窗 + 系统托盘。

- **Codex 数据源**：本地 `codex app-server` 的只读 JSON-RPC 方法 `account/rateLimits/read`，
  不读取 `auth.json`、不接触登录凭证、网络零外发。
- **Kimi 数据源**：本地 `kimi web` 服务器的只读接口 `GET /api/v1/oauth/usage`
  （Bearer token 从其 stdout 解析，服务器随应用退出自动回收）。
- **隐私**：所有数据仅保留在本机。

> 设计调研与整体规划见 [DESIGN.md](DESIGN.md)。

## 快速开始

### 依赖项

| 依赖 | 必需性 | 说明 |
|------|--------|------|
| Python ≥ 3.10 | **必需** | 主程序运行时 |
| Codex CLI（已 `codex login`） | **必需** | Codex 额度的数据源 |
| libxcb-cursor0 | 悬浮窗必需 | Qt xcb 插件依赖；无 root 时安装脚本自动下载到 `vendor/` |
| Kimi CLI（`kimi login`） | 可选 | 检测到就自动增加 Kimi 分区，没有则只显示 Codex |
| 系统托盘（GNOME 需 AppIndicator 扩展） | 可选 | 没有托盘也能用，悬浮窗是主形态 |

### 一键安装

```bash
git clone https://github.com/Smashwinny/codex_company.git
cd codex_company
./install.sh
```

安装脚本会自动：检查依赖（Python 版本 / codex / kimi）→ 创建 `.venv` 并安装 PyQt6
→ 处理 libxcb-cursor（系统没有就免 root 下载到 `vendor/`）→ 创建应用菜单桌面入口。
幂等，可重复运行。

### 运行（三选一）

| 方式 | 命令/操作 |
|------|-----------|
| 应用菜单 | 搜索 **Codex Quota** 点击启动（推荐） |
| 命令行 | `./bin/codex-quota`（脱离终端后台运行，关终端不影响） |
| 开机自启 | 启动后在托盘菜单勾选"开机自启" |

**首次启动会弹出设置向导**：自动检测 Codex CLI（安装/登录）、Kimi、cloudflared，
缺失项旁边直接给"复制命令"按钮，修好点"全部重新检测"即可，全程不用查文档。
之后随时可从托盘菜单"初始设置 / 环境自检"重新打开。

- 停止：`pkill -f "m codex_quota"`（或托盘菜单 → 退出）
- 日志：`~/.cache/codex-quota/hud.log`
- 设置：`~/.config/codex-quota/settings.json`（透明度/紧凑模式/位置）

### 手机查看

应用内置零依赖 Web 服务，提供两种访问地址（控制台和托盘菜单"复制手机访问地址"均可获取）：

| 场景 | 地址形式 | 说明 |
|------|----------|------|
| **任意网络（4G/外出）** | `https://<随机>.trycloudflare.com/t/<token>/` | cloudflared 免费隧道，免 root 免注册，install.sh 自动下载 |
| 同一局域网 | `http://<电脑IP>:8642/t/<token>/` | 不经过公网，延迟更低 |

- 手机浏览器打开后"添加到主屏幕"，当 App 用；页面 30s 自动刷新
- **鉴权**：token 藏在 URL 里（无 token 一律 404），首启生成并持久化；别把完整 URL 分享出去
- **隧道地址是临时的**：每次应用重启会变，以托盘菜单/日志里的当前地址为准
  （需要固定域名可接 Cloudflare 账号的 Named Tunnel，后续可加）
- 关闭手机访问：settings.json 设 `"web_enabled": false`；只关公网隧道：`"tunnel_enabled": false`

### 管理额度来源（providers）

托盘菜单 → **管理额度来源**，无需编辑文件：

- **本地工具**（开关即可）：Codex、Kimi、Claude Code（自动读本地登录凭证）
- **云端服务**（填 API key）：DeepSeek、OpenRouter——key 可填 `$环境变量` 引用，
  点"测试连接"即时验证，保存即热重载（不用重启）

各服务显示形态：

| 服务 | 数据来源 | 显示 |
|------|----------|------|
| Codex | 本地 `codex app-server` JSON-RPC | 5小时/本周窗口 % |
| Kimi | 本地 `kimi web` `/api/v1/oauth/usage` | 5小时/本周窗口 % |
| Claude Code | `api.anthropic.com/api/oauth/usage`（本地 OAuth token） | 5小时/本周窗口 % |
| DeepSeek | `api.deepseek.com/user/balance` | 余额 ¥xx |
| OpenRouter | `openrouter.ai/api/v1/credits` | 已用 % + 余额 $xx |

配置文件为 `~/.config/codex-quota/providers.toml`（权限 600），也可手写：

```toml
[providers.kimi]
enabled = false

[providers.deepseek]
type = "deepseek"
enabled = true
api_key = "$DEEPSEEK_API_KEY"   # 推荐：引用环境变量

[providers.openrouter]
type = "openrouter"
enabled = true
api_key = "$OPENROUTER_API_KEY"
```

### 额度重置推送（ntfy）

任一限流窗口的剩余量**重置回 100%** 时，手机立刻收到推送：

1. 手机装 **ntfy** App（Android/iOS，免费无需注册）
2. 订阅本机主题（托盘菜单"复制 ntfy 通知主题"，或看日志里的 `手机通知:` 行）
3. 完成。此后 Codex/Kimi 任一窗口重置回满即推送，只在跳变时触发、不重复骚扰

- 检测原理：每次刷新对比剩余量，从 <99.5% 跳到 ≥99.5% 视为重置
- 关闭：settings.json 设 `"notify_enabled": false`；换服务器：`ntfy_server`（可自建）
- 主题即凭证，勿外传

### 只要 CLI（无 GUI 依赖）

CLI 模式只用 Python 标准库，不装 PyQt6 也能跑：

```bash
python3 -m codex_quota --cli          # 人类可读
python3 -m codex_quota --cli --json   # JSON（供脚本消费）
```

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

### 系统托盘

托盘可用时（KDE 开箱即用；GNOME 需 AppIndicator 类扩展）自动启用：

- 彩色圆点图标反映**最低剩余量**（绿 >30% / 黄 ≤30% / 红 ≤10%，无数据灰）
- 左键单击：显示/隐藏悬浮窗
- 右键菜单：显隐悬浮窗 / 立即刷新 / 开机自启（勾选，写 `~/.config/autostart/`）/ 额度摘要 / 退出
- 悬浮窗的 × 变为"隐藏到托盘"，从托盘菜单退出应用
- 托盘不可用（如未装扩展的 GNOME）时自动回退：仅悬浮窗，关窗即退出

### CLI 输出示例

```
Codex（套餐: prolite）
  本周     █░░░░░░░░░░░░░░░░░░░   剩 3%  🔴 5 天 10 小时后重置（08-18 10:10）
  ── GPT-5.3-Codex-Spark ──
  本周     ████████████████████  剩 98%  🟢 5 天 12 小时后重置（08-18 11:31）
更新于 23:30:35

Kimi（套餐: kimi-code/k3）
  本周     ████████████████░░░░  剩 82%  🟢 6 天 15 小时后重置（08-19 14:32）
  5小时    ██░░░░░░░░░░░░░░░░░░  剩 10%  🔴 1 分后重置（08-12 23:32）
更新于 23:30:39
```

退出码：`0` 至少一个 provider 成功；`2` 未安装 CLI 类错误；`3` 全部查询失败。

## 测试

```bash
./install.sh                              # 已建好 .venv 则跳过
.venv/bin/pip install -e ".[test]"
.venv/bin/python -m pytest                # 130+ 项，无需显示服务器（offscreen）
```

## 路线图

- [x] M1 数据源 + CLI
- [x] M2 PyQt6 悬浮窗（进度条、倒计时、拖动、置顶、60s 自动刷新）
- [x] M3 缓存降级、失败退避、未登录引导
- [x] M4 系统托盘（GNOME 需 AppIndicator 扩展）
- [x] M5 透明度/紧凑展开/开机自启/i18n/位置记忆
- [x] M6 多模型用量（Codex + Kimi 双 provider 分区显示）
- [x] M7 手机查看（内置 Web 服务 + token 鉴权 + 移动端页面）
- [x] M8 公网访问（cloudflared 隧道，免 root 免注册，4G/外出可看）
- [x] M9 重置推送（额度回 100% 时 ntfy 通知手机）
- [x] M10 首启向导 + provider 管理（开关/DeepSeek/余额显示/热重载）

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
