# -*- coding: utf-8 -*-
"""群消息里的引用 / 聊天记录 / 附件：解析成 AI 看得懂的文字。

老板 2026-10-01：群里发引用、聊天记录、图片时，AI 只拿到一句纯文本，
引用内容直接丢 —— 答案自然对不上。这里锁住解析结果和那个开关。
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, AI_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Author:
    def __init__(self, username=""):
        self.username = username
        self.id = "u-1"


class _Element:
    def __init__(self, content="", message_type=103, author=None):
        self.content = content
        self.message_type = message_type
        self.author = _Author(author) if author else None


class _Attachment:
    def __init__(self, content_type="image/png", url=""):
        self.content_type = content_type
        self.url = url


class _Event:
    def __init__(self, elements=None, attachments=None):
        self.msg_elements = elements
        self.attachments = attachments


def test_quote_and_chat_record():
    rc = _load("ai_rich_t1", "rich_content.py")
    text = rc.describe_elements([
        _Element("今晚八点开会", rc.TYPE_QUOTE, "小李"),
        _Element("你好\n在吗", rc.TYPE_CHAT_RECORD),
    ])
    assert "[对方引用了 小李 的消息] 今晚八点开会" in text, text
    assert "[聊天记录] 你好" in text
    assert rc.describe_elements(None) == ""
    assert rc.describe_elements([_Element("")]) == "", "空内容不占位"


def test_attachments_are_counted_in_plain_words():
    rc = _load("ai_rich_t2", "rich_content.py")
    assert rc.describe_attachments([_Attachment()]) == "[收到图片]", rc.describe_attachments([_Attachment()])
    assert rc.describe_attachments([_Attachment(), _Attachment()]) == "[收到2 个图片]"
    assert "语音" in rc.describe_attachments([_Attachment("voice/silk")])
    assert rc.describe_attachments([]) == ""
    assert rc.describe_attachments([_Attachment("", "a.PNG")]) == "[收到图片]"


def test_rich_context_combines_and_skips_empty():
    rc = _load("ai_rich_t3", "rich_content.py")
    both = rc.rich_context(_Event([_Element("原文", rc.TYPE_QUOTE)], [_Attachment()]))
    assert "原文" in both and "图片" in both
    assert rc.rich_context(_Event()) == ""
    assert rc.rich_context(_Event([_Element("")], [])) == ""


def test_switch_defaults_on_and_roundtrips():
    """客户端开关：默认开（老配置没有这个字段也当开）。"""
    from qbotmanager.core import ai_config
    from qbotmanager.core.settings import Settings

    tmp = Path(tempfile.mkdtemp(prefix="qbm_rich_"))
    s = Settings(tmp)
    s.ensure_dirs()
    assert ai_config.read_rich_parse_enabled(s) is True, "默认必须解析（不然 AI 看不到引用）"
    assert ai_config.save_rich_parse_enabled(s, False) is True
    assert ai_config.read_rich_parse_enabled(s) is False
    assert ai_config.save_rich_parse_enabled(s, True) is True
    assert ai_config.read_rich_parse_enabled(s) is True


# ---------------------------------------------------------------- 卡片（2026-10-03 加）

class _RichAttachment:
    def __init__(self, content_type, filename="", size=0, width=None, height=None, url=""):
        self.content_type = content_type
        self.filename = filename
        self.size = size
        self.width = width
        self.height = height
        self.url = url


def test_bilibili_card_is_parsed_with_title_and_link():
    """B站卡片（ark：#TITLE#/#DESC#/#TAG# + jump_url）要能翻成人话。"""
    import json

    rc = _load("ai_rich_card1", "rich_content.py")
    payload = json.dumps({"ark_data": {"kv": [
        {"key": "#TITLE#", "value": "【原神】4.2版本前瞻直播"},
        {"key": "#DESC#", "value": "B站视频 · 12:31"},
        {"key": "#TAG#", "value": "bilibili"},
        {"key": "#META#", "value": "UP主：某某"},
    ], "jump_url": "https://b23.tv/abcd"}}, ensure_ascii=False)
    text = rc.describe_card(payload)
    assert "B站" in text, text
    assert "【原神】4.2版本前瞻直播" in text, text
    assert "12:31" in text and "https://b23.tv/abcd" in text, text
    assert "UP主" in text, "ark 里其它 kv 也别丢"


def test_music_and_miniapp_cards():
    import json

    rc = _load("ai_rich_card2", "rich_content.py")
    music = json.dumps({"ark_data": {"kv": [
        {"key": "#MUSIC_TITLE#", "value": "夜曲"},
        {"key": "#SINGER#", "value": "周杰伦"},
        {"key": "#MUSIC_URL#", "value": "https://y.qq.com/n/ryqq/songDetail/x"}]}},
        ensure_ascii=False)
    text = rc.describe_card(music)
    assert "夜曲" in text and "周杰伦" in text, text
    assert "QQ音乐" in text, text

    app = json.dumps({"ark_data": {"kv": [{"key": "#TITLE#", "value": "某某小程序"}],
                                   "jump_url": "https://m.example.com/x"}},
                     ensure_ascii=False)
    assert "某某小程序" in rc.describe_card(app)


def test_empty_card_says_so_instead_of_making_things_up():
    import json

    rc = _load("ai_rich_card3", "rich_content.py")
    text = rc.describe_card(json.dumps({"ark_data": {}}, ensure_ascii=False))
    assert "没解析出来" in text and "fetch_text" in text, text


def test_plain_text_and_broken_json_are_not_cards():
    import json

    rc = _load("ai_rich_card4", "rich_content.py")
    assert rc.describe_card(json.dumps({"text": "在吗"}, ensure_ascii=False)) == ""
    assert rc.describe_card("在吗") == ""
    assert rc.describe_card("{坏掉的") == ""
    assert rc.describe_card(None) == ""


def test_video_and_file_attachments_are_honest_about_limits():
    """视频/文件/语音看不懂就说看不懂，别让 AI 假装看过。"""
    rc = _load("ai_rich_att2", "rich_content.py")
    video = rc.describe_attachments([_RichAttachment("video/mp4", "录屏.mp4", 3_355_443)])
    assert "录屏.mp4" in video and "3.2MB" in video, video
    assert "看不了画面" in video, video
    paper = rc.describe_attachments([_RichAttachment("file", "合同.pdf", 204800)])
    assert "合同.pdf" in paper and "200KB" in paper, paper
    voice = rc.describe_attachments([_RichAttachment("voice/silk", "", 0)])
    assert "听不了语音" in voice, voice
    img = rc.describe_attachments([_RichAttachment("image/png", "", 20480, 800, 600)])
    assert "800×600" in img, img


def test_rich_context_includes_card_from_content():
    rc = _load("ai_rich_ctx2", "rich_content.py")

    class _CardEvent:
        content = '{"ark_data": {"kv": [{"key": "#TITLE#", "value": "分享的视频"}], "jump_url": "https://b23.tv/x"}}'
        msg_elements = None
        attachments = None

    out = rc.rich_context(_CardEvent())
    assert "分享的视频" in out and "b23.tv" in out, out

if __name__ == "__main__":
    test_quote_and_chat_record()
    test_attachments_are_counted_in_plain_words()
    test_rich_context_combines_and_skips_empty()
    test_switch_defaults_on_and_roundtrips()
    test_bilibili_card_is_parsed_with_title_and_link()
    test_music_and_miniapp_cards()
    test_empty_card_says_so_instead_of_making_things_up()
    test_plain_text_and_broken_json_are_not_cards()
    test_video_and_file_attachments_are_honest_about_limits()
    test_rich_context_includes_card_from_content()
    print("OK rich content (cards)")
    print("OK rich content")
