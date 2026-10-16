# -*- coding: utf-8 -*-
r"""用**档案插件自己的 Brain** 端到端测看图（不是裸 API）：
   输入一张白底黑字「A」的图当 data URL，看 `think()` 能不能拿回"看到 A"的回答。

重点排查：思考模型（reasoning_content）会不会让 content 变空 → 被当成"没回复"。
"""
import asyncio
import base64
import importlib.util
import io
import sys
import time
import types
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 2026-10-11：机器人目录不再写死 Windows（Linux 上原来直接 FileNotFoundError）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _src import plugin_dir, repo_root  # noqa: E402

_BOT_CANDIDATES = [
    Path(r"D:\AstroSwarm 星群\机器人\bot"),
    Path("/opt/astroswarm/data/astroswarm/bot"),
    repo_root() / "机器人" / "bot",
]
BOT = next((p for p in _BOT_CANDIDATES if p.is_dir()), _BOT_CANDIDATES[0])
SRC = plugin_dir("liqinghan")
print(f"机器人目录：{BOT}\n插件目录：{SRC}")

env = {}
for line in (BOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

# 加载插件包
for name in ("nonebot", "nonebot.adapters", "nonebot.adapters.qq"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["nonebot"].logger = types.SimpleNamespace(
    info=lambda *a, **k: None, warning=lambda *a, **k: None,
    error=lambda *a, **k: None, exception=lambda *a, **k: None)
pkg = types.ModuleType("lqpkg")
pkg.__path__ = [str(SRC)]
sys.modules["lqpkg"] = pkg
spec = importlib.util.spec_from_file_location("lqpkg.brain", SRC / "brain.py")
brain_mod = importlib.util.module_from_spec(spec)
sys.modules["lqpkg.brain"] = brain_mod
spec.loader.exec_module(brain_mod)

img = Image.new("RGB", (240, 240), (255, 255, 255))
try:
    f = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 180)
    ImageDraw.Draw(img).text((50, 10), "A", fill=(0, 0, 0), font=f)
except Exception:  # noqa: BLE001
    ImageDraw.Draw(img).text((80, 80), "A", fill=(0, 0, 0))
buf = io.BytesIO()
img.save(buf, format="PNG")
url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class Cfg:
    ds_api_key = env.get("DEEPSEEK_API_KEY", "")
    ds_api_url = (env.get("DEEPSEEK_API_URL") or "https://api.deepseek.com").rstrip("/")
    ds_model = env.get("DEEPSEEK_MODEL") or "deepseek-flash"
    # 0 = 不限制（与 liqinghan/config.py 的默认口径一致：读不到就 0，不是 300）
    ds_max_tokens = int(env.get("DEEPSEEK_MAX_TOKENS") or "0")


print("模型:", Cfg.ds_model, "| 配置 max_tokens:", Cfg.ds_max_tokens,
      "(0=不限制，带图时保底 1200)")
brain = brain_mod.Brain(Cfg())

# 抓一下**实际发出去的** max_tokens：这是唯一不会说谎的判据。
# （2026-10-11：脚本原来把"读不到就 300"写死，与插件真实默认值（0）不一致，
#   输出的数字会误导人；改成直接看请求体。）
_sent = {}
_orig_post = brain_mod.httpx.AsyncClient.post


async def _spy_post(self, url, **kw):
    payload = kw.get("json")
    if isinstance(payload, dict):
        _sent["max_tokens"] = payload.get("max_tokens", "(未带该字段=不限制)")
        _sent["msg_has_image"] = any(
            isinstance(m.get("content"), list) for m in payload.get("messages", [])
            if isinstance(m, dict))
    return await _orig_post(self, url, **kw)


brain_mod.httpx.AsyncClient.post = _spy_post

t0 = time.time()
try:
    reply = asyncio.run(brain.think(
        "你是李清菡，说话自然简短。",
        [],
        "（对方发来一张图）图里是什么字符？只回那一个字符。",
        [], lambda n, a: "ok", images=[url]))
    print(f"耗时 {time.time()-t0:.1f}s")
    print("实际请求 max_tokens:", _sent.get("max_tokens", "（没抓到）"),
          "| 消息里带图:", _sent.get("msg_has_image"))
    print("think() 返回:", repr(reply))
    if reply.strip() and "A" in reply.upper():
        print("→ ✅ 主模型路径通了：图片直发能拿到回答")
    elif not reply.strip():
        print("→ ❌ 返回空：多半是思考模型把 max_tokens 吃光、content 为空（要修）")
    else:
        print("→ ⚠ 有回答但没识别出 A：", reply[:120])
except Exception as e:  # noqa: BLE001
    print("→ 抛异常:", type(e).__name__, e)
    print("实际请求 max_tokens:", _sent.get("max_tokens", "（没抓到）"))
