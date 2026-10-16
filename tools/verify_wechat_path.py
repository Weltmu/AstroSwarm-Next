# -*- coding: utf-8 -*-
r"""自己验：微信(iLink)通道的"图片 + 引用"到底有没有进上下文（老板要的是这个）。

用**真的适配器代码**跑：把 iLink 的原始 payload（item_list 形态）喂给
`build_channel_from_wechat`，检查它产出的 `msg.text` 里有没有：
  · 图片的**识别结果**
  · 图片的**原图地址**（转发就靠它）
  · 引用内容
不需要机器人进程、不打扰任何人。

跑法（用机器人自带的 python，那里才有 nonebot 等依赖）：
    <机器人>\python\python.exe tools\verify_wechat_path.py
"""
import asyncio
import sys
import types
from pathlib import Path

BOT_DIR = Path(r"D:\AstroSwarm 星群\机器人\bot")
AI = BOT_DIR / "src" / "plugins" / "ai"

# ---- 用最小桩把 nonebot 顶掉（这个脚本只验适配器，不启动机器人）----
nb = types.ModuleType("nonebot")
class _L:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def debug(self, *a, **k): pass
nb.logger = _L()
sys.modules.setdefault("nonebot", nb)

sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(BOT_DIR / "src"))

RESULTS = []


def check(label, ok, extra=""):
    RESULTS.append((label, ok, extra))
    print(("✓ " if ok else "✗ ") + label + ("" if ok else f"   ← {extra}"))


class FakeEvent:
    """模仿 iLink 适配器造的 Event（字段名照 adapter.py 抄）。"""

    def __init__(self, payload, text=""):
        self.raw = payload
        self.raw_message = text
        self.context_token = "tok"
        self._uid = "o9cqTEST@im.wechat"

    def get_user_id(self):
        return self._uid

    def get_plaintext(self):
        return self.raw_message


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send(self, event, text):
        self.sent.append(text)
        return True


IMG_URL = "https://mm.example.com/pic/aaa.jpg"

PAYLOAD = {
    "message_id": "1",
    "from_user_id": "o9cqTEST@im.wechat",
    "context_token": "tok",
    "item_list": [
        {"type": 1, "text_item": {"text": "这个照片发QQ上"}},
        {"type": 2, "image_item": {"image_url": IMG_URL, "thumb_url": "https://mm.example.com/t.jpg"}},
        {"type": 2, "image_item": {"image_url": "https://mm.example.com/b.jpg"}},
    ],
}

PAYLOAD_QUOTE = {
    "message_id": "2",
    "from_user_id": "o9cqTEST@im.wechat",
    "item_list": [
        {"type": 1, "quote": {"text": "他之前说的那句话"}, "text_item": {"text": "就这个"}},
    ],
}


async def main():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "wechat_probe", AI / "adapters" / "wechat.py")
    mod = importlib.util.module_from_spec(spec)
    pkg = types.ModuleType("wechat_probe_pkg")

    # 适配器用相对导入 `..platform` / `..image2txt` / `..manager`：
    # 把 ai 目录挂成一个包，再把 adapters.wechat 当成它的子模块加载。
    import importlib

    ai_spec = importlib.util.spec_from_file_location(
        "ai_pkg", AI / "__init__.py", submodule_search_locations=[str(AI)])
    ai_pkg = importlib.util.module_from_spec(ai_spec)
    sys.modules["ai_pkg"] = ai_pkg
    try:
        ai_spec.loader.exec_module(ai_pkg)      # 可能因为 require() 失败，容忍
    except Exception as e:  # noqa: BLE001
        print(f"（ai 包导入受限，改用直接加载适配器：{type(e).__name__}）")

    # 直接加载：把相对导入替换成绝对路径加载的兄弟模块
    src = (AI / "adapters" / "wechat.py").read_text(encoding="utf-8")
    src = src.replace("from ..platform import", "from platform_probe import")
    src = src.replace("from ..image2txt import", "from image2txt_probe import")
    src = src.replace("from ..manager import", "from manager_probe import")
    # platform 真身要真的（ChannelMessage 是数据类）
    plat_src = (AI / "platform.py").read_text(encoding="utf-8")
    plat_src = plat_src.replace("from nonebot import logger", "logger = __import__('nonebot').logger")
    exec(compile(plat_src, "platform_probe", "exec"), sys.modules.setdefault(
        "platform_probe", types.ModuleType("platform_probe")).__dict__)

    async def _fake_recognize(url):
        return "图里是一件红裙子"

    img_mod = types.ModuleType("image2txt_probe")
    img_mod.recognize_image = _fake_recognize
    sys.modules["image2txt_probe"] = img_mod
    mgr_mod = types.ModuleType("manager_probe")
    mgr_mod.chat_manager = types.SimpleNamespace(is_image_recognition_enabled=lambda: True)
    sys.modules["manager_probe"] = mgr_mod

    ns = {"logger": nb.logger, "__name__": "wechat_probe"}
    exec(compile(src.replace("from nonebot import logger", ""), "wechat_probe", "exec"), ns)
    build = ns["build_channel_from_wechat"]

    # ① 图文 + 两张图
    bot = FakeBot()
    msg, ctx = await build(None, bot, FakeEvent(PAYLOAD, "这个照片发QQ上"))
    text = msg.text
    print("---- 模型实际会看到的内容 ----")
    print(text)
    print("------------------------------")
    check("图片内容进了上下文（识别结果）", "红裙子" in text)
    check("原图地址进了上下文（转发用）", IMG_URL in text)
    check("第二张图也在", "b.jpg" in text)
    check("缩略图没有被当成第二张", "t.jpg" not in text)
    check("只出现一次转发提示", text.count("send_file_to_my_platform") == 2,
          f"出现 {text.count('send_file_to_my_platform')} 次")

    # ② 引用
    msg2, _ = await build(None, bot, FakeEvent(PAYLOAD_QUOTE, "就这个"))
    print("---- 引用那条模型看到的 ----")
    print(msg2.text)
    check("引用内容进了上下文", "他之前说的那句话" in msg2.text)

    bad = [r for r in RESULTS if not r[1]]
    print()
    print(f"结论：{len(RESULTS) - len(bad)}/{len(RESULTS)} 项通过"
          + ("" if not bad else " ✗ 未通过：" + "；".join(r[0] for r in bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
