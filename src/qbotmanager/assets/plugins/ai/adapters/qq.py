# -*- coding: utf-8 -*-
"""QQ 通道适配器（OneBot V11 → 平台中立模型）。"""
from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment

from ..group_policy import (describe_segments, find_reply_segment,
                              is_at_me, mention_targets, mentions_other)
from .. import file_reader
from .. import media_guard
from ..image2txt import recognize_image
from ..manager import chat_manager
from ..platform import (
    ChannelContext,
    ChannelMessage,
    PLATFORM_QQ,
    SCENE_GROUP,
    SCENE_PRIVATE,
)
from ..send2root import send_error_to_super_users


async def _extract_text(event: MessageEvent, bot=None) -> str:
    """原 event_proc：纯文本 + 图片识别结果。

    2026-10-12：**表情包单独标出来**。老板实测：拿表情包逗它，它却回
    「这是你画的吗」「好可爱的小熊」「从哪找的表情包这么丑」—— 因为它收到的只是
    一句"图片里画了什么"，就当成一张**作品**在点评。表情包是人在**表达情绪/玩梗**，
    得让它知道这是表情包（标签 + 提示词里的行为规则一起起作用）。
    """
    user_text = event.get_plaintext().strip()
    recognition_msg = f"{user_text}\n"
    # 2026-10-12：只发图片、又没识别结果（没配识图/识别失败）时，正文会是空的 →
    # 大脑判成"空 @"就回「在呢」（老板看到的怪现象）。这里保证**永远有内容**。
    _has_image = any(seg.type == "image" for seg in event.message)
    if _has_image and not user_text and not chat_manager.is_image_recognition_enabled():
        recognition_msg += "[他发来一张图片]（识图没开，看不到内容 —— 就当他给你看了张图，自然回应一句）"
    if chat_manager.is_image_recognition_enabled():
        image_urls = []
        for seg in event.message:
            if seg.type == "image":
                info = seg.data or {}
                # OneBot V11 表情包的各种长相（2026-10-12 实地见到的两种）：
                #   · summary="[动画表情]"、sub_type=2（收藏/热图）
                #   · summary=空、sub_type=2（群里的表情包常见这种）
                #   · sub_type=1 是表情类
                # 真照片一般是 sub_type=0、summary 为空 → 不会误判。
                summary = str(info.get("summary") or "")
                sub_type = str(info.get("sub_type") or "0")
                sticker = ("表情" in summary
                           or "face" in summary.lower()
                           or sub_type in ("1", "2"))
                image_urls.append((info.get("url"), sticker))
        if image_urls:
            try:
                recognition_list = []
                for i, (image_url, sticker) in enumerate(image_urls):
                    if image_url:
                        # 把地址登记成"机器人自己递出去的"（见 media_guard）：
                        # NapCat 给的图片地址常常就长在本机端口上，不登记的话
                        # "把这张图转到微信"会被内网拦截误伤。
                        media_guard.trust_origin(image_url)
                    result = await recognize_image(image_url)
                    # 把**原图地址**一起给它（2026-10-12 老板：让它把 QQ 上的照片转到微信，
                    # 它回"原图我这边接不到" —— 因为以前只喂了识别文字，模型手里没有地址，
                    # 想调用 send_file_to_my_platform 也没东西可传）。
                    addr = f"｜原图地址：{image_url}" if image_url else ""
                    if sticker:
                        recognition_list.append(
                            f"[表情包{i + 1}]（画面大意：{result}）—— 这是对方在用表情包说话，"
                            f"是在玩梗或表达情绪，不是让你点评这张图。{addr}")
                    else:
                        recognition_list.append(
                            f"[图片{i + 1}的识别结果]{result}{addr}")
                        recognition_list.append(
                            f"[图片{i + 1}·转发用] 要把它发到别的平台就说，地址上面那条里有；"
                            f"调 send_file_to_my_platform(platform=目标, url=原图地址, kind=image) 即可。")
                recognition_msg += "\n".join(recognition_list)
                logger.info(f"识别结果: {recognition_msg}")
            except Exception as e:  # noqa: BLE001
                error_msg = f"图片识别失败: {str(e)}"
                if not user_text:
                    recognition_msg += "[他发来一张图片]（识别失败）—— 看不见内容，如实说一句看不清"
                logger.info(error_msg)
                await send_error_to_super_users(error_msg, event)
                recognition_msg += (
            f"\n[图片识别失败]（原因：{str(e)[:80]}；可在客户端「AI 大脑」里换一个能看图的模型，或把识图开关打开）")
    # 2026-10-15：语音 / 视频段以前**一个字都不留** —— 只发一条语音时正文是空的，
    # 私聊被判成"没实义"直接忽略、群里被当"空 @"只回一句「在呢」（牛头不对马嘴）。
    # 官方通道会描述成"[收到1个语音[听不了语音，需要对方打字]]"、微信回"[对方发来一段语音]"，
    # 这里对齐：至少让 AI 知道自己漏了什么，别装没事。
    try:
        media_kinds = []
        for seg in event.message:
            kind = getattr(seg, "type", "")
            if kind in ("record", "video") and kind not in media_kinds:
                media_kinds.append(kind)
        for kind in media_kinds:
            if kind == "record":
                recognition_msg += ("[他发来一条语音]（这条通道没有语音转文字，听不了内容 —— "
                                    "如实说一句听不了，让他打字）\n")
            else:
                recognition_msg += ("[他发来一个视频]（看不了画面 —— "
                                    "需要他说一句重点，别编内容）\n")
    except Exception:  # noqa: BLE001 —— 兜底描述失败不影响回话
        pass

    # 2026-10-13（老板：发来的 pdf/md 读不读）：文件段读正文 —— md/txt 直读，
    # PDF 走 pypdf（装了就读）。读不出来也留一句人话，保证"发文件"不被当成空消息。
    try:
        file_segs = [seg for seg in event.message if getattr(seg, "type", "") == "file"]
    except Exception:  # noqa: BLE001
        file_segs = []
    for i, seg in enumerate(file_segs, 1):
        try:
            info = await file_reader.read_file(
                bot, seg.data or {},
                group_id=str(getattr(event, "group_id", "") or ""))
        except Exception as e:  # noqa: BLE001 —— 读文件失败只影响这一次
            info = {"name": str((seg.data or {}).get("file_name") or "文件"),
                    "text": "", "note": f"读取失败：{str(e)[:80]}", "url": ""}
        recognition_msg += "\n" + file_reader.describe(info, i)
    return recognition_msg


def _plain_from_raw(raw) -> str:
    """把被引用消息的 message 字段读成人话（**list 和单个 dict 都要认**）。

    2026-10-12 老板报的 bug：他**引用了一张图**，QQ 返回的 message 是**单个段 dict**，
    代码却直接 Message(raw) → nonebot 抛 Unexpected type: <class 'dict'>
    → 引用正文取不到 → 上下文被整批丢掉 → 机器人只能回「在呢」。
    """
    segs = raw if isinstance(raw, list) else [raw]
    parts = []
    for seg in segs:
        if isinstance(seg, str):
            parts.append(seg)
            continue
        if not isinstance(seg, dict):
            parts.append(str(seg))
            continue
        kind = str(seg.get("type") or "")
        data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
        if kind == "text":
            parts.append(str(data.get("text") or ""))
        elif kind == "image":
            parts.append("[图片]")
        elif kind == "face":
            parts.append("[表情]")
        # 2026-10-16：补几个以前会被整条吞掉的段型（引用里是语音/视频/文件/表情包时，
        # 正文不至于全空 → 引用被当成"解析不出来"，上下文整批丢）。
        elif kind in ("mface", "marketface", "bface"):
            parts.append("[动画表情]")
        elif kind == "record":
            parts.append("[语音]")
        elif kind == "video":
            parts.append("[视频]")
        elif kind == "file":
            parts.append("[文件]")
        elif kind in ("json", "xml"):
            parts.append("[卡片]")
    text = "".join(parts).strip()
    if text:
        return text
    # 兜底：交给 nonebot 自己解（能解出来就用）
    try:
        return Message(raw).extract_plain_text().strip()
    except Exception:  # noqa: BLE001
        return ""


async def _describe_quoted_media(raw) -> str:
    """把**被引用消息里的图片**讲给 AI 听（2026-10-16 老板实测：群里引用照片，它说看不见）。

    以前 `_plain_from_raw` 把图片段折成字面量 `[图片]` —— 模型只知道"被引用的是一条
    图片"，看不到画面、也没有原图地址，只能回"我看不见"。这里按和主消息
    （`_extract_text`）**同一条链**补上：
      · 登记原图地址（media_guard）→ 这张图之后可以转发到别的平台；
      · 识图开关开着 → 走多模态模型识别，结果写进引用正文；
      · 识别失败/开关关着 → 如实说一句，并把原图地址给它（转发仍然可用）。
    """
    segs = raw if isinstance(raw, list) else [raw]
    urls = []
    for seg in segs:
        if not isinstance(seg, dict):
            continue
        if str(seg.get("type") or "") != "image":
            continue
        data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
        url = str(data.get("url") or "").strip()
        if url.startswith(("http://", "https://")) and url not in urls:
            urls.append(url)
    if not urls:
        return ""
    recognize_on = False
    try:
        recognize_on = bool(chat_manager.is_image_recognition_enabled())
    except Exception:  # noqa: BLE001 —— 读开关失败按"不识别"处理，别挡住引用正文
        recognize_on = False
    lines = []
    for i, url in enumerate(urls[:2], 1):
        # 和主消息路径一致：NapCat 的图片地址常常长在本机端口上，先登记出处
        try:
            media_guard.trust_origin(url)
        except Exception:  # noqa: BLE001
            pass
        desc = ""
        if recognize_on:
            try:
                desc = str(await recognize_image(url) or "").strip()
            except Exception as e:  # noqa: BLE001 —— 识别失败不影响引用正文
                logger.info(f"[qq] 引用图片{i}识别失败：{str(e)[:100]}")
        if desc:
            lines.append(f"[引用的消息里是图片{i}，内容识别结果] {desc}"
                         f"｜原图地址：{url}")
        else:
            lines.append(f"[引用的消息里是图片{i}，但这次没识别出内容]"
                         "（别猜画面，如实说看不到、请对方把图重新发一条）"
                         f"｜原图地址：{url}")
    return "\n".join(lines)


async def _extract_quoted(bot, event):
    """本条是不是「引用/回复某条消息」→ 把被引用那条的正文取出来。

    **只有 OneBot 的 `reply` 段才算引用**（2026-10-11 回归的正面修复）：
    以前"没有引用段"时返回的是 `{}`（空字典），而 brain 那边按 `is not None` 判 ——
    于是**只有 @ 段、根本没有引用的正常消息也被当成"引用拿不到正文"**，
    上文被整批丢掉（日志：`引用未解析 → 不带上文（丢弃 20 条）`），
    机器人从此对所有问题只能瞎答。现在明确返回 `None` = **没有引用段**。

    `@` 段、`face` 段、图片段**一律不算引用** —— 只认 `reply` 段。
    有 `reply` 段但取不到正文 → 返回 `content` 为空的 dict，交给 prompt_window
    判成"不带上文"（这条规则本身是对的，别一起改坏）。

    2026-10-11 晚 **再修一次**（H-25）：线上明明收到
    `[reply:id=388291130][at:qq=6885483] …`，这里却打"无 reply 段"。
    段是对象/dict、字段叫 `id`/`message_id`、段列表整个缺失只剩原始串 —— 三种形态
    现在都由 `group_policy.find_reply_segment` 统一认，而且**每次判定都把依据打进日志**
    （`from=seg:reply.id` / `cq:reply` / `event.reply` / `none`）＋ 段列表快照，
    以后再出"引用了却说没引用"，看一行日志就能定位。
    """
    found = find_reply_segment(event)
    reply_id = str(found.get("reply_id") or "")
    segs_line = describe_segments(event)
    logger.info("[qq] 引用段判定：%s（依据 %s）段列表=%s"
                % (("有引用段 id=%s" % reply_id) if reply_id else "**无引用段**",
                   found.get("from"), segs_line))
    if not reply_id:
        return None
    text, sender, reason = "", "", ""
    try:
        got = await bot.get_msg(message_id=int(reply_id))
        if isinstance(got, dict):
            raw = got.get("message")
            if raw is not None:
                text = _plain_from_raw(raw)
            if not text:
                text = str(got.get("raw_message") or "").strip()
            # 2026-10-13（老板：引用 pdf/md）：被引用的是文件 → 把内容读出来当正文
            if raw is not None:
                try:
                    file_text = await file_reader.files_text_from_raw(
                        bot, raw,
                        group_id=str(getattr(event, "group_id", "") or ""))
                except Exception as e:  # noqa: BLE001 —— 读文件失败不影响其它引用
                    file_text = ""
                    logger.info(f"[qq] 引用文件读取失败：{str(e)[:100]}")
                if file_text:
                    text = (text + "\n" + file_text).strip() if text else file_text
            # 2026-10-16（老板实测：引用照片说"看不见"）：被引用的图片要过识图
            if raw is not None:
                try:
                    media_text = await _describe_quoted_media(raw)
                except Exception as e:  # noqa: BLE001 —— 读引用媒体失败不影响其它引用
                    media_text = ""
                    logger.info(f"[qq] 引用媒体解析失败：{str(e)[:100]}")
                if media_text:
                    text = (text + "\n" + media_text).strip() if text else media_text
            sender = str(((got.get("sender") or {}).get("nickname")) or "")
    except Exception as e:  # noqa: BLE001 —— 取被引用那条失败不是错误，只是没有上文
        reason = f"取被引用消息失败：{str(e)[:120]}"
        logger.info(f"[qq] 引用段判定：有 reply 段（id={reply_id}）但{reason} → 不带上文")
    content = f"{sender}：{text}" if (sender and text) else text
    if not content:
        reason = reason or "被引用那条没有可读正文"
        logger.info(f"[qq] 引用段判定：有 reply 段（id={reply_id}）但{reason} → 不带上文")
        # 2026-10-12：取不到正文时**仍然是"不带上文"** —— `content` 必须保持空字符串
        # （适配器契约，测试③锁着）。那句"看不到你引用的那条、请再说一次"的提醒
        # **不塞进 content**（塞进去会被下游当成"解析成功"，还会把最近 3 条历史当
        # 引用后续带进上下文）；改由 brain 层在 quoted_state=="unresolved" 时注入
        # QUOTED_UNRESOLVED_HINT —— 提醒照旧给到模型，契约不被污染。
    else:
        logger.info(f"[qq] 引用段判定：有 reply 段（id={reply_id}，依据 {found.get('from')}），"
                    f"取到正文 {len(content)} 字 → 用被引用的那条")
    return {"content": content, "ts": 0.0, "message_id": reply_id, "reason": reason}



_GROUP_ROLES_CACHE: dict = {}          # group_id -> (时间戳, {"owner": 谁, "admins": [...]})
_GROUP_ROLES_TTL = 300.0               # 群主/管理员名单缓存 5 分钟（名单变得不快）


def _role_word(role: str) -> str:
    return {"owner": "群主", "admin": "管理员"}.get(str(role or ""), "普通成员")


async def _group_roles(bot, group_id: str, asker_id: str) -> str:
    """一句话说清群里的身份：我在本群是什么 / 说话的人是什么 / 群主和管理员是谁。

    2026-10-12 老板：**他看不见群管理或群主是谁** —— 所以它会答不出"谁是群主"，
    还会去试着禁言群主（一定失败）。这里查一次、拼成一行进提示词。
    """
    import time as _t

    me = str(getattr(bot, "self_id", "") or "")
    parts = []
    try:
        mine = await bot.call_api("get_group_member_info", group_id=int(group_id),
                                  user_id=int(me), no_cache=False)
        parts.append(f"你在本群是{_role_word((mine or {}).get('role'))}")
    except Exception as exc:  # noqa: BLE001
        logger.info(f"[qq] 查自己的群身份失败：{str(exc)[:80]}")
    if asker_id and asker_id != me:
        try:
            him = await bot.call_api("get_group_member_info", group_id=int(group_id),
                                     user_id=int(asker_id), no_cache=False)
            word = _role_word((him or {}).get("role"))
            if word != "普通成员":
                parts.append(f"正跟你说话的他**是{word}**")
        except Exception:  # noqa: BLE001
            pass
    now = _t.time()
    cache = _GROUP_ROLES_CACHE.get(group_id)
    if not cache or now - float(cache[0]) > _GROUP_ROLES_TTL:
        try:
            members = await bot.call_api("get_group_member_list", group_id=int(group_id))
            owner, admins = "", []
            for m in (members or []):
                tag = f"{m.get('card') or m.get('nickname') or ''}({m.get('user_id')})"
                r = str(m.get("role") or "member")
                if r == "owner":
                    owner = tag
                elif r == "admin":
                    admins.append(tag)
            cache = (now, {"owner": owner, "admins": admins[:8]})
            _GROUP_ROLES_CACHE[group_id] = cache
        except Exception as exc:  # noqa: BLE001 —— 有些协议端不给成员列表
            logger.info(f"[qq] 查群主/管理员名单失败（不影响回话）：{str(exc)[:80]}")
    if cache:
        data = cache[1]
        if data.get("owner"):
            parts.append(f"群主={data['owner']}")
        if data.get("admins"):
            parts.append("管理员=" + "、".join(data["admins"]))
    parts.append("（群主和管理员都不能被管理员禁言；要动谁先看清身份，别对群主下手）")
    return "；".join(parts)


async def build_channel_from_event(
    chat_handler, bot: Bot, event: MessageEvent
) -> tuple[ChannelMessage, ChannelContext]:
    """OneBot V11 事件 → ChannelMessage + ChannelContext。"""
    group_id = getattr(event, "group_id", None)
    scene = SCENE_GROUP if group_id else SCENE_PRIVATE
    user_id = str(event.user_id)
    room_id = str(group_id) if group_id else user_id
    nickname = event.sender.nickname if event.sender else ""
    plain = event.get_plaintext().strip()
    text = await _extract_text(event, bot)
    # 2026-10-13：nonebot 只认"开头/结尾"的 @ —— 消息中间 @ 机器人（典型就是
    # "先发图/文件，再 @ 机器人 问一句"）时 to_me 恒 False，机器人会装没看见。
    # 这里再兜一遍**全位置**的 mention 扫描（group_policy.is_at_me 就是干这个的）。
    is_at = bool(getattr(event, "is_tome", lambda: False)()) \
        or is_at_me(event, bot_id=str(getattr(bot, "self_id", "") or ""))
    # 私聊没有 @ 概念（老板口径）：`is_tome()` 在 OneBot v11 里就是 `to_me`，
    # 私聊恒为 True —— 私聊路径一律不参与 @ 相关判定，只按"有实义就回"走。
    if scene == SCENE_PRIVATE:
        is_at = False
    # @ 别人：**复用 group_policy 的解析**（2026-10-11 晚，H-7）。
    # 改前在这里手写 `seg.type == "at" and str(seg.data["qq"]) != str(bot.self_id)`：
    # ① 只有 `bot.self_id` 一条路，拿不到它时"@ 机器人自己"会被算成"@ 别人"；
    # ② 与 group_policy（含 user_id / is_bot / mentions 兜底）是两套解析，容易分叉。
    at_others = False
    at_targets: list = []
    if scene == SCENE_GROUP:
        _bot_id = str(getattr(bot, "self_id", "") or "")
        at_others = mentions_other(event, bot_id=_bot_id)
        # 被 @ 的别人的号（2026-10-12 禁言实测：正文里 @ 段会被剥掉，
        # 只给 AI 一句「禁言 一分钟」它不知道要动谁）—— 一并塞进 extra。
        at_targets = mention_targets(event, bot_id=_bot_id)
    quoted = await _extract_quoted(bot, event)
    # 2026-10-12（老板：他看不见群管理或群主是谁）：
    # 群消息里把"我在本群是什么身份 / 说话的人是什么身份 / 本群群主和管理员是谁"
    # 一起给它。不查的话它既答不出"谁是群主"，还会去试着禁言群主（一定失败）。
    group_roles = ""
    if scene == SCENE_GROUP:
        try:
            group_roles = await _group_roles(bot, str(group_id), user_id)
        except Exception as exc:  # noqa: BLE001 —— 查不到就算了，别影响回话
            logger.info(f"[qq] 群身份查询失败（不影响回话）：{str(exc)[:100]}")
    extra = {"at_others": at_others, "quoted": quoted, "at_targets": at_targets}
    if group_roles:
        extra["group_roles"] = group_roles
    msg = ChannelMessage(
        platform=PLATFORM_QQ,
        scene=scene,
        user_id=user_id,
        room_id=room_id,
        text=text,
        is_at_me=is_at,
        nickname=nickname,
        plain_text=plain,
        raw=event,
        extra=extra,
    )

    # ---- 发送：**按本事件捕获的会话显式发送** ----
    # 为什么不用 `chat_handler.send(...)`（2026-10-11 老板报的"串人"）：
    # 那是 NoneBot 的 **matcher 级**发送，目标是"这个 matcher 当前正在处理的事件"。
    # A 的点赞表情与 B 的提问几乎同时到达时，matcher 会并发处理两条 —— `send()`
    # 可能落到**另一条事件**的会话上，于是"A 的事回了 B 的话题"。
    # 这里按本事件捕获的 group_id / user_id 直接调原生接口，目标只由事件决定，
    # 与处理时序无关。
    async def _send_here(payload) -> None:
        if scene == SCENE_GROUP:
            await bot.call_api("send_group_msg", group_id=int(room_id), message=payload)
        else:
            await bot.call_api("send_private_msg", user_id=int(user_id), message=payload)

    async def send_text(t: str) -> None:
        await _send_here(t)

    async def send_text_at(t: str) -> None:
        await _send_here(Message(f"[CQ:at,qq={user_id}] {t}"))

    async def notify_error(err: str) -> None:
        await send_error_to_super_users(err, event)

    # ---- G1 媒体动作（OneBot V11：直接拼消息段）----
    async def send_image(target: str, caption: str = "") -> bool:
        await _send_here(Message(MessageSegment.image(target)))
        if caption:
            await _send_here(Message(caption))
        return True

    async def send_file(target: str, name: str = "") -> bool:
        """OneBot v11 标准里没有"文件段"：群聊走 upload_group_file，私聊老实说做不到。"""
        if scene != SCENE_GROUP:
            logger.info("[qq] 私聊发文件：OneBot v11 没有对应接口，已跳过")
            return False
        await bot.call_api("upload_group_file", group_id=int(room_id),
                           file=str(target), name=name or str(target).rsplit("/", 1)[-1])
        return True

    async def send_voice(target: str) -> bool:
        await _send_here(Message(MessageSegment.record(target)))
        return True

    async def send_card(target: str, title: str = "", singer: str = "",
                        song_id: str = "", image: str = "", audio: str = "") -> bool:
        """音乐卡片：字段齐 → 真·QQ 音乐卡；缺字段 → 降级"歌名 + 链接"文本（诚实降级）。

        2026-10-12 网易云点歌（plugins/ai/song.py）接进来时实测出来的两条硬规矩：
        - 必须发 `type=custom` 全字段（url/audio/title/image/content）—— NapCat 的
          签名服务只把这个签成真音乐卡（com.tencent.music.lua）；发 `type=163,id=`
          只会被签成图文卡，点了不会播，别再改回去；
        - 五件套缺一（尤其 image）就别硬发 —— 宁可在 QQ 里发"歌名 + 链接"两行字，
          也别给用户一张点不开的空卡片。
        `song_id`（网易云 id）只进日志，不参与 CQ 码。
        """
        if image and audio:
            payload = Message(MessageSegment("music", {
                "type": "custom",
                "url": target,
                "audio": audio,
                "title": title or "分享一首歌",
                "image": image,
                "content": singer or "",
            }))
            await _send_here(payload)
            return True
        text = "、".join([x for x in (title, singer) if x]) or "分享一首歌"
        await _send_here(Message(f"{text}\n{target}"))
        return True


    async def send_to_target(target: str, text: str) -> bool:
        """G2：主动发到指定目标（OneBot：group:123 走群，user:123 走私聊）。

        走 bot.call_api 的原生接口（适配器没有 send_group_msg 这类封装）。
        """
        kind, _, ident = str(target or "").partition(":")
        ident = (ident or kind).strip()
        if str(target).startswith("group:"):
            await bot.call_api("send_group_msg", group_id=int(ident), message=text)
        else:
            await bot.call_api("send_private_msg", user_id=int(ident), message=text)
        return True

    ctx = ChannelContext(
        platform=PLATFORM_QQ,
        scene=scene,
        send_text=send_text,
        send_text_at=send_text_at,
        notify_error=notify_error,
        send_image=send_image,
        send_file=send_file,
        send_voice=send_voice,
        send_card=send_card,
        send_to_target=send_to_target,
        # 声明"实际会发到哪"：护栏据此对账（QQ 的 session_key 就是 "qq:<room>"）
        target_key=f"{PLATFORM_QQ}:{room_id}",
        # 连发：OneBot/NapCat 就是一条条 send_group_msg，**确定能连发**（老板要的
        # "像人一样分几条发"主要就落在这一条通道上）。
        multi_message=True,
        multi_message_note="OneBot（NapCat）：可以连发多条",
    )
    return msg, ctx
