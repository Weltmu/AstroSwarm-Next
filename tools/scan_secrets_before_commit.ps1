# -*- coding: utf-8 -*-
# 提交前的敏感串扫描：对 git status 里所有 改动/未跟踪 文件逐个搜 .release_guard.txt 的串。
# 用法：powershell -File tools\scan_secrets_before_commit.ps1
$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)

$guardFile = "tools\.release_guard.txt"
if (-not (Test-Path $guardFile)) { Write-Host "[x] 缺 $guardFile"; exit 2 }
$patterns = Get-Content $guardFile -Encoding UTF8 |
    Where-Object { $_.Trim() -ne "" -and -not $_.Trim().StartsWith("#") } |
    ForEach-Object { $_.Trim() }
Write-Host ("守卫串 " + $patterns.Count + " 条")

$paths = git status --short | ForEach-Object {
    $line = $_
    if ($line.Length -lt 4) { return }
    $p = $line.Substring(3).Trim('"')
    if ($p -match ' -> ') { $p = ($p -split ' -> ')[-1].Trim('"') }
    $p
} | Where-Object { $_ -ne "" -and (Test-Path $_ -PathType Leaf) }

$hits = 0
$scanned = 0
foreach ($p in $paths) {
    # 二进制大文件跳过（图标/图片等）
    if ($p -match '\.(ico|png|jpg|jpeg|exe|zip|gz|whl|bin|db|pyc|ttf|otf|woff2?|mp4|mp3)$') { continue }
    $scanned++
    $text = ""
    try { $text = Get-Content -LiteralPath $p -Raw -Encoding UTF8 -ErrorAction Stop } catch { continue }
    if (-not $text) { continue }
    foreach ($pat in $patterns) {
        if ($text.Contains($pat)) {
            Write-Host ("[!] 命中「" + $pat + "」→ " + $p)
            $hits++
        }
    }
}
Write-Host ("扫了 " + $scanned + " 个文本文件，命中 " + $hits + " 处")
if ($hits -gt 0) { exit 1 } else { exit 0 }
