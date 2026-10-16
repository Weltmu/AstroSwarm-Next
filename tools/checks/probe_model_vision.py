# -*- coding: utf-8 -*-
r"""直接测：当前 API 模型到底能不能看图（用机器人 .env 里的密钥，不打印明文）。

做法：造一张"黑底大白字 A"的图，问它"图里是什么字，只回一个字"。
  · 答 "A" → 主模型能看图，问题在我们这边；
  · 答"看不到/没有图"或答错 → 主模型是纯文本模型，必须走备用视觉模型。
"""
import base64
import io
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

# 2026-10-11：机器人 .env 的位置不再写死 Windows（Linux 上原来直接 FileNotFoundError）。
# 顺序：QBM_BOT_ENV > Windows 安装目录 > Linux 无头端 > 本仓库开发态。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _src import repo_root  # noqa: E402

_ENV_CANDIDATES = [
    Path(r"D:\AstroSwarm 星群\机器人\bot\.env"),
    Path("/opt/astroswarm/data/astroswarm/bot/.env"),
    repo_root() / "机器人" / "bot" / ".env",
]

ENV = (Path(os.environ["QBM_BOT_ENV"]) if os.environ.get("QBM_BOT_ENV")
       else next((p for p in _ENV_CANDIDATES if p.is_file()), _ENV_CANDIDATES[0]))
print(f"机器人 .env：{ENV}（{'存在' if ENV.is_file() else '不存在'}）")


def load_env(p: Path) -> dict:
    cfg = {}
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip()
    return cfg


env = load_env(ENV)
api_url = (env.get("DEEPSEEK_API_URL") or "https://api.deepseek.com").rstrip("/")
api_key = env.get("DEEPSEEK_API_KEY") or ""
model = env.get("DEEPSEEK_MODEL") or ""
print("接口:", api_url, "| 配置的模型:", model, "| 密钥:", "已读取" if api_key else "空")
assert api_key, "没有读到密钥"

# 造一张很明确的白底黑字图（大号 "A"）
img = Image.new("RGB", (200, 200), (255, 255, 255))
d = ImageDraw.Draw(img)
d.text((60, 60), "A", fill=(0, 0, 0))
try:
    from PIL import ImageFont

    f = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 150)
    img = Image.new("RGB", (200, 200), (255, 255, 255))
    ImageDraw.Draw(img).text((40, 10), "A", fill=(0, 0, 0), font=f)
except Exception:  # noqa: BLE001
    pass
buf = io.BytesIO()
img.save(buf, format="PNG")
b64 = base64.b64encode(buf.getvalue()).decode()
print("测试图: 200x200 白底黑字「A」，", len(b64), "字节 base64")


def ask(m: str) -> str:
    payload = {
        "model": m,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}},
                {"type": "text", "text": "图里是什么字符？只回那一个字符。"},
            ],
        }],
        "max_tokens": 20,
        "temperature": 0,
    }
    req = urllib.request.Request(
        f"{api_url}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
        txt = (data["choices"][0]["message"].get("content") or "").strip()
        return f"HTTP 200 | {time.time()-t0:.1f}s | 回答={txt!r}"
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200]
        return f"HTTP {e.code} | {body}"
    except Exception as e:  # noqa: BLE001
        return f"异常 {type(e).__name__}: {e}"


for m in dict.fromkeys([model, "deepseek-v4-pro", "deepseek-flash"]):
    if not m:
        continue
    out = ask(m)
    sees = bool(re.search(r"\bA\b|「A」|\"A\"|'A'", out)) and "HTTP 200" in out
    print(f"  {m:<20} → {out}")
    print(f"      {'✅ 能看图' if sees else '❌ 看不到图（纯文本模型或忽略图片）'}")
