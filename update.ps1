# jietu 一键升级：终止运行中的进程 → 升级 → 静默重启
$ErrorActionPreference = "Continue"
$repo = "git+https://github.com/fendouww/jietu.git"

Write-Host "[1/4] 查找并终止运行中的 jietu 进程..." -ForegroundColor Cyan
$procs = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -in 'python.exe','pythonw.exe' -and $_.CommandLine -match 'jietu' }

# 记住 jietu 所在的 Python 解释器，确保升级到同一环境
$pyExe = $null
if ($procs) { $pyExe = ($procs | Select-Object -First 1).ExecutablePath }

foreach ($p in $procs) {
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
        Write-Host "  已终止 PID $($p.ProcessId)"
    } catch {}
}
Start-Sleep -Milliseconds 1000   # 等文件锁释放

# 解析 python.exe / pythonw.exe 路径
if (-not $pyExe) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $pyExe = $cmd.Source }
}
if (-not $pyExe) {
    Write-Host "找不到 Python，请确认已安装并在 PATH 中。" -ForegroundColor Red
    exit 1
}
$pyDir      = Split-Path $pyExe
$pythonExe  = Join-Path $pyDir 'python.exe'
$pythonwExe = Join-Path $pyDir 'pythonw.exe'

Write-Host "[2/4] 升级 jietu（$pythonExe）..." -ForegroundColor Cyan
& $pythonExe -m pip install --upgrade --force-reinstall --no-deps $repo
if ($LASTEXITCODE -ne 0) {
    Write-Host "升级失败，请检查网络或 git 是否安装。" -ForegroundColor Red
    exit 1
}

Write-Host "[3/4] 静默重启（守护进程）..." -ForegroundColor Cyan
if (Test-Path $pythonwExe) {
    Start-Process $pythonwExe -ArgumentList "-m","jietu.watchdog"
} else {
    Start-Process $pythonExe -ArgumentList "-m","jietu.watchdog"
}

Write-Host "[4/4] 完成！jietu 已升级并在后台运行。" -ForegroundColor Green
