"""网易云点歌工具：搜歌 → 交主程序发音乐卡片（只回结构化结果，话由 agent 说）。

为什么要做成能力包（2026-10-12 上架插件商店）：
- 内置聊天对「点歌 歌名」有直通车；但「放首歌 / 想听××」这类自然语言
  要靠模型自己挑工具，这个包把"搜歌 + 发卡片"补成模型可调用的能力；
- 用户也能从插件商店单独安装它（免费）。

卡片由主程序动作 ``send_music_card`` 发送（参数整包透传）：
- QQ（OneBot/NapCat）：真·音乐卡片，能点开直接播放；
- QQ 官方：通道自己降级成"歌名 + 链接"文本；
- 发不了卡片/没有 media 权限的通道：只返回数据，模型把 link 直接发出去。

联网走网易云公开接口（搜索 → 详情补封面），任何一步失败都如实返回结构化错误，
不写死任何中文话术。
"""
import json
from urllib.parse import quote

import httpx

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://music.163.com/",
}
_SEARCH_API = ("https://music.163.com/api/search/get/web"
               "?csrf_token=&type=1&offset=0&total=true&limit=5&s={kw}")
_DETAIL_API = "https://music.163.com/api/song/detail/?ids=[{ids}]"
_SONG_PAGE = "https://music.163.com/song?id={id}"
_AUDIO = "https://music.163.com/song/media/outer/url?id={id}.mp3"


def _get(url):
    """GET 一个 JSON 接口（单测里会被替换掉）。"""
    with httpx.Client(timeout=10, headers=_HEADERS, follow_redirects=True) as cli:
        resp = cli.get(url)
        resp.raise_for_status()
        return resp.json()


def pick_song(search_data, detail_data=None):
    """把网易云返回规整成卡片字段；没搜到/数据坏了返回 None（绝不抛异常）。"""
    try:
        songs = ((search_data or {}).get("result") or {}).get("songs") or []
        if not songs:
            return None
        first = songs[0] or {}
        song_id = first.get("id")
        if not song_id:
            return None
        title = str(first.get("name") or "").strip() or "未知歌曲"
        names = [str((a or {}).get("name") or "").strip()
                 for a in (first.get("artists") or [])]
        artist = "、".join(n for n in names if n) or "未知歌手"
        cover = str(((first.get("album") or {}).get("picUrl")) or "").strip()
        if not cover:
            for s in ((detail_data or {}).get("songs") or []):
                cover = str(((s or {}).get("album") or {}).get("picUrl") or "").strip()
                if cover:
                    break
        if cover:
            # 原图 5~7 MB，QQ 客户端拉封面会卡：带 300y300 缩放（约 100 KB）。
            cover += ("&" if "?" in cover else "?") + "param=300y300"
        return {
            "id": str(song_id),
            "title": title,
            "artist": artist,
            "image": cover,
            "link": _SONG_PAGE.format(id=song_id),
            "audio": _AUDIO.format(id=song_id),
        }
    except Exception:  # noqa: BLE001 —— 接口字段变脸也不能把点歌搞崩
        return None


def handle(ctx, args):
    keyword = str(args.get("keyword") or args.get("song") or
                  args.get("name") or "").strip()[:50]
    if not keyword:
        return json.dumps({"ok": False, "error": "missing_keyword"},
                          ensure_ascii=False)
    try:
        search = _get(_SEARCH_API.format(kw=quote(keyword)))
        song = pick_song(search)
        if song and not song.get("image"):
            try:
                detail = _get(_DETAIL_API.format(ids=song["id"]))
                song = pick_song(search, detail) or song
            except Exception:  # noqa: BLE001 —— 没封面不影响发卡
                pass
    except Exception as exc:  # noqa: BLE001 —— 网络层失败如实上报
        return json.dumps({"ok": False, "error": "netease_unreachable",
                           "detail": str(exc)[:120]}, ensure_ascii=False)
    if not song:
        return json.dumps({"ok": False, "error": "not_found", "keyword": keyword},
                          ensure_ascii=False)

    try:
        can_card = bool(ctx.can("media"))
    except Exception:  # noqa: BLE001 —— 老上下文没有 can 就当发不了卡
        can_card = False
    if can_card:
        ctx.send("send_music_card", {
            "url": song["link"],
            "title": song["title"],
            "singer": song["artist"],
            "image": song["image"],
            "audio": song["audio"],
        })
    return json.dumps({
        "ok": True,
        "title": song["title"],
        "artist": song["artist"],
        "link": song["link"],
        "card_sent": can_card,
        "note": ("音乐卡片已交给通道发送；要是最终回执说失败，就把 link 原样发给他。"
                 if can_card else
                 "这条通道发不了音乐卡：用你自己的话回应，并把 link 一起发给他。"),
    }, ensure_ascii=False)
