# -*- coding: utf-8 -*-
r"""验证识图三级降级（离线）：
  ① 主模型能收图 → 直接把图给它；
  ② 主模型走不了 → 用备用视觉模型转文字；
  ③ 备用没配/也失败 → 提示词里如实说"看不到"，不许装作看见。
"""
import asyncio
import importlib.util
import sys
import types
from pathlib import Path

# 2026-10-11：路径不再写死 Windows（Linux 上原来直接 FileNotFoundError）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _src import plugin_dir  # noqa: E402

SRC = plugin_dir("liqinghan")
print(f"插件目录：{SRC}")
for name in ("nonebot", "nonebot.adapters", "nonebot.adapters.qq"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["nonebot"].logger = types.SimpleNamespace(
    info=lambda *a, **k: None, warning=lambda *a, **k: None,
    error=lambda *a, **k: None, exception=lambda *a, **k: None)

pkg = types.ModuleType("lqpkg")
pkg.__path__ = [str(SRC)]
sys.modules["lqpkg"] = pkg
spec = importlib.util.spec_from_file_location("lqpkg.agent", SRC / "agent.py")
agent_mod = importlib.util.module_from_spec(spec)
sys.modules["lqpkg.agent"] = agent_mod
spec.loader.exec_module(agent_mod)

Brain = importlib.util.spec_from_file_location("lqpkg.brain", SRC / "brain.py")
brain_mod = importlib.util.module_from_spec(Brain)
sys.modules["lqpkg.brain"] = brain_mod
Brain.loader.exec_module(brain_mod)

Agent = agent_mod.Agent
bad = []


def ok(label, cond, detail=""):
    print(("✓ " if cond else "× ") + label + (f" —— {detail}" if detail else ""))
    if not cond:
        bad.append(label)


class Stub(Agent):
    def __init__(self, urls, desc):
        self._urls = urls
        self._desc = desc

    async def _image_data_urls(self, ctx, images):
        return list(self._urls)

    async def _describe_images(self, ctx, images):
        return self._desc


IMGS = [{"file": "x.jpg"}]
BASE = "用户说：看看这张图"

print("=== ① 主模型能收图 ===")
s1 = Stub(["data:image/jpeg;base64,AAAA"], "备用模型的描述（不该被用）")
prompt1, urls1 = asyncio.run(s1._vision_context(None, IMGS, BASE))
ok("提示词保持原样", prompt1 == BASE, prompt1[:40])
ok("图片交给主模型", urls1 == ["data:image/jpeg;base64,AAAA"], str(urls1)[:40])

print("=== ② 主模型走不了 → 备用视觉模型 ===")
s2 = Stub([], "一只橘猫趴在地上")
prompt2, urls2 = asyncio.run(s2._vision_context(None, IMGS, BASE))
ok("没有图给主模型", urls2 == [])
ok("描述塞进了提示词", "一只橘猫趴在地上" in prompt2, prompt2[-40:])

print("=== ③ 备用也没配/失败 → 如实说看不见 ===")
s3 = Stub([], "")
prompt3, urls3 = asyncio.run(s3._vision_context(None, IMGS, BASE))
ok("没有图给主模型", urls3 == [])
ok("明确要求如实说看不到", "看不到" in prompt3 and "别装作看见" in prompt3, prompt3[-50:])

print("=== 没有图片时不该乱加东西 ===")
s4 = Stub([], "不该被调用")
prompt4, urls4 = asyncio.run(s4._vision_context(None, [], BASE))
ok("无图：提示词不变、无图分片", prompt4 == BASE and urls4 == [])

print("=== brain 拒图要抛 ImageRejected（不再偷偷降级）===")
ok("ImageRejected 存在", hasattr(brain_mod, "ImageRejected"))
ok("异常里带状态码", "HTTP" in str(brain_mod.ImageRejected(400, "bad")))

print()
if bad:
    print("失败:", bad)
    sys.exit(1)
print("识图三级降级：全部通过 ✓")
