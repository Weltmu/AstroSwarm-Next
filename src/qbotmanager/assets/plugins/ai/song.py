# -*- coding: utf-8 -*-
"""网易云点歌（音乐卡片版）—— 卡片由通道发，配文一律交给 AI。

背景（2026-10-12 老板要求）：群友在群里/私聊发「点歌 歌名」，机器人要回一张
**QQ 里能点的音乐卡片**（网易云），不是"歌名 + 链接"两行字。

三层各管各的事，任一层失败都不影响正常聊天：
1. 识别：`点歌 xxx` / `点一首 xxx`（私聊、群里都认；@ 了别人则不抢话）；
2. 查歌：网易云公开搜索接口 → 详情接口补封面 → 规整出 id/歌名/歌手/封面/外链；
3. 发卡：交给通道 `send_card`。OneBot(NapCat) 走 `[CQ:music,type=custom,...]`
   （NapCat 再经签名服务转成真 QQ 音乐卡）；通道不支持就诚实降级成文本。

**配文红线**：卡片旁边那句话一律调 AI 按人设现编（`chat.get_song_reply`），
任何时候都不允许退化成"好的，为你点了一首歌"这种固定话术；AI 出不来就只发卡片。

模块顶部只 import 标准库（re/urllib），httpx / nonebot / chat 都在用到处懒导入 ——
这样单测可以按路径加载它，不拖 nonebot 一套依赖。
"""
import re
from urllib.parse import quote

__all__ = ["parse_command", "pick_song", "search_song", "card_kwargs", "try_handle"]

_CMD_RE = re.compile(r"^点歌[\s:：,，]*(\S.*)$")
_ALIAS_RE = re.compile(r"^点一首[\s:：,，]*(\S.*)$")
_KEYWORD_MAX = 50

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://music.163.com/",
}
_SEARCH_API = ("https://music.163.com/api/search/get/web"
               "?csrf_token=&type=1&offset=0&total=true&limit=5&s={kw}")
_DETAIL_API = "https://music.163.com/api/song/detail/?ids=[{ids}]"
_SONG_PAGE = "https://music.163.com/song?id={id}"
_AUDIO = "https://music.163.com/song/media/outer/url?id={id}.mp3"


def _log(text: str) -> None:
    """日志只在真的要用时导入 nonebot（单测环境没有它）。"""
    try:
        from nonebot import logger

        logger.info(text)
    except Exception:  # noqa: BLE001
        pass


def parse_command(text):
    """认「点歌」命令 → 返回搜索关键词；不是点歌（或只有"点歌"两个字）返回 None。"""
    t = (text or "").strip()
    if not t:
        return None
    m = _CMD_RE.match(t) or _ALIAS_RE.match(t)
    if not m:
        return None
    # 正则的回溯会把"点歌："里的冒号吐回捕获组 —— 这里再剥一次前导分隔符，
    # 否则"点歌："会被当成一首叫"："的歌。
    kw = m.group(1).strip().lstrip(":：,，").strip()
    kw = kw[:_KEYWORD_MAX].strip()
    return kw or None


def pick_song(search_data, detail_data=None):
    """把网易云接口返回规整成卡片字段；没搜到/数据坏了返回 None（绝不抛异常）。"""
    try:
        songs = ((search_data or {}).get("result") or {}).get("songs") or []
        if not songs:
            return None
        first = songs[0] or {}
        song_id = first.get("id")
        if not song_id:
            return None
        name = str(first.get("name") or "").strip() or "未知歌曲"
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
            # 网易云原图动不动 5~7 MB：QQ 客户端拉封面会卡半天。带上缩放参数
            # （实测 300y300 → 约 100 KB），卡片秒开。
            cover += ("&" if "?" in cover else "?") + "param=300y300"
        return {
            "id": str(song_id),
            "name": name,
            "artist": artist,
            "cover": cover,
            "url": _SONG_PAGE.format(id=song_id),
            "audio": _AUDIO.format(id=song_id),
        }
    except Exception:  # noqa: BLE001 —— 接口字段变脸也不能把点歌搞崩
        return None


async def _fetch_json(url: str):
    import httpx  # 懒导入：启动时省时间，只有真点歌才需要

    async with httpx.AsyncClient(timeout=10.0, headers=_HEADERS,
                                 follow_redirects=True) as cli:
        resp = await cli.get(url)
        resp.raise_for_status()
        return resp.json()


async def search_song(keyword: str):
    """搜索一首歌；搜索接口不带封面时，再查一次详情补上。搜不到返回 None。"""
    data = await _fetch_json(_SEARCH_API.format(kw=quote(keyword)))
    song = pick_song(data)
    if song and not song["cover"]:
        try:
            detail = await _fetch_json(_DETAIL_API.format(ids=song["id"]))
            song = pick_song(data, detail) or song
        except Exception as e:  # noqa: BLE001 —— 没封面不影响发卡（QQ 也接受空图）
            _log(f"[song] 补封面失败（不影响点歌）：{e}")
    return song


def card_kwargs(song: dict) -> dict:
    """给通道 send_card 的扩展参数（OneBot 用来拼 [CQ:music,type=custom]）。"""
    return {"song_id": song.get("id") or "",
            "image": song.get("cover") or "",
            "audio": song.get("audio") or ""}


async def _ai_line(title: str, artist: str, found: bool) -> str:
    """卡片配文：一律 AI 现编（死文本是红线）；生成不出来就空串（宁可不发）。"""
    try:
        from .chat import get_song_reply

        return (await get_song_reply(title, artist, found=found)) or ""
    except Exception as e:  # noqa: BLE001
        _log(f"[song] AI 配文生成失败（本次不配文）：{e}")
        return ""


async def _send_card(ctx, song: dict) -> bool:
    """发卡片；通道没有 / 参数对不上 / 发送失败 → 诚实降级成文字（返回是否发了真卡）。"""
    sender = getattr(ctx, "send_card", None)
    if sender is None:
        await ctx.send_text(f"{song['name']} — {song['artist']}\n{song['url']}")
        return False
    try:
        return bool(await sender(song["url"], song["name"], song["artist"],
                                 **card_kwargs(song)))
    except TypeError:
        # 老通道（官方 QQ / 微信…）的 send_card 只认三个参数：它们自己会降级
        try:
            return bool(await sender(song["url"], song["name"], song["artist"]))
        except Exception as e:  # noqa: BLE001
            _log(f"[song] 发卡片失败：{e}")
    except Exception as e:  # noqa: BLE001
        _log(f"[song] 发卡片失败：{e}")
    try:
        await ctx.send_text(f"{song['name']} — {song['artist']}\n{song['url']}")
    except Exception as e:  # noqa: BLE001
        _log(f"[song] 降级文字也发不出去：{e}")
    return False


async def try_handle(msg, ctx) -> bool:
    """点歌主流程：命中返回 True（处理完毕），不命中返回 False（继续走正常聊天）。"""
    text = (getattr(msg, "plain_text", "") or getattr(msg, "text", "") or "")
    keyword = parse_command(text)
    if not keyword:
        return False
    extra = getattr(msg, "extra", None) or {}
    if extra.get("at_others") and not getattr(msg, "is_at_me", False):
        # 与正常聊天同规矩：@ 了别人（又没 @ 我）不抢话
        return False

    try:
        song = await search_song(keyword)
    except Exception as e:  # noqa: BLE001 —— 搜歌炸了当"没搜到"，不要让点歌连累聊天
        _log(f"[song] 搜索失败（按没搜到处理）：{e}")
        song = None

    if song:
        ok = await _send_card(ctx, song)
        _log("[song] 点歌命中：%r → 《%s》- %s（%s）"
             % (keyword, song["name"], song["artist"],
                "已发卡片" if ok else "已降级/发送失败"))
        line = await _ai_line(song["name"], song["artist"], True)
    else:
        _log(f"[song] 没搜到：{keyword!r} → 交给 AI 顺口回一句")
        line = await _ai_line(keyword, "", False)

    if line:
        try:
            await ctx.send_text(line)
        except Exception as e:  # noqa: BLE001 —— 配文发不出去不影响卡片
            _log(f"[song] 配文发送失败：{e}")
    return True
