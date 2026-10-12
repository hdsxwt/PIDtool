$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "错误: 找不到虚拟环境 $python"
    Write-Host "请先执行: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e ."
    exit 1
}

& $python -m PyInstaller `
    --noconfirm --clean --windowed `
    --name PIDtool `
    --hidden-import serial.tools.list_ports_windows `
    --distpath dist --workpath build `
    main.py 2>&1 | Write-Host

if ($LASTEXITCODE -ne 0) {
    Write-Host "打包失败, 退出码 $LASTEXITCODE"
    exit $LASTEXITCODE
}

$exe = Join-Path $root "dist\PIDtool\PIDtool.exe"
Write-Host ""
Write-Host "打包完成: $exe"
Write-Host "可将整个目录复制到程序目录, 例如: C:\Program Files\PIDtool"
Write-Host "然后右键 PIDtool.exe -> 发送到 -> 桌面快捷方式"
