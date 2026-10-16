# -*- coding: utf-8 -*-
"""群聊策略：这个群能不能说话 + 这条消息是不是 @ 了机器人。

单独放一个**不依赖 nonebot** 的文件，原因有两个：
1. 插件本体跑在 bot venv（有 nonebot），构建环境的测试没有它 —— 纯逻辑单独放，
   测试可以直接按路径加载，不用把 nonebot 也搬进构建环境；
2. 这两条规则最近都踩过坑（群里一律不回、全量事件认不出 @），集中在一处好读好测。
"""


def group_allowed(data: dict, group_id: str) -> bool:
    """这个群能不能回话。

    默认**所有群都允许**（群里被 @ 就回）：`group_chat_enabled` 缺省为 True。
    以前 `enabled_groups` 默认为空就等于"一个群都不回"，界面上又没有加群入口，
    用户装完在群里怎么 @ 都没反应。现在 `enabled_groups` 留空 = 不限群；
    它非空时才当白名单用（只想听几个群就往里填）。
    """
    if not bool((data or {}).get("group_chat_enabled", True)):
        return False
    groups = (data or {}).get("enabled_groups") or []
    return (not groups) or (str(group_id) in {str(g) for g in groups})


def _bot_ids(event, bot_id="") -> set:
    """机器人自己的 id 集合（用于按 id 比对 mention）。

    `bot_id` 可以是**一个** id，也可以是一串候选（不同通道把机器人自己的 id
    放在不同字段上：飞书的 BotInfo 是 `open_id`、QQ 官方是 `id`）。2026-10-15：
    飞书适配器以前只读 `self_info.id` —— 那个字段在飞书那边**不存在**（叫 open_id），
    取到空串 → `ids` 为空 → 群里 @ 机器人认不出来、还因为 `mentions_other` 把
    自己的 @ 当成"@ 别人"而彻底不接话。
    """
    ids = set()
    cands = bot_id if isinstance(bot_id, (list, tuple, set, frozenset)) else (bot_id,)
    for cand in tuple(cands) + (getattr(event, "self_id", ""),):
        s = str(cand or "").strip()
        if s:
            ids.add(s)
    return ids


def is_at_me(event, bot_id: str = "") -> bool:
    """这条消息 @ 的是不是机器人自己。

    @-专用事件（GROUP_AT_MESSAGE_CREATE）里 `to_me` 直接是 True；
    但机器人开了"接收所有消息"后，群里**每条**消息都走全量事件
    （GROUP_MESSAGE_CREATE），它的 `to_me` 恒为 False —— 这时只能看
    mention 列表里哪个是机器人自己。

    2026-10-10 修：只认 `is_bot` / `is_you` 标记会漏 —— QQ 官方的全量事件里
    mention 段不一定带这两个标记（适配器的"改写成 self_info.id"那步也就没触发），
    结果群里 @ 了它也认不出来（老板遇到的"又不说话了"就是这个）。
    现在多一条：mention 的 user_id 等于**机器人自己的 id**（或事件 self_id）也算。
    """
    if bool(getattr(event, "to_me", False)):
        return True
    ids = _bot_ids(event, bot_id)
    for seg_type, data in message_segments(event):
        if seg_type not in ("mention_user", "at"):
            continue
        if data.get("is_bot") or data.get("is_you"):
            return True
        if ids and str(data.get("user_id") or data.get("qq") or "").strip() in ids:
            return True
    try:
        for mention in (getattr(event, "mentions", None) or []):
            if bool(getattr(mention, "is_you", False)) or bool(getattr(mention, "bot", False)):
                return True
            mid = ""
            for attr in ("id", "user_id", "user_openid", "openid"):
                v = getattr(mention, attr, None)
                if v:
                    mid = str(v).strip()
                    break
            if mid and ids and mid in ids:
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _seg_type_data(seg) -> tuple:
    """一个消息段的 (type, data)；段是 dict 还是对象都认（协议端两种都出现过）。"""
    if isinstance(seg, dict):
        data = seg.get("data")
        return str(seg.get("type") or ""), (data if isinstance(data, dict) else seg)
    if isinstance(seg, (tuple, list)) and len(seg) == 2:
        # CQ 串兜底路径（_parse_cq_codes）产出的就是 (type, data) 二元组 ——
        # 以前这里不认二元组，整条兜底等于没生效（@/引用段都认不出来）。
        t, d = seg
        return str(t or ""), (d if isinstance(d, dict) else {})
    data = getattr(seg, "data", None)
    if data is None:
        return str(getattr(seg, "type", "") or ""), {}
    if isinstance(data, dict):
        return str(getattr(seg, "type", "") or ""), data
    try:
        return str(getattr(seg, "type", "") or ""), dict(data)
    except Exception:  # noqa: BLE001
        return str(getattr(seg, "type", "") or ""), {}


def message_segments(event) -> list:
    """这条事件的消息段列表（**统一归一化成 (type, data) 二元组**）。

    2026-10-11 晚 修（老板报的"引用了却说没引用"）：线上看到
    `[reply:id=388291130][at:qq=6885483] …`，插件却打了"无 reply 段"。
    段是对象还是 dict、引用 id 的字段叫 `id` 还是 `message_id`，这里一律抹平 ——
    引用判定、@ 判定都走这一份，别再各写一套。
    """
    segs = []
    raw = None
    getter = getattr(event, "get_message", None)
    if callable(getter):
        try:
            raw = getter()
        except Exception:  # noqa: BLE001 —— 拿不到消息体就当没有段
            raw = None
    if raw is None:
        # 兜底：事件只有 `.message` 字段的形态（OneBot 事件两个字段都在）
        raw = getattr(event, "message", None)
    if isinstance(raw, str):
        raw = _parse_cq_codes(raw)
    try:
        iterator = list(raw or [])
    except TypeError:
        return segs
    for seg in iterator:
        t, d = _seg_type_data(seg)
        if t:
            segs.append((t, d))
    return segs


def _parse_cq_codes(text: str) -> list:
    """从原始串里抠出 **CQ 码本身**（只在段列表缺失时兜底）。

    与 OneBot 适配器的 `Message._construct` 不同：那边把 CQ 码**展开成段**；
    没展开的（例如事件里塞的是渲染文本 `[reply:id=1]`）在这里还能认出来。
    两种形态都认：
      * CQ 码：`[CQ:reply,id=123]`；
      * 渲染串：`[reply:id=123]`（线上日志里看到的就是这种，H-25）。
    大小写、全角括号都容忍（H-29 提过的边角形态）。
    """
    import re

    out = []
    cq = (r"[\[【]\s*[Cc][Qq]\s*:\s*(?P<t>[a-zA-Z0-9_.\-]+)"
          r"(?P<p>(?:\s*,\s*[a-zA-Z0-9_.\-]+\s*=[^,\]]*)*)\s*[\]】]")
    for m in re.finditer(cq, str(text or "")):
        data = {}
        for item in filter(None, (x.strip() for x in m.group("p").lstrip(",").split(","))):
            if "=" in item:
                k, v = item.split("=", 1)
                data[k.strip()] = v
        out.append((m.group("t"), data))
    plain = r"[\[【]\s*(?P<t>[a-zA-Z0-9_.\-]+)\s*:\s*(?P<k>[a-zA-Z0-9_.\-]+)\s*=\s*(?P<v>[^\]】\s]+)\s*[\]】]"
    for m in re.finditer(plain, str(text or "")):
        if m.group("t").lower() == "cq":
            continue
        out.append((m.group("t"), {m.group("k"): m.group("v")}))
    return out


def find_reply_segment(event) -> dict:
    """这条消息里的**引用段**（OneBot 的 `reply` 段）。

    返回 `{"reply_id": str, "from": str, "segs": [(type, data), …]}`：
    `reply_id` 为空 = 没有引用段；`from` 写清**依据**（哪一段、哪个字段），
    调用方原样打进日志 —— 以后再出"明明引用了却说没引用"，一眼定位。

    为什么不能只认 `seg.type == "reply" and seg.data["id"]`（2026-10-11 晚）：
    ① 段可能是 dict，`getattr(seg, "type")` 直接拿不到；
    ② 引用 id 的字段名不止一个（`id` / `message_id` / `msg_id` / `reply_id`）；
    ③ 段列表整个缺失时，原始串里的 CQ 码还得兜得住。
    """
    segs = message_segments(event)
    for t, d in segs:
        if t != "reply":
            continue
        for key in ("id", "message_id", "msg_id", "reply_id"):
            value = str((d or {}).get(key) or "").strip()
            if value:
                return {"reply_id": value, "from": "seg:reply.%s" % key, "segs": segs}
        return {"reply_id": "", "from": "seg:reply(无 id 字段)", "segs": segs}
    # 段里没有：原始串的 CQ 码 / event.reply 兜底
    raw = ""
    for attr in ("raw_message", "message"):
        value = getattr(event, attr, None)
        if isinstance(value, str) and value:
            raw = value
            break
    for t, d in _parse_cq_codes(raw):
        if t == "reply":
            value = str((d or {}).get("id") or (d or {}).get("message_id") or "").strip()
            return {"reply_id": value, "from": "cq:reply", "segs": segs}
    reply = getattr(event, "reply", None)
    rid = ""
    if reply is not None:
        rid = str(getattr(reply, "id", "") or getattr(reply, "message_id", "") or "").strip()
        if not rid:
            dump = getattr(reply, "model_dump", None)
            if callable(dump):
                try:
                    got = dump()
                    if isinstance(got, dict):
                        rid = str(got.get("id") or got.get("message_id") or "").strip()
                except Exception:  # noqa: BLE001
                    rid = ""
    if rid:
        return {"reply_id": rid, "from": "event.reply", "segs": segs}
    return {"reply_id": "", "from": "none", "segs": segs}


def describe_segments(event, limit: int = 8) -> str:
    """一段给人看的段列表（排查用，别把超长正文打爆日志）。"""
    parts = []
    for t, d in message_segments(event)[:limit]:
        text = str((d or {}).get("text") or "")
        parts.append("%s(text=%r)" % (t, text[:20]) if text
                     else "%s(%s)" % (t, ",".join(sorted(str(k) for k in (d or {})))))
    return "[" + ", ".join(parts) + "]"


def _mention_is_bot(data: dict, ids: set) -> bool:
    """这条 mention 是不是机器人自己（先看标记，再看 id）。"""
    if data.get("is_bot") or data.get("is_you"):
        return True
    mid = str(data.get("user_id") or data.get("qq") or "").strip()
    if not mid or mid.lower() == "all":
        return False          # @全体成员 之类：不是机器人自己
    return bool(ids) and mid in ids


def mentions_other(event, bot_id: str = "") -> bool:
    """这条消息有没有 @ 别人（用于"别人 @ 别人时不抢话"）。

    2026-10-10 修：mention 没有 is_bot/is_you 标记时，原来一律当成"@ 别人"——
    @ 机器人自己也被当成 @ 别人，于是"视为与机器人无关，不回复"。现在按机器人 id 排除。

    2026-10-11 晚 修（H-7）：OneBot v11 的 @ 段**叫 `at`、id 在 `qq` 字段**
    （官方通道叫 `mention_user` / `user_id`）—— 这里两种形态都认，
    于是 OneBot 侧也能直接复用本函数，不必再手写一份（那份在 `bot.self_id`
    为空时会把"@ 我自己"算成"@ 别人"）。

    和 `is_at_me` **用同一套 mention 解析**：机器人开了"接收所有消息"后，
    群里每条消息都走全量事件（`to_me` 恒为 False），@ 别人也只会体现在
    mention 列表里 —— 以前只按 @-专用事件写的那份判断在全量事件下永远认不出来，
    就会去接别人的话。
    """
    ids = _bot_ids(event, bot_id)
    for seg_type, data in message_segments(event):
        if seg_type not in ("mention_user", "at"):
            continue
        if not _mention_is_bot(data, ids):
            return True
    try:
        for mention in (getattr(event, "mentions", None) or []):
            if bool(getattr(mention, "is_you", False)) or bool(getattr(mention, "bot", False)):
                continue
            mid = ""
            for attr in ("id", "user_id", "user_openid", "openid"):
                v = getattr(mention, attr, None)
                if v:
                    mid = str(v).strip()
                    break
            if mid and ids and mid in ids:
                continue          # 是机器人自己，不算 @ 别人
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def mention_targets(event, bot_id: str = "") -> list:
    """这条消息 @ 了哪些**别人**（返回 id 列表；机器人自己 / @全体都不算）。

    2026-10-12 老板实测（禁言）：@ 段在归一化时会被剥掉 —— 「禁言 @某人 一分钟」
    到 AI 手里只剩「禁言 一分钟」，它只能反问"禁谁啊"。把被 @ 的号补进给 AI 的
    上下文，它才能把"他"对上号（禁言、关于他的问题都要这个）。
    """
    out: list = []

    def _add(value) -> None:
        mid = str(value or "").strip()
        if not mid or mid.lower() == "all" or mid in out:
            return
        out.append(mid)

    ids = _bot_ids(event, bot_id)
    for seg_type, data in message_segments(event):
        if seg_type not in ("mention_user", "at"):
            continue
        if _mention_is_bot(data, ids):
            continue
        _add((data or {}).get("user_id") or (data or {}).get("qq"))
    try:
        for mention in (getattr(event, "mentions", None) or []):
            if bool(getattr(mention, "is_you", False)) or bool(getattr(mention, "bot", False)):
                continue
            mid = ""
            for attr in ("id", "user_id", "user_openid", "openid"):
                v = getattr(mention, attr, None)
                if v:
                    mid = str(v).strip()
                    break
            if mid and ids and mid in ids:
                continue
            _add(mid)
    except Exception:  # noqa: BLE001
        pass
    return out
