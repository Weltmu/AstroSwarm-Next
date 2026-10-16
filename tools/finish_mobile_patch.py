# -*- coding: utf-8 -*-
"""官网手机端补丁：追加 handover 一行 + 快照 + 线上复核。"""
import subprocess
import time
from pathlib import Path

ROOT = Path("/opt/qbot_website")
HANDOVER = ROOT / ".coord" / "handover.md"

line = ("- " + time.strftime("%Y-%m-%d %H:%M") + " 客户端会话：手机端实测后补「可点区域 ≥44px」"
        "（390×844 真机尺寸量出来：导航链接原来只有 18px 高）。改动两处："
        "space/space.css 末尾加小屏媒体查询（下载/教程/插件等用外部样式的页），"
        "index.html 最后一段内联样式里加同样规则（首页是内联样式）。"
        "实测：/ 与 download.html 横向溢出 0px、导航链接 44px、小目标 15→3（剩的是正文行内链接）。"
        "回滚：space.css.bak_*_mobiletap 与 index.html.bak_*_mobiletap。\n")
with HANDOVER.open("a", encoding="utf-8") as fh:
    fh.write(line)
print("handover 已追加一行")

out = subprocess.run(["bash", str(ROOT / ".coord" / "site_fingerprint.sh"), "snapshot"],
                     capture_output=True, text=True, cwd=str(ROOT))
print(out.stdout.strip()[-120:])

for path in ("/", "/download.html", "/tutorial.html", "/plugins.html", "/space/space.css"):
    code = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         "https://astroswarm.cn" + path], capture_output=True, text=True).stdout.strip()
    print(f"  {path} → HTTP {code}")
