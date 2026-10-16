$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# Build venv MUST be Python 3.12 x64 (NOT 3.14).
# Release builds shipped to users are built on 3.12; the base interpreter lives at
# D:\Python312\python.exe. PyInstaller >= 6.16 is required either way.
$venvDir = ".build_venv_py312"
$basePy = "D:\Python312\python.exe"
if (-not (Test-Path "$venvDir\Scripts\python.exe")) {
    Write-Host "Creating build venv from $basePy ..."
    if (-not (Test-Path $basePy)) { $basePy = "python" }
    & $basePy -m venv $venvDir
}
$py = "$venvDir\Scripts\python.exe"
Write-Host "安装 PySide6 / PyInstaller ..."
& $py -m pip install -U pip -i https://pypi.tuna.tsinghua.edu.cn/simple
& $py -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
& $py -m pip install -U "pyinstaller>=6.16" -i https://pypi.tuna.tsinghua.edu.cn/simple

# Strip stale bytecode from dirs that ship as *data files* (assets/plugins/**,
# core/agent/** -> appr/). A 3.14-compiled .pyc riding along in a 3.12 bundle is
# dead weight at best; sourceless .pyc would break the plugin on a version switch.
foreach ($d in @("src\qbotmanager\assets", "src\qbotmanager\core\agent")) {
    if (-not (Test-Path $d)) { continue }
    Get-ChildItem $d -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
    Get-ChildItem $d -Recurse -File -Include "*.pyc", "*.pyo" | Remove-Item -Force
}

Write-Host "打包 AstroSwarm.exe（AstroSwarm.spec，含 logo 图标）..."
& $py -m PyInstaller --noconfirm --clean AstroSwarm.spec

Write-Host "打包 uninstall.exe（uninstall.spec）..."
& $py -m PyInstaller --noconfirm --clean uninstall.spec
Write-Host "完成: dist\AstroSwarm\（onedir 目录，入口 AstroSwarm.exe） / dist\uninstall.exe"
