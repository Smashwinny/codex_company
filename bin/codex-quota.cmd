@echo off
rem codex-quota Windows 启动器：pythonw 后台运行，终端关闭不影响。
rem 日志：%LOCALAPPDATA%\codex-quota\hud.log（pythonw 无控制台，由应用内守卫写）
setlocal enabledelayedexpansion
set "ROOT=%~dp0.."
set "PYW=%ROOT%\.venv\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"

rem 单实例提示：应用内还有 QLocalServer 兜底（第二实例会激活已有窗口）。
rem 这里用 app.pid + tasklist 提前给出友好的"已在运行"
set "PIDFILE=%LOCALAPPDATA%\codex-quota\app.pid"
if exist "%PIDFILE%" (
    set /p APP_PID=<"%PIDFILE%"
    tasklist /FI "PID eq !APP_PID!" 2>nul | find "!APP_PID!" >nul
    if !errorlevel! == 0 (
        echo codex-quota 已在运行（PID !APP_PID!）
        exit /b 0
    )
)

start "" /D "%ROOT%" "%PYW%" -m codex_quota %*

rem 3 秒后确认应用写下了自己的 app.pid，否则打印日志末尾
timeout /t 3 /nobreak >nul
set "OLD_PID=%APP_PID%"
set "APP_PID="
if exist "%PIDFILE%" set /p APP_PID=<"%PIDFILE%"
if defined APP_PID if not "%APP_PID%" == "%OLD_PID%" (
    echo codex-quota 已启动（PID %APP_PID%，日志: %LOCALAPPDATA%\codex-quota\hud.log）
    exit /b 0
)
echo codex-quota 可能启动失败，日志末尾： 1>&2
powershell -NoProfile -Command "Get-Content '%LOCALAPPDATA%\codex-quota\hud.log' -Tail 15" 1>&2
exit /b 1
