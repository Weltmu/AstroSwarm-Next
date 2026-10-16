# -*- coding: utf-8 -*-
r"""验证"图片直发主模型"的组装逻辑（离线，不真调 API）。

① `_user_content`：有图 → 多模态分片（图在前、文字在后）；没图 → 还是纯字符串
② `think()`：发给接口的 messages 里确实带上了 image_url 分片
③ `_has_image`：能识别带图消息（4xx 退回纯文本这条路径靠它判断）
"""
import asyncio
import importlib.util
import sys
from pathlib import Path

# 2026-10-11：路径不再写死 Windows（Linux 上原来直接 FileNotFoundError）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _src import plugin_dir  # noqa: E402

SRC = plugin_dir("liqinghan")
print(f"插件目录：{SRC}")
spec = importlib.util.spec_from_file_location("lq_brain", SRC / "brain.py")
brain_mod = importlib.util.module_from_spec(spec)
sys.modules["lq_brain"] = brain_mod
spec.loader.exec_module(brain_mod)

Brain = brain_mod.Brain
bad = []


def ok(label, cond, detail=""):
    print(("✓ " if cond else "× ") + label + (f" —— {detail}" if detail else ""))
    if not cond:
        bad.append(label)


IMG = "data:image/png;base64,AAAABBBB"

# ① _user_content
c1 = Brain._user_content("你好")
ok("没图时 content 还是纯字符串", isinstance(c1, str) and c1 == "你好")
c2 = Brain._user_content("看看这张图", [IMG])
ok("有图时 content 是分片列表", isinstance(c2, list) and len(c2) == 2, str(c2)[:80])
ok("图片分片是 image_url（detail=high）",
   c2[0]["type"] == "image_url" and c2[0]["image_url"]["detail"] == "high")
ok("文字分片排在后面", c2[1]["type"] == "text" and c2[1]["text"] == "看看这张图")

# ② think 组装 + ③ _has_image
sent = {}


class FakeCfg:
    ds_api_key = "k"
    ds_model = "deepseek-v4-pro"
    ds_api_url = "https://api.deepseek.com"
    ds_max_tokens = 100


b = Brain(FakeCfg())


async def fake_chat_once(messages, tools):
    sent["messages"] = messages
    return {"role": "assistant", "content": "看到了，是一只猫"}


b._chat_once = fake_chat_once            # 拦掉真实请求
out = asyncio.run(b.think("你是李清菡", [{"role": "user", "text": "在吗"}], "帮我看看图",
                          [], lambda n, a: "ok", images=[IMG]))
ok("think 返回了回复", out == "看到了，是一只猫", out)
msgs = sent.get("messages") or []
user_msg = [m for m in msgs if m.get("role") == "user"][-1]
ok("最后一条 user 消息带 image_url 分片",
   isinstance(user_msg.get("content"), list)
   and any(p.get("type") == "image_url" for p in user_msg["content"]))
ok("_has_image 认得出带图消息", Brain._has_image(msgs))
ok("_has_image 对纯文本返回 False",
   not Brain._has_image([{"role": "user", "content": "纯文字"}]))

# 不带图时不产生分片
sent.clear()
asyncio.run(b.think("sys", [], "纯文本", [], lambda n, a: "ok"))
u2 = [m for m in sent["messages"] if m.get("role") == "user"][-1]
ok("不带图时仍是纯文本", isinstance(u2.get("content"), str))

print()
if bad:
    print("失败:", bad)
    sys.exit(1)
print("图片直发主模型：组装逻辑全部通过 ✓")
