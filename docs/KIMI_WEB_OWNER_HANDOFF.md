# Kimi Web 共享服务交接

## 结论

Ubuntu 上由 `/home/hulk/stupid` 成为 Kimi Web 唯一所有者。`codex_company` 以及其他客户端只发现、查询和复用，不得启动、停止、重启服务，也不得因401执行 `kimi login`。

## Owner

- unit：`/home/hulk/.config/systemd/user/kimi-code-web.service`
- 源模板：`/home/hulk/stupid/systemd/kimi-code-web.service`
- 启动命令：`/home/hulk/.kimi-code/bin/kimi web --no-open --port 58627`
- 故障恢复：`Restart=always`，`RestartSec=5`
- 飞书中继 unit 通过 `Wants/After=kimi-code-web.service` 依赖它；中继重启不会停止 Kimi Web。
- 2026-09-01 观测值：owner PID为137705、监听 `127.0.0.1:58627`。PID会随systemd故障恢复而变化，客户端不得硬编码PID。

## 客户端发现协议

1. 扫描 `~/.kimi-code/server/instances/*.json`。
2. 忽略解析失败、缺少 port/PID、心跳超过120秒的记录。
3. 优先选择端口58627；否则临时选择最新心跳用于兼容诊断。
4. 从选中记录取得 host、port、PID；token 始终单独从 `~/.kimi-code/server.token` 读取。
5. HTTP 401：重新读取 token 并重试一次；再次失败才报告错误。
6. 连接失败：重新发现一次实例；不 spawn、不 kill、不 restart、不 login。

## 涉及源文件

- Owner 与飞书客户端：`/home/hulk/stupid/systemd/kimi-code-web.service`、`src/quota.js`、`src/config.js`
- 桌面额度客户端：`codex_quota/providers/kimi.py`、`codex_quota/providers/base.py`
- 验证：`tests/test_kimi.py`

## 安全要求

任何日志、报错、文档、截图和交接消息都不得包含 `server.token` 内容。允许记录端口、PID、实例文件路径、心跳年龄和HTTP状态。

## 2026-09-01 验证

- `stupid` Node测试16项通过，其中包含401重读token且只重试一次。
- `codex_company` Kimi provider测试16项通过，覆盖owner端口优先、陈旧实例拒绝、不spawn/login、close不停止服务及401重读。
- 两客户端并发真实查询得到一致结果，查询前后Kimi进程集合不变。
- 发现并停止了监听39113的旧实例；它已不再监听，当前唯一监听端口为58627。
