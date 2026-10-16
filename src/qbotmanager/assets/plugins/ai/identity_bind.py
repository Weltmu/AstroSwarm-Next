# -*- coding: utf-8 -*-
"""跨平台身份确认：让"微信上的他"和"QQ 上的他"被认成同一个人。

老板 2026-10-03 的问题："机器人不知道哪个微信和哪个 QQ 是同一个人的，咋办？"
答案：平台不给手机号/QQ号（官方通道连 QQ 号都没有，只有 openid），**没有任何技术
手段能自动对上** —— 只能让用户自己确认一次。这就是本文件干的事：

1. 他在任意平台私聊说「绑定」→ 我给他一个 6 位码（默认 5 分钟有效）；
2. 他去另一个平台私聊我，发「绑定 482913」→ 两边身份合并成一组；
3. 合并后：记忆互通（老的 identity 模块已经在用），而且他可以说
   "把这文件传到我 QQ" —— 由主程序的 send_to_my_platform 工具按绑定关系发。

规矩：
- 每人 30 秒内只能要一次码（防刷）；
- 码一次性使用、过期作废；
- 只绑定"自己"的两段身份：谁拿到码谁说话，码只回给要码的那个人。
"""
from __future__ import annotations

import json
import secrets
import time

from nonebot import logger

from .platform import SCENE_PRIVATE

CODE_TTL = 300          # 秒：码的有效期
ISSUE_COOLDOWN = 30     # 秒：同一个人两次要码的最小间隔
MAX_CODES = 200         # 上限，防内存/文件无限涨

# 2026-10-15（审计 G1）：兑换失败的限速。以前 redeem **完全没有任何失败惩罚** ——
# 同时最多 200 个有效码 / 100 万个组合 / 5 分钟窗口，攻击者按"一条消息一次"的
# 速度刷 6 位码就能期望命中（命中即双向并组：记忆互通、跨平台发送按对方身份走，
# 而码的主人**完全收不到任何通知**）。现在按身份限速 + 全局限速。
REDEEM_FAIL_WINDOW = 300.0     # 秒：失败计数窗口
REDEEM_FAIL_PER_KEY = 5        # 同一个人在这个窗口里最多失败几次
REDEEM_FAIL_GLOBAL = 30        # 全局上限（同机多身份轮着刷也拦得住）
_redeem_fails: dict = {}       # key -> [失败时间戳...]
_redeem_fails_global: list = []


def _redeem_blocked(user_key: str, now: float) -> bool:
    """这个身份 / 全局是不是已经刷太多次了。"""
    fails = [t for t in _redeem_fails.get(user_key, []) if now - t < REDEEM_FAIL_WINDOW]
    _redeem_fails[user_key] = fails
    glob = [t for t in _redeem_fails_global if now - t < REDEEM_FAIL_WINDOW]
    _redeem_fails_global[:] = glob
    return len(fails) >= REDEEM_FAIL_PER_KEY or len(glob) >= REDEEM_FAIL_GLOBAL


def _note_redeem_fail(user_key: str, now: float) -> None:
    _redeem_fails.setdefault(user_key, []).append(now)
    _redeem_fails_global.append(now)
    if len(_redeem_fails) > 500:      # 别让这张表无限长
        for k in list(_redeem_fails)[:200]:
            if k != user_key:
                _redeem_fails.pop(k, None)

PLATFORM_NAMES = {
    "qq": "QQ（第三方协议端）",
    "qq_official": "QQ 官方机器人",
    "wechat": "微信",
    "feishu": "飞书",
    "telegram": "纸飞机",
}


# 2026-10-12 老板：绑定成功的回复别用固定用语，让 AI 按人设自己说。
# 这里只登记"谁刚刚绑定成功"，由 chat.platform_context 取走（一次性，120 秒内有效）。
JUST_BOUND: dict = {}
JUST_BOUND_TTL = 120.0


def mark_just_bound(user_key: str, info: str) -> None:
    """记下"这个身份刚刚完成了绑定"，等 AI 大脑来取。"""
    if user_key:
        JUST_BOUND[user_key] = (time.time(), info or "")


def take_just_bound(user_key: str) -> str:
    """取走并清掉（过期的不给）。返回要告诉 AI 的那句话，没有就返回空串。"""
    item = JUST_BOUND.pop(user_key, None) if user_key else None
    if not item:
        return ""
    ts, info = item
    if time.time() - ts > JUST_BOUND_TTL:
        return ""
    return info


def platform_name(key: str) -> str:
    """把身份 key（platform:user_id）翻成人话里的平台名。"""
    head = str(key or "").split(":", 1)[0]
    return PLATFORM_NAMES.get(head, head or "未知平台")


def _codes_file():
    import nonebot_plugin_localstore as store

    return store.get_plugin_config_file("aichat_identity_codes.json")


def _load() -> dict:
    try:
        path = _codes_file()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[identity] 读绑定码失败: {e}")
    return {}


def _save(data: dict) -> None:
    try:
        path = _codes_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[identity] 存绑定码失败: {e}")


def _prune(data: dict, now: float) -> dict:
    """清掉过期的码 + 顺带清掉太久以前的"上次要码时间"。

    注意 `_last` 是**记账**（user_key → 时间戳），不是码 —— 早先的写法把它当码一起
    清掉了，结果冷却失效（30 秒内能连续要码），用例抓出来的。
    """
    out = {}
    for code, value in data.items():
        if code == "_last":
            continue
        if isinstance(value, dict) and now - float(value.get("at") or 0) < CODE_TTL:
            out[code] = value
    lasts = data.get("_last") if isinstance(data.get("_last"), dict) else {}
    keep_last = {k: float(v) for k, v in lasts.items()
                 if isinstance(v, (int, float)) and now - float(v) < ISSUE_COOLDOWN * 4}
    if keep_last:
        out["_last"] = keep_last
    if len(out) > MAX_CODES:
        for code in sorted((c for c in out if c != "_last"),
                           key=lambda c: out[c].get("at") or 0)[: len(out) - MAX_CODES]:
            out.pop(code, None)
    return out


def issue(user_key: str, now: float | None = None) -> dict:
    """给这个身份发一个新码；返回 {ok, code?, error?, wait?}。"""
    now = time.time() if now is None else float(now)
    data = _prune(_load(), now)
    last = float(((data.get("_last") or {}) if isinstance(data.get("_last"), dict) else {})
                 .get(user_key) or 0)
    if now - last < ISSUE_COOLDOWN:
        return {"ok": False, "error": "too_soon",
                "wait": int(ISSUE_COOLDOWN - (now - last))}
    code = ""
    for _ in range(20):
        # 2026-10-15：码原来用 `random.randint`（梅森旋转，**不是密码学随机**）——
        # 观测到几个码就能推后续序列。换成 secrets。
        code = f"{secrets.randbelow(1000000):06d}"
        if code not in data:
            break
    data[code] = {"key": user_key, "at": now}
    lasts = data.get("_last") if isinstance(data.get("_last"), dict) else {}
    lasts[user_key] = now
    data["_last"] = lasts
    _save(data)
    return {"ok": True, "code": code, "ttl": CODE_TTL}


def redeem(code: str, user_key: str, now: float | None = None) -> dict:
    """用码把两个身份绑到一起；返回 {ok, other?, error?}。"""
    from . import identity

    now = time.time() if now is None else float(now)
    text = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(text) != 6:
        return {"ok": False, "error": "bad_code"}
    # 先看限速：刷太多次就直接拒（不查表、不计时延，省得被当"探测器"用）
    if _redeem_blocked(user_key, now):
        logger.warning("[identity] 兑换码尝试过于频繁，已临时拒绝｜user=%s" % user_key)
        return {"ok": False, "error": "too_many_attempts"}
    data = _prune(_load(), now)
    entry = data.get(text)
    if not isinstance(entry, dict):
        _note_redeem_fail(user_key, now)      # 猜错的次数要记（见上面的限速）
        return {"ok": False, "error": "expired"}
    owner = str(entry.get("key") or "")
    if not owner:
        return {"ok": False, "error": "expired"}
    if owner == user_key:
        return {"ok": False, "error": "same_identity"}
    if not identity.bind(owner, user_key):
        # 已经绑过了也算成功（用户重复发一次码不该报错）
        if identity.canonical(owner) == identity.canonical(user_key):
            data.pop(text, None)
            _save(data)
            return {"ok": True, "other": owner, "already": True}
        return {"ok": False, "error": "bind_failed"}
    data.pop(text, None)
    _save(data)
    return {"ok": True, "other": owner, "already": False}


def describe(user_key: str) -> str:
    """这个人的身份人话描述（给 AI 和用户看）。"""
    from . import identity

    keys = identity.resolve(user_key)
    if len(keys) <= 1:
        return ""
    names = []
    for key in keys:
        head, _, tail = key.partition(":")
        label = PLATFORM_NAMES.get(head, head)
        names.append(f"{label}")
    # 去重但保序
    seen = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return "、".join(seen)


async def try_handle(msg, ctx) -> bool:
    """命令拦截：绑定 / 绑定 123456 / 我的身份 / 直接发 6 位码。处理了就返回 True。

    2026-10-12 三处修正（老板："QQ 上绑定不了了"）：
      · 重复绑定（早就绑好了）→ **直接说清状态**：既不是失败，也别让模型自由发挥；
      · 单独发 6 位码、码不对或过期 → **明确回话**（以前丢给 AI，AI 只会回"这串数字是啥意思"）；
      · 首次绑定成功 → 交给 AI 按人设说（老板要的"别用固定用语"），并打日志留痕。
    """
    text = str(getattr(msg, "plain_text", "") or "").strip()
    if not text:
        return False
    # 2026-10-12 老板：他在 QQ 上直接发「788297」（最自然的做法）被判成闲聊。
    # 现在**正好 6 位数字**也算绑定尝试。
    bare_code = text.isdigit() and len(text) == 6
    if not (text == "绑定" or text.startswith("绑定") or text in ("我的身份", "我绑了谁")
            or bare_code):
        return False
    user_key = getattr(msg, "user_key", "") or f"{msg.platform}:{msg.user_id}"
    # 2026-10-15 安全修（群里绑定码被广播 → 记忆串人）：
    # 以前不管是群聊还是私聊，码都直接发回**当前会话**。群里发一次「绑定」，
    # 全群 5 分钟内都能拿这个码去私聊机器人，把**自己的号**和群里那个人的身份
    # 合并（双向并组）→ 此后两边记忆互相可见、跨平台发送按对方身份走。
    # 所以：**发码只认私聊**；群里的失败反馈也一律不吭声（免得围观者莫名其妙）。
    is_private = str(getattr(msg, "scene", "")) == SCENE_PRIVATE

    if bare_code:
        result = redeem(text, user_key)
        if result.get("ok"):
            info = describe(user_key) or PLATFORM_NAMES.get(msg.platform, msg.platform)
            if result.get("already"):
                if is_private:
                    await ctx.send_text(
                        f"你俩早就是同一个人了：{info}。不用重复绑 —— "
                        "直接说「发到我 QQ / 微信」就行。")
                    return True
                return False          # 群里别插话，交给 AI 正常聊
            mark_just_bound(user_key, info)
            logger.info("[identity] 绑定成功（单独发码）→ 交给 AI 说｜user=%s beyond=%s"
                        % (user_key, info))
            return False
        if result.get("error") == "same_identity":
            if is_private:
                await ctx.send_text("这是你自己当前这个账号的码，去另一个平台发它才有用。")
                return True
            return False
        if result.get("error") == "too_many_attempts":
            if is_private:
                await ctx.send_text("试太多次了，过几分钟再来。要码就发「绑定」。")
                return True
            return False
        # 找不到/过期：明确回一句。以前这里丢给 AI，AI 只会回"这串数字是啥"，
        # 用户根本分不清是流程错了还是码不对（老板 2026-10-12 就是这么卡的）。
        # 但**只在私聊**回：群里谁随口一个 6 位数（"666666"、验证码、数学题）
        # 都不该换来一条系统话术，更不该把那条消息从正常聊天里吞掉。
        if is_private:
            await ctx.send_text(
                "这个绑定码不对或已经过期了。重新在另一个平台发「绑定」拿一个新码，"
                f"{CODE_TTL // 60} 分钟内回来发给我（或直接发那 6 位数字）。")
            return True
        return False

    if text in ("我的身份", "我绑了谁"):
        # 纯查询（只列**他自己**绑了哪些平台，没有任何秘密），群聊私聊都能问 ——
        # 这条是 2026-10-15 之前的明确决定（tests/test_identity_bind_privacy.py::
        # test_identity_query_still_works_everywhere 锁着），审计提过"群里问会公示
        # 给全群"，但那毕竟是他自己发的话、查的是他自己的绑定，保持原样不动。
        info = describe(user_key)
        await ctx.send_text(
            f"你现在绑定的平台：{info}" if info else
            "你还没绑定其它平台。想绑的话，在这里发「绑定」，"
            "然后去另一个平台私聊我发「绑定 六位数字」。")
        return True

    code = "".join(ch for ch in text[2:] if ch.isdigit())
    if not code:
        # 2026-10-15 安全修：**码只在私聊发**（理由见上面 is_private 那段说明）。
        # 群里只回一句提示，绝不把码打出来。
        if not is_private:
            await ctx.send_text(
                "绑定码得私聊拿 —— 在群里发出来，别人能抢先把你的号绑走。"
                "私聊我发「绑定」就行。")
            return True
        result = issue(user_key)
        if not result.get("ok"):
            await ctx.send_text(f"刚给过你码了，{result.get('wait', 30)} 秒后再来一次。")
            return True
        await ctx.send_text(
            f"绑定码：{result['code']}（{CODE_TTL // 60} 分钟内有效）\n"
            f"去另一个平台私聊我，发一句：绑定 {result['code']}\n"
            "绑好之后，你说的「发到我的 QQ / 微信」这类我就能照做了。")
        return True

    result = redeem(code, user_key)
    if result.get("ok"):
        info = describe(user_key) or PLATFORM_NAMES.get(msg.platform, msg.platform)
        if result.get("already"):
            await ctx.send_text(
                f"你俩早就是同一个人了：{info}。不用重复绑 —— "
                "直接说「发到我 QQ / 微信」就行。")
            return True
        # 首次绑定成功：交给 AI 按人设说一句（老板 2026-10-12：别用固定话术）
        mark_just_bound(user_key, info)
        logger.info("[identity] 绑定成功 → 交给 AI 说｜user=%s beyond=%s" % (user_key, info))
        return False
    if result.get("error") == "expired":
        await ctx.send_text("这个码不对或已过期。重新发「绑定」拿一个新码。")
    elif result.get("error") == "same_identity":
        await ctx.send_text("这是你自己当前这个账号的码，去另一个平台发它才有用。")
    elif result.get("error") == "too_many_attempts":
        # 2026-10-15：刷码限速（见 redeem 上的说明）—— 别再回"码不对"，
        # 那等于告诉对方"继续猜"。
        await ctx.send_text("试太多次了，过几分钟再来。要码就发「绑定」。")
    else:
        await ctx.send_text("绑定没成功，稍后再试一次；不行就发「我的身份」看看现在绑了哪些。")
    return True
