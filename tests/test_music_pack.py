# -*- coding: utf-8 -*-
"""网易云点歌能力包（上架插件商店的那个）守卫测试。

锁住：清单合法（kind/adapters/免费）、搜歌与卡片字段、发卡动作参数透传、
发卡失败会被动作回执纠正。以及：没有 media 权限的通道退化成"只给链接"。
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "src" / "qbotmanager" / "assets" / "tool_packs" / "music"
BRIDGE = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai" / "tools_bridge.py"
TOOL = ROOT / "src" / "qbotmanager" / "core" / "agent" / "tool.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SONG = _load(PACK / "tools" / "song_request.py", "music_song_request")


class _Ctx:
    def __init__(self, media=True):
        self.sent = []
        self.media = media

    def can(self, perm):
        return perm == "media" and self.media

    def send(self, action, params=None):
        self.sent.append((action, dict(params or {})))
        return {"ok": True, "queued": True, "action": action}


SEARCH = {"result": {"songs": [{"id": 1330348068, "name": "起风了",
                                "artists": [{"name": "买辣椒也用券"}]}]}}
DETAIL = {"songs": [{"id": 1330348068,
                     "album": {"picUrl": "https://p1.music.126.net/x.jpg"}}]}


def test_manifest_is_valid_tool_pack():
    data = json.loads((PACK / "manifest.json").read_text(encoding="utf-8"))
    assert data["id"] == "music" and data["kind"] == "tool-pack"
    # 第三方 QQ 和官方 QQ 都要能用；商店校验要求条目里含官方通道
    assert "qq" in data["adapters"] and "qq_official" in data["adapters"]
    assert data["tier"] == "free"
    assert [t["name"] for t in data["tools"]] == ["song_request"]


def test_pick_song_merges_detail_cover():
    song = SONG.pick_song(SEARCH, DETAIL)
    assert song["title"] == "起风了"
    assert song["artist"] == "买辣椒也用券"
    assert song["image"].endswith("?param=300y300")
    assert song["audio"].endswith("id=1330348068.mp3")
    assert song["link"].endswith("id=1330348068")
    assert SONG.pick_song(None) is None
    assert SONG.pick_song({"result": {"songs": []}}) is None


def test_handle_sends_music_card_action(monkeypatch):
    monkeypatch.setattr(SONG, "_get",
                        lambda url: SEARCH if "search" in url else DETAIL)
    ctx = _Ctx(media=True)
    out = json.loads(SONG.handle(ctx, {"keyword": "起风了"}))
    assert out["ok"] and out["title"] == "起风了"
    assert ctx.sent and ctx.sent[0][0] == "send_music_card"
    params = ctx.sent[0][1]
    assert params["audio"].endswith(".mp3") and params["image"] and params["url"]
    assert params["title"] == "起风了"


def test_handle_not_found_and_unreachable(monkeypatch):
    monkeypatch.setattr(SONG, "_get", lambda url: {"result": {"songs": []}})
    ctx = _Ctx()
    out = json.loads(SONG.handle(ctx, {"keyword": "不存在的歌"}))
    assert out["ok"] is False and out["error"] == "not_found" and not ctx.sent

    def boom(url):
        raise RuntimeError("network")

    monkeypatch.setattr(SONG, "_get", boom)
    out = json.loads(SONG.handle(ctx, {"keyword": "起风了"}))
    assert out["ok"] is False and out["error"] == "netease_unreachable"
    assert json.loads(SONG.handle(ctx, {}))["error"] == "missing_keyword"


def test_handle_without_media_returns_link_only(monkeypatch):
    monkeypatch.setattr(SONG, "_get",
                        lambda url: SEARCH if "search" in url else DETAIL)
    ctx = _Ctx(media=False)
    out = json.loads(SONG.handle(ctx, {"keyword": "起风了"}))
    assert out["ok"] and out["card_sent"] is False and out["link"]
    assert not ctx.sent


def test_bridge_passes_card_fields_through():
    src = BRIDGE.read_text(encoding="utf-8")
    body = src[src.index("async def _act_send_media"):src.index("async def _act_send_to")]
    assert "audio" in body and "image" in body and "send_card" in body, \
        "音乐卡片动作没把 audio/image 透传给通道（商店包会退化成文本）"


def test_music_card_counts_as_effect():
    src = TOOL.read_text(encoding="utf-8")
    seg = src[src.index("EFFECT_ACTIONS"):src.index("def merge_effect_report")]
    assert "send_music_card" in seg, "发卡失败不会被回执纠正（模型会误报已发送）"
