# -*- coding: utf-8 -*-
"""iLink 纯图片 / 纯语音消息**不能被丢弃**（2026-10-15 修）。

现象（老板实测）：微信给机器人发一张**不带文字**的图片 → 机器人完全没反应，
连消息中心都没有记录。

根因在 adapter.py 的两处叠加：
1. `build_event` 只从「文字项(type1)」「语音转写(type3)」取文本，纯图片(type2)
   拿到的是空串；
2. `_on_message` 紧接着 `if not event.raw_message: return` —— **整条丢掉**，
   而且这一句**早于**消息落盘（messages.jsonl）与派发（bot.handle_event）。
于是 `ai/adapters/wechat.py` 里那套"从原始 payload 自捞图片地址 + 识图"的代码
**永远跑不到**。

修法：没有文本、但 `item_list` 里有内容时，回一句描述当正文。

跑法：
    .\\.build_venv_py314\\Scripts\\python.exe -m pytest tests/test_ilink_media_only.py -q
"""
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ILINK = (ROOT / "src" / "qbotmanager" / "assets" / "plugins"
         / "nonebot_adapter_ilink")
ADAPTER_SRC = ILINK / "adapter.py"

_MOD = None


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def adapter_mod():
    """按**真实源码**加载 adapter.py；nonebot 侧只做最小替身。

    被测的 `build_event` / `_describe_media` 一行没改。
    """
    global _MOD
    if _MOD is not None:
        return _MOD

    nb = types.ModuleType("nonebot")
    nb.logger = types.SimpleNamespace(info=lambda *a, **k: None,
                                      warning=lambda *a, **k: None)
    nb.get_plugin_config = lambda cfg: None
    sys.modules["nonebot"] = nb

    class _Kw:
        """能收任意 kwargs 的基类（真实的 Event 是 pydantic 模型）。"""
        def __init__(self, *a, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

        def __class_getitem__(cls, item):        # 支持 BaseMessage[T] 这种写法
            return cls

    adapters = types.ModuleType("nonebot.adapters")
    adapters.Event = type("Event", (_Kw,), {})
    adapters.Message = type("Message", (_Kw,), {})
    adapters.MessageSegment = type("MessageSegment", (_Kw,), {})
    adapters.Adapter = type("Adapter", (_Kw,), {})
    sys.modules["nonebot.adapters"] = adapters

    drivers = types.ModuleType("nonebot.drivers")
    drivers.Driver = object
    sys.modules["nonebot.drivers"] = drivers

    pkg = types.ModuleType("ilink_under_test")
    pkg.__path__ = [str(ILINK)]
    sys.modules["ilink_under_test"] = pkg

    _load("ilink_under_test.event", ILINK / "event.py")
    for name, attrs in (
            ("bot", {"Bot": object}),
            ("client", {"IlinkClient": object,
                        "SessionExpired": type("SessionExpired", (Exception,), {})}),
            ("config", {"Config": object})):
        m = types.ModuleType("ilink_under_test." + name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules["ilink_under_test." + name] = m

    _MOD = _load("ilink_under_test.adapter", ADAPTER_SRC)
    return _MOD


def _msg(**items):
    return {"item_list": list(items.values()), "from_user_id": "u1",
            "context_token": "tok", "message_id": "m1"}


# ================================================================ 核心回归
def test_pure_image_message_is_not_dropped(capsys):
    """纯图片消息必须留下来（正文非空），才可能被下游识图逻辑处理。"""
    mod = adapter_mod()
    data = _msg(a={"type": 2, "image_url": "http://example.com/a.jpg"})
    ev = mod.build_event(data, "self1")
    print("\n[纯图片] raw_message = %r" % ev.raw_message)
    assert ev.raw_message, "纯图片消息的正文绝不能是空串 —— 空串会在 _on_message 被整条丢掉"
    assert "图片" in ev.raw_message, "要说清对方发来的是图片"


def test_voice_without_transcript_is_not_dropped():
    """语音但服务端没给转写 → 同样不能丢。"""
    mod = adapter_mod()
    data = _msg(a={"type": 3, "voice_item": {}})
    ev = mod.build_event(data, "self1")
    print("\n[无转写语音] raw_message = %r" % ev.raw_message)
    assert ev.raw_message and "语音" in ev.raw_message


def test_unknown_item_type_still_tells_model_something():
    mod = adapter_mod()
    data = _msg(a={"type": 99, "whatever": 1})
    ev = mod.build_event(data, "self1")
    print("\n[未知类型] raw_message = %r" % ev.raw_message)
    assert ev.raw_message, "不认识的类型也要给一句描述，别让消息凭空消失"


def test_pure_image_with_text_still_prefers_text():
    """带文字时**正文必须是文字**，不要被描述盖掉（老行为不能回归）。"""
    mod = adapter_mod()
    data = _msg(a={"type": 1, "text_item": {"text": "这张图是啥"}},
                b={"type": 2, "image_url": "http://example.com/a.jpg"})
    ev = mod.build_event(data, "self1")
    print("\n[文字+图片] raw_message = %r" % ev.raw_message)
    assert ev.raw_message == "这张图是啥"


def test_voice_with_transcript_prefers_transcript():
    mod = adapter_mod()
    data = _msg(a={"type": 3, "voice_item": {"text": "说句话"}})
    ev = mod.build_event(data, "self1")
    assert ev.raw_message == "说句话"


def test_truly_empty_message_still_dropped():
    """真的什么都没有（item_list 为空）→ 仍然照旧丢弃，不要凭空造消息。"""
    mod = adapter_mod()
    data = _msg()
    ev = mod.build_event(data, "self1")
    print("\n[空消息] raw_message = %r" % ev.raw_message)
    assert ev.raw_message == "", "空消息不该被凭空补出内容"


def test_multiple_images_merge_into_one_line():
    mod = adapter_mod()
    data = _msg(a={"type": 2, "image_url": "http://e/1.jpg"},
                b={"type": 2, "image_url": "http://e/2.jpg"})
    ev = mod.build_event(data, "self1")
    print("\n[两张图] raw_message = %r" % ev.raw_message)
    assert ev.raw_message.count("一张图片") == 1, "同样的描述别重复堆叠"


# ================================================================ 源码级锁
def test_source_lock_media_fallback_still_wired():
    """源码锁：`build_event` 里"没文本时补描述"这条兜底不许被删掉。

    注意：`_on_message` 里那句 `if not event.raw_message: return` **保留是故意的**
    ——它负责挡掉真正空的消息（item_list 也是空的）。纯图片消息能活下来，
    靠的是 build_event 这一步先把正文补上，所以锁要锁在这里。
    """
    src = ADAPTER_SRC.read_text(encoding="utf-8")
    assert "def _describe_media(" in src, "缺少「没有文本但有条目」时的描述兜底函数"
    assert "text = _describe_media(data)" in src, \
        "build_event 里没有接上描述兜底 —— 纯图片消息会再次变成空正文被丢掉"
    assert "_MEDIA_LABELS" in src, "缺少 item 类型 → 人话 的映射表"
