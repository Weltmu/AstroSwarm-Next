# -*- coding: utf-8 -*-
"""教程页改完之后：追加 handover 一行 + 校验。"""
import subprocess
import time
from pathlib import Path

ROOT = Path("/opt/qbot_website")
PAGE = ROOT / "tutorial.html"
HANDOVER = ROOT / ".coord" / "handover.md"

raw = PAGE.read_text(encoding="utf-8")
checks = {
    "新一节在": 'id="qqofficial"' in raw,
    "提到接收所有消息": "接收所有消息" in raw,
    "提到 q.qq.com": "q.qq.com" in raw,
    "FAQ 还在": 'id="faq"' in raw,
    "没被截断（有收尾标签）": raw.rstrip().endswith("</html>"),
}
for name, ok in checks.items():
    print(("✓ " if ok else "✗ ") + name)

line = ("- " + time.strftime("%Y-%m-%d %H:%M") + " 客户端会话：tutorial.html 增补一节"
        "「QQ 官方机器人 · 从 0 到能聊」（建机器人 → 沙箱联调 → 打开「接收所有消息」→ 群里 @ 一次 → "
        "平台限制 → 体检按钮），插在 FAQ 之前；原因：历史欠账 #12（线上教程里 q.qq.com 0 命中，"
        "用户不知道要去平台开全量消息，导致「群里 @ 它不回」无解）。"
        "回滚：同目录 tutorial.html.bak_*_qqofficial。\n")
with HANDOVER.open("a", encoding="utf-8") as fh:
    fh.write(line)
print("handover 已追加一行")

print(subprocess.run(["bash", "/opt/qbot_website/.coord/site_fingerprint.sh", "snapshot"],
                     capture_output=True, text=True, cwd=str(ROOT)).stdout.strip()[-120:])
out = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                      "https://astroswarm.cn/tutorial.html"], capture_output=True, text=True)
print("线上 tutorial.html HTTP", out.stdout.strip())
body = subprocess.run(["curl", "-s", "https://astroswarm.cn/tutorial.html"],
                      capture_output=True, text=True).stdout
print("线上含新一节:", 'id="qqofficial"' in body, "| 含「接收所有消息」:", "接收所有消息" in body)
