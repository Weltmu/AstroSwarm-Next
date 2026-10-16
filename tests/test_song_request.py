# -*- coding: utf-8 -*-
"""网易云点歌（音乐卡片）行为测试。

锁住四件事（2026-10-12 老板点名要的点歌插件）：
1. 命令识别：`点歌 xxx` / `点一首 xxx`；只有"点歌"两个字不处理；
2. 数据规整：搜索接口 → 详情接口补封面；坏数据/没结果都不许炸；
3. 发送路径：QQ OneBot 发 [CQ:music,type=custom] 真卡片；老通道 TypeError → 三参降级；
   通道没有 send_card → 文本降级（诚实降级，不假装成功）。
4. 配文红线：卡片旁那句**一律走 AI**（chat.get_song_reply），提示词里明令禁止固定套话。
"""
import asyncio
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
BRAIN = AI_DIR / "brain.py"
CHAT = AI_DIR / "chat.py"
QQ = AI_DIR / "adapters" / "qq.py"
SONG_SRC = AI_DIR / "song.py"


def _load_song():
    spec = importlib.util.spec_from_file_location("song_under_test", SONG_SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SONG = _load_song()

SEARCH = {"result": {"songs": [{
    "id": 1330348068,
    "name": "起风了",
    "artists": [{"name": "买辣椒也用券"}, {"name": "周深"}],
}]}}
DETAIL = {"songs": [{"id": 1330348068,
                     "album": {"picUrl": "https://p2.music.126.net/cover.jpg"}}]}


class _Msg:
    def __init__(self, text, is_at_me=False, at_others=False):
        self.plain_text = text
        self.text = text
        self.is_at_me = is_at_me
        self.user_id = "990000003"
        self.extra = {"at_others": True} if at_others else {}


class _Ctx:
    """假通道：记录卡片与文本；card_mode 模拟三种通道能力。"""

    def __init__(self, card_mode="full"):
        self.cards = []
        self.texts = []
        self.card_mode = card_mode

    async def send_card(self, url, title="", singer="", song_id="", image="", audio=""):
        if self.card_mode == "legacy" and (song_id or image or audio):
            raise TypeError("legacy send_card(target, title, singer)")
        self.cards.append((url, title, singer, song_id, image, audio))
        return True

    async def send_text(self, text):
        self.texts.append(text)


def _fake_search(song):
    async def _inner(keyword):
        return dict(song) if song else None
    return _inner


def _fake_line(reply):
    calls = []

    async def _inner(title, artist, found):
        calls.append((title, artist, found))
        return reply
    return _inner, calls


def test_parse_command_branches():
    assert SONG.parse_command("点歌 起风了") == "起风了"
    assert SONG.parse_command("点一首：晴天") == "晴天"
    assert SONG.parse_command(" 点歌   起风了 ") == "起风了"
    assert SONG.parse_command("点歌") is None
    assert SONG.parse_command("点歌：") is None
    assert SONG.parse_command("你会点歌吗") is None
    assert SONG.parse_command("签到") is None
    assert SONG.parse_command("") is None
    assert len(SONG.parse_command("点歌 " + "长" * 80)) == 50


def test_pick_song_normalizes_data():
    song = SONG.pick_song(SEARCH)
    assert song["id"] == "1330348068"
    assert song["name"] == "起风了"
    assert song["artist"] == "买辣椒也用券、周深"
    assert song["url"].endswith("id=1330348068")
    assert song["audio"].endswith("id=1330348068.mp3")
    assert song["cover"] == ""  # 搜索接口这版不带封面
    song2 = SONG.pick_song(SEARCH, DETAIL)
    # 封面会带 300y300 缩放参数（原图 5~7 MB，QQ 拉不动）
    assert song2["cover"] == "https://p2.music.126.net/cover.jpg?param=300y300"


def test_pick_song_never_raises_on_garbage():
    assert SONG.pick_song(None) is None
    assert SONG.pick_song({}) is None
    assert SONG.pick_song({"result": {"songs": []}}) is None
    assert SONG.pick_song({"result": {"songs": [{"name": "无 id"}]}}) is None
    assert SONG.pick_song("垃圾数据") is None
    song = SONG.pick_song({"result": {"songs": [{"id": 1, "name": "x"}]}})
    assert song["artist"] == "未知歌手"


def test_card_kwargs():
    song = SONG.pick_song(SEARCH, DETAIL)
    kw = SONG.card_kwargs(song)
    assert kw["song_id"] == "1330348068"
    assert kw["image"].startswith("https://p2.music.126.net/cover.jpg")
    assert kw["audio"].endswith(".mp3")


def test_try_handle_sends_card_and_ai_line(monkeypatch):
    msg, ctx = _Msg("点歌 起风了"), _Ctx("full")
    monkeypatch.setattr(SONG, "search_song", _fake_search(SONG.pick_song(SEARCH, DETAIL)))
    line_fn, calls = _fake_line("放这首吧，别emo")
    monkeypatch.setattr(SONG, "_ai_line", line_fn)
    assert asyncio.run(SONG.try_handle(msg, ctx)) is True
    assert len(ctx.cards) == 1
    url, title, singer, song_id, image, audio = ctx.cards[0]
    assert title == "起风了" and singer == "买辣椒也用券、周深"
    assert "cover.jpg" in image and audio.endswith(".mp3")
    assert calls == [("起风了", "买辣椒也用券、周深", True)]
    assert ctx.texts == ["放这首吧，别emo"]


def test_try_handle_at_others_does_not_grab(monkeypatch):
    msg, ctx = _Msg("点歌 起风了", at_others=True), _Ctx("full")
    monkeypatch.setattr(SONG, "search_song", _fake_search(SONG.pick_song(SEARCH, DETAIL)))
    assert asyncio.run(SONG.try_handle(msg, ctx)) is False
    assert ctx.cards == [] and ctx.texts == []


def test_try_handle_not_found_goes_to_ai(monkeypatch):
    msg, ctx = _Msg("点歌 不存在的歌"), _Ctx("full")
    monkeypatch.setattr(SONG, "search_song", _fake_search(None))
    line_fn, calls = _fake_line("名字记错了吧？")
    monkeypatch.setattr(SONG, "_ai_line", line_fn)
    assert asyncio.run(SONG.try_handle(msg, ctx)) is True
    assert ctx.cards == []
    assert calls == [("不存在的歌", "", False)]
    assert ctx.texts == ["名字记错了吧？"]


def test_try_handle_legacy_channel_falls_back(monkeypatch):
    """官方 QQ / 微信这类老 send_card 只认三参：TypeError → 原样三参调用，绝不炸。"""
    msg, ctx = _Msg("点歌 起风了"), _Ctx("legacy")
    monkeypatch.setattr(SONG, "search_song", _fake_search(SONG.pick_song(SEARCH, DETAIL)))
    line_fn, _ = _fake_line("")
    monkeypatch.setattr(SONG, "_ai_line", line_fn)
    assert asyncio.run(SONG.try_handle(msg, ctx)) is True
    assert len(ctx.cards) == 1
    assert ctx.cards[0][3:] == ("", "", "")  # 三参调用


def test_try_handle_no_card_channel_degrades_to_text(monkeypatch):
    msg, ctx = _Msg("点歌 起风了"), _Ctx("full")
    ctx.send_card = None
    monkeypatch.setattr(SONG, "search_song", _fake_search(SONG.pick_song(SEARCH, DETAIL)))
    line_fn, _ = _fake_line("")
    monkeypatch.setattr(SONG, "_ai_line", line_fn)
    assert asyncio.run(SONG.try_handle(msg, ctx)) is True
    assert any("起风了" in t and "music.163.com" in t for t in ctx.texts)


def test_wiring_brain_intercepts_song():
    src = BRAIN.read_text(encoding="utf-8")
    assert "song_mod.try_handle" in src, "brain 没有接线点歌，插件等于不存在"
    assert "点歌处理异常（继续正常对话）" in src, "点歌异常没有兜底日志"


def test_wiring_qq_adapter_uses_real_music_card():
    src = QQ.read_text(encoding="utf-8")
    assert '"music"' in src and '"custom"' in src and '"audio"' in src, \
        "send_card 没拼 [CQ:music,type=custom] —— NapCat 只有 custom 全字段才签成音乐卡"
    body = src[src.index("async def send_card"):src.index("async def send_to_target")]
    assert "image and audio" in body, "缺字段时没有走文本降级"


def test_wiring_chat_prompt_forbids_canned_song_text():
    src = CHAT.read_text(encoding="utf-8")
    body = src[src.index("async def get_song_reply"):
               src.index("async def get_interject_reply")]
    assert "固定套话" in body, "点歌配文提示词没禁固定套话"
    assert "不要把歌名歌手再复述一遍" in body
    assert "不用词库兜底" in body, "AI 出不来时必须只发卡片，不许退化成词库套话"


def test_song_module_imports_chat_lazily():
    src = SONG_SRC.read_text(encoding="utf-8")
    assert "from .chat import get_song_reply" in src
    assert "固定话术" in src  # 文件头把红线写清楚
    top = [ln for ln in src.splitlines()
           if ln.startswith("import ") or ln.startswith("from ")]
    assert not any("httpx" in ln for ln in top), "httpx 必须懒导入（单测按路径加载不拖依赖）"
