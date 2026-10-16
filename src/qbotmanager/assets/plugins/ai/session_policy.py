# -*- coding: utf-8 -*-
"""免@连续对话的策略：两种判断模式 + 别人@别人不抢话。

单独放一个**不依赖 nonebot** 的文件，原因和 group_policy 一样：插件本体跑在
bot venv，构建环境的测试没有 nonebot —— 判定逻辑单独放，测试可以直接按路径加载。

两种模式（插件配置里的 session_mode 选）：
- `off`：关掉免@连续对话 —— 群里只有 @ 它才回，其余一律不理；
- `timeout`（默认）：群聊里被 @ 之后 N 秒内（默认 30，可配 30~300），这个人不用再 @
  也能继续聊；超时或连续多条无关消息就断开，下次必须重新 @。判断走"时间窗 + AI
  判一句"两步（时间窗内的消息才需要 AI 判）。
- `api`：不看时间窗，每条非 @ 的群消息都交给 AI，读群聊最近记录（带发送者 openid）
  判断要不要回。判断失败一律当"不回"并写日志。
"""
from __future__ import annotations

# ---- 免@会话有效期范围（秒）----
SESSION_MIN_SECONDS = 30
SESSION_MAX_SECONDS = 300
DEFAULT_SESSION_SECONDS = 30

# ---- 判断模式 ----
MODE_OFF = "off"            # 关掉免@连续对话：群里每条都得重新 @（给不想被打扰的人）
MODE_TIMEOUT = "timeout"
MODE_API = "api"
MODES = (MODE_OFF, MODE_TIMEOUT, MODE_API)

# choose_branch 的返回值：这一条群消息该怎么走
BRANCH_AT_ME = "at_me"              # 被 @ → 直接回
BRANCH_SKIP = "skip"                # @ 了别人 → 不抢话，直接跳过
BRANCH_JUDGE_API = "judge_api"      # api 模式：让 AI 读群记录判断
BRANCH_JUDGE_TIMEOUT = "judge_timeout"  # timeout 模式且在有效期内：让 AI 判是否延续
BRANCH_JUDGE_GROUP = "judge_group"  # 旧版群聊随机参与
BRANCH_IGNORE = "ignore"            # 其余情况不回复


def clamp_expire_seconds(value, minimum: int = SESSION_MIN_SECONDS,
                         maximum: int = SESSION_MAX_SECONDS) -> int:
    """把有效期夹到 30~300 秒；非法值回落到默认 30。"""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return DEFAULT_SESSION_SECONDS
    return max(int(minimum), min(int(maximum), seconds))


def normalize_mode(value) -> str:
    """模式名规范化：认不出的一律回 timeout（默认模式）。"""
    text = str(value or "").strip().lower()
    return text if text in MODES else MODE_TIMEOUT


def says_at_other(*, is_at_me: bool, at_others: bool) -> bool:
    """这一条是不是「@ 了别人、但没 @ 我」。

    **第一优先级**（2026-10-11 晚 老板急报：「我和机器人聊天时 @ 别人，机器人也
    选择回复」）：命中就必须**完全不接话**，并且**压过"免@延续"**——
    否则"正在聊天时顺手 @ 别人"一定会被它抢话。

    * `at_others` 由适配器算好（`group_policy.mentions_other`，与 `is_at_me`
      同一套 mention 解析）—— 这里**不另起一套**判定；
    * **只要 @ 了我就不算**：同时 @ 我和别人 = 有我在里面 = 正常回复（老板口径）；
    * 私聊没有 `at_others` 这回事（适配器恒为 False），因此私聊不受影响。

    改前的坑（别退回去）：`@ 了别人` 只体现在 `choose_branch` 的两条 AI 判断分支
    之后，timeout 分支里的 `direct_continue` 快捷路径（"机器人上一条回复对象就是他
    → 免 AI 判断直接回"）**根本没看它** —— 用户正在跟机器人聊、顺手 @ 了别人，
    这一条 100% 被抢话。
    """
    return bool(at_others) and not bool(is_at_me)


def wake_hit(text: str, words) -> str:
    """这条消息里有没有"叫机器人"的称呼词；命中返回那个词，没命中空串。

    2026-10-12 老板报「（群里）第一次喊了奈姐她没有回复」：裸喊称呼（不带 @）
    以前一律走免@判定，多数情况直接被忽略 —— 而"被叫到名字会回应"是人感最
    基本的预期。命中的消息**按被 @ 处理**（直接回 + 续期免@会话）。

    规矩：
    * 中文称呼按**子串**匹配（"奈姐" 出现在 "奈姐在吗" 里也算），英文不分大小写；
    * 词表由调用方给（客户端热配置里那条；空表 = 该功能关闭）；
    * **不改变「@ 了别人不抢话」的第一优先级** —— 那条判定在 brain 里排在它前面。
    """
    t = str(text or "").strip()
    if not t:
        return ""
    low = t.lower()
    for word in words or []:
        w = str(word or "").strip()
        if w and w.lower() in low:
            return w
    return ""


def choose_branch(*, is_at_me: bool, at_others: bool, mode: str,
                  session_active: bool, auto_participate: bool) -> str:
    """决定这一条群消息怎么处理（纯函数，方便测）。

    顺序就是优先级：
    1. 被 @ → 回（并续期免@会话）；
    2. @ 了别人（且没 @ 我）→ 不抢话，跳过（`says_at_other` 说的就是这一条，
       两种模式都一样；brain 那边还会在"免@延续"岔路**之前**先判它一次）；
    3. off 模式 → 只有上面的 @ 才回，其余一律不理（关掉免@连续对话）；
    4. api 模式 → 交给 AI 判断（不看时间窗）；
    5. timeout 模式 → 只在免@会话有效期内，才交给 AI 判断是否延续；
    6. 开了旧版群聊随机参与 → 掷骰子后交给 AI 判断；
    7. 其余 → 不回复。
    """
    if is_at_me:
        return BRANCH_AT_ME
    if says_at_other(is_at_me=is_at_me, at_others=at_others):
        return BRANCH_SKIP
    mode = normalize_mode(mode)
    if mode == MODE_OFF:
        return BRANCH_IGNORE
    if mode == MODE_API:
        return BRANCH_JUDGE_API
    if session_active:
        return BRANCH_JUDGE_TIMEOUT
    if auto_participate:
        return BRANCH_JUDGE_GROUP
    return BRANCH_IGNORE


def _log_warning(text: str) -> None:
    """写日志；没有 nonebot（比如构建环境跑测试）时静默。"""
    try:
        from nonebot import logger

        logger.warning(text)
    except Exception:  # noqa: BLE001
        pass


async def judge_quietly(judge, *, messages: list, user_id: str, nickname: str = "该用户",
                        last_reply_to: str = "") -> bool:
    """让 AI 判断这条免@消息要不要回；任何异常都当"不回"并写日志。

    判断失败绝不能把收消息的流程打断（更不许在这里发任何写死的兜底话术）。
    """
    try:
        value = await judge(messages, user_id, nickname, last_reply_to=last_reply_to)
        # 判断函数可能直接给布尔，也可能给 "YES 因为…" / "NO，…"（模型习惯），两种都认
        if isinstance(value, str):
            return value.strip().upper().startswith("YES")
        return bool(value)
    except Exception as e:  # noqa: BLE001
        _log_warning(f"免@会话判断失败（用户{user_id}），本条不回复：{e}")
        return False
