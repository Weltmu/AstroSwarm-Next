# 星巢 · 看 NapCat 日志（不会乱码）
#
# 为什么专门做个脚本：
#   · NapCat 的日志是 **UTF-8 + 带 ANSI 颜色码**，用记事本/`type`/普通 PowerShell 直接打开会花屏
#     （表现为「鍔犺浇鎴愬姛」或「←[32minfo←[39m」这类火星文）；
#   · 本脚本：按 UTF-8 解码 + 去掉颜色码 + 跟随最新日志，中文清清楚楚。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File 看NapCat日志.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File 看NapCat日志.ps1 -Once        # 只看一眼，不跟随
#   powershell -NoProfile -ExecutionPolicy Bypass -File 看NapCat日志.ps1 -Path "D:\x\napcat.log"
param(
    [string]$Path = "",
    [switch]$Once,
    [int]$Tail = 40
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not $Path) {
    # 常见位置里挑最新的那个 napcat.log
    $cands = @()
    foreach ($d in @(
        "$env:USERPROFILE\Desktop\NapCat.Shell.Windows.OneKey",
        "$env:USERPROFILE\Desktop\NapCat.Shell.Windows.OneKey\bootmain",
        "$env:USERPROFILE\Downloads",
        "$env:USERPROFILE\Documents"
    )) {
        if (Test-Path $d) {
            $cands += Get-ChildItem $d -Recurse -File -Filter "napcat.log" -ErrorAction SilentlyContinue -Depth 3
        }
    }
    if (-not $cands) {
        Write-Host "没找到 napcat.log。" -ForegroundColor Yellow
        Write-Host "可以这样指定： -Path `"C:\完整\路径\napcat.log`""
        exit 1
    }
    $Path = ($cands | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
}

Write-Host "日志文件：$Path" -ForegroundColor Cyan
Write-Host ("（最后更新：{0}）" -f (Get-Item $Path).LastWriteTime) -ForegroundColor DarkGray
Write-Host ("-" * 70)

# 去 ANSI 颜色码的函数
$ansi = [regex]'(\x1B\[[0-9;?]*[a-zA-Z])|(\[[0-9]{1,2}m)'

function Show-New {
    param([string]$file, [int]$tail)
    $lines = [System.IO.File]::ReadAllLines($file, [System.Text.Encoding]::UTF8)
    $slice = if ($lines.Count -gt $tail) { $lines[($lines.Count - $tail)..($lines.Count - 1)] } else { $lines }
    foreach ($l in $slice) { Write-Host ($ansi.Replace($l, "")) }
}

Show-New -file $Path -tail $Tail
if ($Once) { exit 0 }

Write-Host ("-" * 70)
Write-Host "（跟随中，按 Ctrl+C 退出）" -ForegroundColor DarkGray
$pos = (Get-Item $Path).Length
while ($true) {
    Start-Sleep -Milliseconds 800
    $now = Get-Item $Path -ErrorAction SilentlyContinue
    if (-not $now) { continue }
    if ($now.Length -lt $pos) { $pos = 0 }        # 日志被轮转/截断
    if ($now.Length -gt $pos) {
        $fs = [System.IO.File]::Open($Path, 'Open', 'Read', 'ReadWrite')
        try {
            $fs.Seek($pos, 'Begin') | Out-Null
            $sr = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8)
            $chunk = $sr.ReadToEnd()
            $pos = $fs.Position
            foreach ($l in ($chunk -split "`r?`n")) {
                if ($l.Trim()) { Write-Host ($ansi.Replace($l, "")) }
            }
        } finally { $fs.Close() }
    }
}
