# -*- coding: utf-8 -*-
"""平台中立事件层：让 AI 大脑不感知具体平台（QQ / 微信 ClawBot / 飞书 / 纸飞机）。

设计目标：
- 大脑（brain.py）只依赖 ChannelMessage 与 ChannelContext；
- 各平台适配器（adapters/）负责把平台事件转成 ChannelMessage，并实现发送；
- 老平台（QQ）行为不回归：会话记忆 key 沿用旧格式。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

PLATFORM_QQ = "qq"
PLATFORM_QQ_OFFICIAL = "qq_official"
PLATFORM_WECHAT = "wechat"
PLATFORM_FEISHU = "feishu"
PLATFORM_TELEGRAM = "telegram"

SCENE_PRIVATE = "private"
SCENE_GROUP = "group"


async def _noop(*args, **kwargs):
    return None


@dataclass
class ChannelMessage:
    """归一化入站消息。"""

    platform: str                 # PLATFORM_*
    scene: str                    # SCENE_PRIVATE / SCENE_GROUP
    user_id: str                  # 平台内用户 id（QQ 号 / wechat im id / ...）
    room_id: str                  # 会话 id：群=群号，私聊=user_id
    text: str                     # 归一化文本（QQ 侧已含图片识别结果）
    is_at_me: bool = False        # 是否 @ 机器人（群聊）
    nickname: str = ""            # 发送者昵称
    plain_text: str = ""          # 纯文本（用于命令过滤，不含图片识别等附加内容）
    raw: Any = None               # 原始平台事件（适配器内部用）
    extra: dict = field(default_factory=dict)  # 平台附加数据（如 at_others、wechat context_token）
    is_superuser: bool = False    # 该消息发送者是否为机器人管理员（微信 ClawBot 扫码者默认是）

    @property
    def context_key(self) -> str:
        """会话记忆 key。QQ 沿用旧格式，保证老客户历史上下文不失效。"""
        return context_key_for(self.platform, self.scene, self.room_id)

    @property
    def session_key(self) -> str:
        """免@会话 / 群活跃度 key（平台+会话）。"""
        return f"{self.platform}:{self.room_id}"

    @property
    def user_key(self) -> str:
        """跨平台用户 key（平台+用户）。"""
        return f"{self.platform}:{self.user_id}"


def context_key_for(platform: str, scene: str, room_id: str) -> str:
    """会话记忆 key 的**唯一口径**（入站消息与主动消息都必须用这个）。

    2026-10-15（审计 memory/extra-4）：主动聊天以前拿**裸 openid** 当上下文 key，
    而用户回话时按 `qq_official:private:<openid>` 读 —— 开场白永远进不了后续对话，
    "用户回复时机器人记得这次主动聊天"这句承诺落不了地。抽成函数让两边同源。
    """
    platform = str(platform or "")
    scene = str(scene or "")
    room_id = str(room_id or "")
    if platform == PLATFORM_QQ:
        return f"group_{room_id}" if scene == SCENE_GROUP else room_id
    return f"{platform}:{scene}:{room_id}"


@dataclass
class ChannelContext:
    """发送/通知抽象：大脑只调用这几个方法，不感知平台。

    G1（媒体动作，2026-10-02）：多出 send_image / send_file / send_voice / send_card。
    通道不支持时留空（None），调用方要按"发不出去"处理并写日志 —— 绝不假装成功。
    """

    platform: str
    scene: str
    send_text: Callable[[str], Awaitable[None]]          # 发一段普通文本
    send_text_at: Callable[[str], Awaitable[None]]       # 发一段 @触发者 的文本（群聊）
    notify_error: Callable[[str], Awaitable[None]] = _noop  # 通知管理员（平台错误上报）
    # 这个通道**实际会把消息发到**哪个会话（与 ChannelMessage.session_key 同口径）。
    # 为什么要显式声明（2026-10-11 "串人"事故）：适配器如果靠"matcher 当前事件"决定
    # 目标，两条消息并发时就会发错会话。声明出来之后，`target_guard.check_reply_target`
    # 能在发送前对一遍账，对不上就不发（宁可沉默，也不要发错人）。
    # 留空 = 老适配器还没填，护栏不拦（行为不变）。
    target_key: str = ""
    # 媒体：参数分别是 (图片地址或本地路径, 说明文字) / (文件地址, 文件名) / (语音地址,)
    send_image: Callable[..., Awaitable[bool]] | None = None
    send_file: Callable[..., Awaitable[bool]] | None = None
    send_voice: Callable[..., Awaitable[bool]] | None = None
    # 音乐卡片 / ark 卡片：QQ 官方支持，其它通道多半没有
    send_card: Callable[..., Awaitable[bool]] | None = None
    # 带按钮的消息（QQ 官方支持键盘；发不了就由调用方降级成"1. xxx"的文字列表）
    send_buttons: Callable[..., Awaitable[bool]] | None = None
    # 主动发到指定目标（G2）：target 形如 "group:123" / "user:abc"；
    # 白名单校验在 tools_bridge 侧做，这里只负责"能不能发"。
    # ⚠ 2026-10-03 事故：这个字段当时只在适配器里传、忘了在数据类里声明，
    #   消息一进来就 TypeError（表现为"连上了但不回话"）——
    #   以后加字段务必同步这里，有 tests/test_channel_context_fields.py 兜着。
    send_to_target: Callable[..., Awaitable[bool]] | None = None
    # 这个通道能不能**自然地连发多条**（2026-10-12 老板要求"像人一样分几条发"）。
    # 默认 **False = 保守**：没显式标记过的通道一律合并成一条。
    # 只有 OneBot(NapCat) 这种确定能连发的置 True；QQ 官方（主动消息有额度）、
    # 微信 iLink、飞书一律 False —— "能自然连发的通道才分条，受限通道默认合并"。
    multi_message: bool = False
    # 为什么不能连发（这一句会**原样进日志**，别让人猜）
    multi_message_note: str = ""

    def supports(self, kind: str) -> bool:
        """这个通道支不支持某种媒体（插件据此给用户一句人话，而不是默默失败）。"""
        return getattr(self, f"send_{kind}", None) is not None

    async def send_media(self, kind: str, target: str, *args) -> bool:
        """按类型发媒体；不支持时返回 False（调用方负责告诉用户）。"""
        sender = getattr(self, f"send_{kind}", None)
        if sender is None:
            return False
        try:
            return bool(await sender(target, *args))
        except Exception as e:  # noqa: BLE001 —— 发媒体失败不能把收消息流程打断
            try:
                from nonebot import logger

                logger.warning(f"[platform] 发 {kind} 失败：{e}")
            except Exception:  # noqa: BLE001
                pass
            return False
