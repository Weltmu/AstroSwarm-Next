# -*- coding: utf-8 -*-
"""QQ 官方机器人通道适配器（nonebot-adapter-qq 事件 → 平台中立模型）。

platform 使用 qq_official，session_key 天然带平台前缀，与微信等通道会话隔离。
"""
from pathlib import Path

from nonebot import logger
from nonebot.adapters.qq import Message, MessageSegment

from .. import group_state
from .. import media_guard
from .. import rich_content
from ..contacts import remember as remember_contact
from ..group_policy import is_at_me, mentions_other
# 媒体动作的单文件上限（官方通道上传要求，别把内存撑爆）
MEDIA_MAX_BYTES = 20 * 1024 * 1024

from ..image2txt import recognize_image
from ..manager import chat_manager
from ..platform import (
    ChannelContext,
    ChannelMessage,
    PLATFORM_QQ_OFFICIAL,
    SCENE_GROUP,
    SCENE_PRIVATE,
)


def _is_group_event(event) -> bool:
    try:
        from nonebot.adapters.qq import GroupMessageCreateEvent

        return isinstance(event, GroupMessageCreateEvent)
    except Exception:  # noqa: BLE001
        return bool(getattr(event, "group_openid", None))


def _bot_id(bot) -> str:
    """机器人自己的 id（官方事件里 mention 的 user_id 会等于它）。"""
    try:
        return str(getattr(getattr(bot, "self_info", None), "id", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def _bot_ids(bot) -> list:
    """机器人自己的 id 候选（@ 判定按 id 比对时用；取到一个命中就够）。

    2026-10-15：以前只读 `self_info.id`（见 `_bot_id`）—— 官方适配器不同版本把
    自己的 id 放在不同字段上，漏一个就会出现"@ 我认不出来"。这里全收进来。
    """
    out = []
    for holder in (getattr(bot, "self_info", None), getattr(bot, "bot_info", None)):
        for attr in ("id", "open_id", "user_id", "app_id"):
            value = str(getattr(holder, attr, "") or "").strip()
            if value and value not in out:
                out.append(value)
    self_id = str(getattr(bot, "self_id", "") or "").strip()
    if self_id and self_id not in out:
        out.append(self_id)
    return out


def _is_at_me(event, bot=None) -> bool:
    """这条群消息 @ 的是不是机器人自己（规则见 group_policy.is_at_me）。

    全量群事件（开了"接收所有消息"之后每条群消息都是它）里 to_me 恒为 False，
    必须看 mention 列表里哪个是机器人自己，否则群里 @ 它永远不当作 @。

    2026-10-10 修：把机器人自己的 id 一起传进去比对 —— 官方事件的 mention 段
    不一定带 is_bot / is_you 标记，只靠标记会漏判（老板遇到的"又不说话了"）。
    """
    return is_at_me(event, bot_id=_bot_id(bot))


def remember_group_event(event, at_me: bool | None = None, group_openid: str = "") -> None:
    """把"这一条群消息是哪种事件"记进状态文件，供客户端状态卡显示。

    全量事件（GROUP_MESSAGE_CREATE）= 机器人开了"接收所有消息"；
    仅 @ 事件（GROUP_AT_MESSAGE_CREATE）= 只有被 @ 才收得到。
    这两种在界面上一模一样，记下来才能一眼说清"为什么群里 @ 没反应"。
    顺带把群 openid 记进"见过的群"名单，客户端据此让用户勾选允许回话的群。
    """
    try:
        import nonebot_plugin_localstore as store

        path = store.get_plugin_config_file(group_state.STATE_NAME)
        kind = group_state.normalize_event_type(getattr(type(event), "__type__", ""))
        if not kind:
            kind = type(event).__name__
        state = group_state.load(path)
        group_state.save(path, group_state.record(
            state, kind,
            at_me=_is_at_me(event) if at_me is None else bool(at_me),
            group_openid=group_openid or str(getattr(event, "group_openid", "") or ""),
        ))
    except Exception as e:  # noqa: BLE001 —— 记状态失败绝不能影响收消息
        logger.debug(f"[qq_official] 记群消息状态失败: {e}")


async def _extract_text(event) -> str:
    """官方事件 → 纯文本 + 引用/聊天记录 + 图片识别结果（与 OneBot 侧行为对齐）。"""
    user_text = event.get_plaintext().strip()
    recognition_msg = f"{user_text}\n"
    # 引用 / 聊天记录 / 附件说明：以前直接丢，AI 只能瞎猜
    if chat_manager.is_rich_parse_enabled():
        extra = rich_content.rich_context(event)
        if extra:
            recognition_msg = f"{extra}\n{recognition_msg}"
    image_urls = []
    try:
        for seg in event.get_message():
            if seg.type == "image":
                url = (seg.data or {}).get("url")
                if url:
                    image_urls.append(url)
    except Exception:  # noqa: BLE001
        image_urls = []
    if not image_urls:
        return recognition_msg

    # 2026-10-15：先把**原图地址**交给 AI —— OneBot 那边早就这么干了，官方通道
    # 以前只给识别文字，于是"把这张图转到微信"它只能回"原图我这边接不到"：
    # 模型手里没地址，调 send_file_to_my_platform 也没东西可传。
    # 官方图片地址是公开的 https（multimedia.nt.qq.com.cn），照旧登记一下出处，
    # 免得日后换成内网形态的地址时被 media_guard 当内网拦掉。
    forwards = []
    for i, url in enumerate(image_urls, 1):
        media_guard.trust_origin(url)
        forwards.append(f"[图片{i}·转发用] 原图地址：{url}"
                        "（要发到别的平台就调 send_file_to_my_platform"
                        "(platform=目标, url=这个地址, kind=image)）")

    if not chat_manager.is_image_recognition_enabled():
        # 识图没开：至少让它知道"有人发了图"，别把带图的消息当成空的
        recognition_msg += ("[他发来一张图片]（识图没开，看不到内容 —— 就当他给你看了张图，"
                            "自然回应一句）\n" + "\n".join(forwards))
        return recognition_msg
    try:
        parts = []
        for i, url in enumerate(image_urls):
            result = await recognize_image(url)
            parts.append(f"[图片{i + 1}的识别结果]{result}")
        recognition_msg += "\n".join(parts) + "\n" + "\n".join(forwards)
        logger.info(f"[qq_official] 图片识别结果: {recognition_msg}")
    except Exception as e:  # noqa: BLE001
        logger.info(f"[qq_official] 图片识别失败: {str(e)}")
        recognition_msg += (
            f"\n[图片识别失败]（原因：{str(e)[:80]}；可在客户端「AI 大脑」里换一个能看图的模型，或把识图开关打开）")
        recognition_msg += "\n" + "\n".join(forwards)
    return recognition_msg


def _button_action_type(b: dict) -> int:
    """按钮的 `action.type` —— 默认 **1 = 回调**（点了会产生 INTERACTION_CREATE）。

    官方取值（bot.q.qq.com「消息按钮」）：
      0 = 链接    —— data 是 URL / scheme
      1 = 回调    —— data 回传给后台，产生 INTERACTION_CREATE，机器人要在 3 秒内
                      `PUT /interactions/{id}` 应答，否则客户端一直转圈到超时
      2 = 指令    —— 只把 `@机器人 data` 填进输入框，**不产生任何事件**

    调用方可以用 `action_type` 显式覆盖（比如做纯链接按钮传 0）。
    """
    try:
        value = int(b.get("action_type"))
    except (TypeError, ValueError):
        return 1
    return value if value in (0, 1, 2) else 1


async def build_channel_from_interaction(chat_handler, bot, event):
    """按钮点击（INTERACTION_CREATE）→ 当成"用户说了这句话"交给 AI。

    官方通道的按钮回调只给 `button_id` / `button_data`（没有文字），
    所以这里把它拼成一句 AI 能懂的话；回复用 send_to_group / send_to_c2c
    （交互是通知事件，适配器的 bot.send 不认它）。
    """
    chat_type = int(getattr(event, "chat_type", 0) or 0)
    # 2026-10-15 修：点击事件里的 button_id / button_data **不在 `event.data` 上**。
    # nonebot-adapter-qq 1.7.3 的 `ButtonInteractionData` 只有 `resolved` 一个字段
    # （见其 models/common.py:144-155）：
    #     class ButtonInteractionData:  resolved: ButtonInteractionContent
    #     class ButtonInteractionContent: … button_id / button_data …
    # 旧代码直接从 `event.data` 上 getattr，三个都取空 → AI 永远只看到
    # 「（用户点了按钮：一个按钮）」，老板在客户端给按钮配的语义全丢，
    # 日志里也查不出点了哪个。这里兼容两种形态（取 resolved，取不到退回 data）。
    data = getattr(event, "data", None)
    resolved = getattr(data, "resolved", None) or data
    button_id = str(getattr(resolved, "button_id", "") or "")
    button_data = str(getattr(resolved, "button_data", "") or "")
    group_openid = str(getattr(event, "group_openid", "") or "")
    user_openid = str(getattr(event, "user_openid", "") or "")
    user_id = ""
    try:
        user_id = str(event.get_user_id())
    except Exception:  # noqa: BLE001
        user_id = user_openid or group_openid
    scene = SCENE_GROUP if (chat_type == 1 or group_openid) else SCENE_PRIVATE
    room_id = group_openid or user_openid or user_id
    # 按钮上带的数据优先（通常就是按钮文字），没有就用 id，再没有就说"点了按钮"
    what = button_data or button_id or "一个按钮"
    text = f"（用户点了按钮：{what}）"

    msg = ChannelMessage(
        platform=PLATFORM_QQ_OFFICIAL, scene=scene, user_id=user_id, room_id=room_id,
        text=text, is_at_me=True, plain_text="", raw=event,
        extra={"group_openid": group_openid, "interaction": True, "is_notice": True,
               "notice_kind": "interaction",
               "button_id": button_id, "button_data": button_data},
    )

    async def send_text(t: str) -> bool:
        # 2026-10-15 修：**优先走被动回复**（复用本次交互的 event_id），不再一律算主动消息。
        # 旧注释说"适配器的 bot.send 不认交互事件"是错的 —— 1.7.3 的 bot.py:719-744
        # 明确处理 `InteractionCreateEvent` 并把 `event.event_id` 透传给
        # send_to_group / send_to_c2c。每点一次按钮回一句都走主动消息，会把
        # 每日主动额度白白烧掉（烧完的表现是"机器人突然不说话、点按钮也没反应"）。
        try:
            await bot.send(event, t)
            return True
        except Exception as e:  # noqa: BLE001 —— 被动回复失败再退回主动发送，别把回复丢了
            logger.info(f"[qq_official] 交互被动回复失败，退回主动发送：{str(e)[:120]}")
        if group_openid:
            await bot.send_to_group(group_openid=group_openid, message=t)
        else:
            await bot.send_to_c2c(openid=user_openid or user_id, message=t)
        return True

    async def notify_error(err: str) -> None:
        logger.error(f"[qq_official] 按钮交互：{err}")

    ctx = ChannelContext(
        platform=PLATFORM_QQ_OFFICIAL, scene=scene,
        send_text=send_text, send_text_at=send_text, notify_error=notify_error,
        # 声明"实际会发到哪"（按钮交互是通知事件，护栏据此对账，对不上就不发）
        target_key=f"{PLATFORM_QQ_OFFICIAL}:{room_id}",
        # 官方通道默认**不连发**：被动回复有次数/时间窗限制、主动消息有额度，
        # 分多条会把额度烧掉（老板口径：受限通道默认合并成一条）。
        multi_message=False,
        multi_message_note="QQ 官方通道：主动消息有额度，分多条会把额度烧掉 → 默认合并",
    )
    return msg, ctx


async def build_channel_from_guild(chat_handler, bot, event):
    """频道事件 → 平台中立模型（room 用频道 id）。

    2026-10-15 修（两个真问题）：以前这个分支把**所有**频道事件当成
    "群里 @ 了我"——`scene` 恒为 group、`is_at_me` 恒为 True，于是：
      · 机器人一旦接收频道全量消息，成员之间正常互聊也会被它抢话
        （就是"@ 别人也必被抢话"的历史事故形态）；
      · 频道**私信**（DIRECT_MESSAGE_CREATE）被当成群聊，走群聊开关与群逻辑。
    现在：@ 判定交给 group_policy（同 OneBot/官方群那一套），私信按私聊处理。
    """
    kind = str(getattr(type(event), "__type__", "") or "")
    is_dm = "DIRECT" in kind.upper()
    channel_id = str(getattr(event, "channel_id", "") or "")
    guild_id = str(getattr(event, "guild_id", "") or "")
    user_id = ""
    try:
        user_id = str(event.get_user_id())
    except Exception:  # noqa: BLE001
        user_id = str(getattr(event, "user_id", "") or "")
    room_id = channel_id or guild_id or user_id
    plain = ""
    try:
        plain = event.get_plaintext().strip()
    except Exception:  # noqa: BLE001
        plain = ""
    nickname = ""
    author = getattr(event, "author", None)
    if author is not None:
        nickname = str(getattr(author, "username", "") or "")

    bot_ids = _bot_ids(bot)
    is_at = is_at_me(event, bot_id=bot_ids)
    at_others = mentions_other(event, bot_id=bot_ids)
    scene = SCENE_PRIVATE if is_dm else SCENE_GROUP
    if is_dm:
        is_at = True          # 私信天然是"对我说的"

    msg = ChannelMessage(
        platform=PLATFORM_QQ_OFFICIAL, scene=scene, user_id=user_id,
        room_id=room_id, text=plain or "（有人在频道里 @ 我，但没带文字）",
        is_at_me=is_at, nickname=nickname, plain_text=plain, raw=event,
        extra={"guild_id": guild_id, "channel_id": channel_id, "guild": True,
               "at_others": at_others, "quoted": None},
    )

    async def send_text(t: str) -> bool:
        await bot.send(event, t)
        return True

    async def notify_error(err: str) -> None:
        logger.error(f"[qq_official] 频道：{err}")

    ctx = ChannelContext(
        platform=PLATFORM_QQ_OFFICIAL, scene=scene,
        send_text=send_text, send_text_at=send_text, notify_error=notify_error,
        # 频道（公域）同样是官方通道：保守合并，别分条刷屏
        multi_message=False,
        multi_message_note="QQ 官方频道：官方通道保守处理 → 默认合并",
    )
    return msg, ctx


def audit_line(event) -> tuple[str, str]:
    """审核事件 → (级别, 一句人话)。通过=info，被拦=warn。

    平台审核是"消息发出去之后才判定"的：被拦下时用户那边什么也看不到，
    所以必须写进日志、也让管理员知道一声，不然就像"机器人突然不回话了"。
    """
    kind = str(getattr(type(event), "__type__", "") or "")
    group_openid = str(getattr(event, "group_openid", "") or "")
    user_openid = str(getattr(event, "user_openid", "") or "")
    where = f"群 {group_openid[:12]}…" if group_openid else (
        f"私聊 {user_openid[:12]}…" if user_openid else "未知会话")
    if "REJECT" in kind.upper():
        return "warn", f"平台拦下了一条消息（审核不通过，{where}）：用户那边看不到这条，别以为是机器人不说话"
    return "info", f"平台审核通过了一条消息（{where}）"


async def build_channel_from_qq_official(chat_handler, bot, event):
    """官方 QQ 事件 → ChannelMessage + ChannelContext。"""
    group = _is_group_event(event)
    scene = SCENE_GROUP if group else SCENE_PRIVATE
    user_id = event.get_user_id()
    room_id = str(getattr(event, "group_openid", "") or user_id)
    remember_contact(scene, room_id)
    plain = event.get_plaintext().strip()
    text = await _extract_text(event)
    is_at = _is_at_me(event, bot)
    nickname = ""
    author = getattr(event, "author", None)
    member_role = ""
    if author is not None:
        nickname = str(getattr(author, "username", "") or "")
        # 2026-10-15：群消息事件的 author 上**直接带 `member_role`**
        # （member / admin / owner；在 nonebot-adapter-qq 的 models/qq.py:16
        #  `GroupMemberAuthor` 里是**必填**字段）。所以官方通道判"喊话人有没有权限"
        # **不需要额外调接口、也不需要内邀白名单** —— 反而比 OneBot 侧更可靠
        # （那边要另查 get_group_member_info，还可能查失败）。
        # 带下去给 tools_bridge 的禁言安全闸用。
        member_role = str(getattr(author, "member_role", "") or "").lower()
    if group:
        # 口径统一：状态文件记的就是这里算出来的 is_at（原来 record 里又算了一遍）
        remember_group_event(event, at_me=is_at, group_openid=room_id)
        # 诊断：群里 @ 的真实结构（mention 认不出来时靠它定位；稳定后可删）
        try:
            segs = [(getattr(s, "type", ""), dict(getattr(s, "data", {}) or {}))
                    for s in event.get_message()]
            mentions = []
            for m in (getattr(event, "mentions", None) or []):
                dump = getattr(m, "model_dump", None)
                mentions.append(dump() if callable(dump) else repr(m))
            logger.info(
                "[qq_dbg] to_me=%r self_id=%r is_at=%r segs=%r mentions=%r plain=%r",
                getattr(event, "to_me", None), getattr(event, "self_id", None), is_at,
                segs, mentions, plain[:40])
        except Exception as e:  # noqa: BLE001
            logger.info(f"[qq_dbg] 诊断打印失败: {e}")
    msg = ChannelMessage(
        platform=PLATFORM_QQ_OFFICIAL,
        scene=scene,
        user_id=user_id,
        room_id=room_id,
        text=text,
        is_at_me=is_at,
        nickname=nickname,
        plain_text=plain,
        raw=event,
        extra={
            "group_openid": str(getattr(event, "group_openid", "") or ""),
            # 喊话人在本群的角色（member/admin/owner）—— 禁言等群管理动作的安全闸用它
            "member_role": member_role,
            # 别人@别人时不抢话：和 is_at_me 同一套 mention 解析，
            # 全量事件（群里每条消息都推过来）下也认得出"@ 的不是我"
            # 别人@别人时不抢话；机器人自己那条 mention 不算"@ 别人"（同样要传 bot id）
            "at_others": (mentions_other(event, bot_id=_bot_id(bot)) if group else False),
        },
    )

    async def send_text(t: str) -> bool:
        await bot.send(event, t)
        return True

    async def send_text_at(t: str) -> bool:
        # 官方接口按 msg_id 被动回复即可，不需要手动拼接 @
        await bot.send(event, t)
        return True

    async def notify_error(err: str) -> None:
        logger.error(f"[qq_official] {err}")

    # ---- G1 媒体动作（官方通道：适配器自带媒体上传，图片/视频/语音/文件都能发）----
    # 网址直接发；本地文件读成 bytes 走 file_* 段（适配器会用 file_data 上传）。
    def _read_local(target: str):
        try:
            path = Path(str(target))
            if not path.is_file():
                return None
            if path.stat().st_size > MEDIA_MAX_BYTES:
                logger.warning(f"[qq_official] 媒体文件太大（>{MEDIA_MAX_BYTES // 1048576}MB），已跳过")
                return None
            return path.read_bytes()
        except OSError as e:
            logger.warning(f"[qq_official] 读本地媒体失败：{e}")
            return None

    def _is_url(target: str) -> bool:
        return str(target).lower().startswith(("http://", "https://"))

    async def send_image(target: str, caption: str = "") -> bool:
        if _is_url(target):
            await bot.send(event, MessageSegment.image(target))
        else:
            blob = _read_local(target)
            if blob is None:
                return False
            await bot.send(event, MessageSegment.file_image(blob, file_name=Path(target).name))
        if caption:
            await bot.send(event, caption)
        return True

    async def send_file(target: str, name: str = "") -> bool:
        if _is_url(target):
            await bot.send(event, MessageSegment.file(target))
            return True
        blob = _read_local(target)
        if blob is None:
            return False
        await bot.send(event, MessageSegment.file_file(blob, file_name=name or Path(target).name))
        return True

    async def send_voice(target: str) -> bool:
        if _is_url(target):
            await bot.send(event, MessageSegment.audio(target))
            return True
        blob = _read_local(target)
        if blob is None:
            return False
        await bot.send(event, MessageSegment.file_audio(blob, file_name=Path(target).name))
        return True

    async def send_card(target: str, title: str = "", singer: str = "") -> bool:
        """音乐卡片：官方通道支持 ark 卡片；这里用"歌名+链接"的文本兜底，最稳。"""
        text = "、".join([x for x in (title, singer) if x]) or "分享一首歌"
        await bot.send(event, f"{text}\n{target}")
        return True

    async def send_buttons(text: str, buttons, per_row: int = 3) -> bool:
        """发一条**带按钮**的消息（官方键盘，msg_type=2：markdown + keyboard）。

        buttons 形如 [{"id": "sign_in", "label": "签到", "data": "签到"}, ...]；
        id 会原样回传给机器人（用户点了按钮就是 INTERACTION_CREATE，带这个 id）。
        做不出键盘就如实返回 False，由调用方降级成文字。
        """
        rows = []
        try:
            from nonebot.adapters.qq.models import (
                Action, Button, InlineKeyboard, MessageKeyboard, RenderData,
            )

            items = [b for b in (buttons or []) if isinstance(b, dict)][:12]
            per_row = max(1, min(int(per_row or 3), 5))
            for i in range(0, len(items), per_row):
                chunk = items[i:i + per_row]
                rows.append(InlineKeyboard.InlineKeyboardRow(buttons=[
                    Button(
                        id=str(b.get("id") or b.get("label") or f"btn{i + j}")[:60],
                        render_data=RenderData(
                            label=str(b.get("label") or b.get("id") or "按钮")[:20],
                            visited_label=str(b.get("visited_label")
                                               or b.get("label") or "已点击")[:20],
                            style=int(b.get("style") or 0),
                        ),
                        action=Action(
                            # 2026-10-15 修：旧代码默认写 2、注释还标成"2 = 回传（推荐）"
                            # —— 正好写反了。官方 2 是**指令按钮**（只把 `@机器人 data`
                            # 填进输入框，不产生任何事件）；用户点下去机器人这边收不到
                            # 任何回调，得自己再按一次发送。回调按钮必须是 1。
                            type=_button_action_type(b),
                            data=str(b.get("data") or b.get("id") or "")[:200],
                            # 权限 2 = 所有人。旧代码写 0（指定用户）却没给
                            # specify_user_ids —— 等于谁都点不了。
                            permission=Action.Permission(type=2),
                        ),
                    ) for j, b in enumerate(chunk)
                ]))
            # 平台限制：键盘最多 5 行（每行最多 5 个）。超了会被整条拒收，
            # 于是静默降级成纯文字列表 —— 用户看不到按钮还以为功能没做。
            rows = rows[:5]
            if not rows:
                return False
            keyboard = MessageKeyboard(content=InlineKeyboard(rows=rows))
            await bot.send(event, Message([
                MessageSegment.markdown(text or "请选择："),
                MessageSegment.keyboard(keyboard),
            ]))
            return True
        except Exception as e:  # noqa: BLE001 —— 键盘没做成别把回复搞丢
            logger.warning(f"[qq_official] 发带按钮的消息失败（会退化成文字）：{e}")
            return False

    async def send_to_target(target: str, text: str) -> bool:
        """G2：主动发到指定目标。

        官方通道按 openid 发，且**主动消息有额度/时间窗限制**（平台规则）；
        发不出去就抛异常让上层如实回报，不假装成功。
        """
        kind, _, ident = str(target or "").partition(":")
        ident = ident.strip() or kind.strip()
        if str(target).startswith("group:"):
            await bot.send_to_group(group_openid=ident, message=text)
        else:
            await bot.send_to_c2c(openid=ident, message=text)
        return True

    ctx = ChannelContext(
        platform=PLATFORM_QQ_OFFICIAL,
        scene=scene,
        send_text=send_text,
        send_text_at=send_text_at,
        notify_error=notify_error,
        send_image=send_image,
        send_file=send_file,
        send_voice=send_voice,
        send_card=send_card,
        send_buttons=send_buttons,
        send_to_target=send_to_target,
    )
    return msg, ctx
