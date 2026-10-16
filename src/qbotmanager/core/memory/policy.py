# -*- coding: utf-8 -*-
"""内置记忆的**注入策略**与**隐私过滤**。

三档语义（老板 2026-10-11 拍板，必须严格区分）：

* ``off``   无：不写不读、不注入任何记忆；
* ``basic`` 基础 = **线上现在已有的那套行为**（只是从插件搬进程序里）：
      每次对话注入「该用户最近 3 条记忆 + 最近 2 天日记」，事实提炼每轮取最近
      25 条消息、最多 3 条去重写库，``recall`` 工具可翻最近 10 条；
* ``smart`` 智能 = 基础 + ① 分级/权重注入（最近 3 条 + 高权重 3 条 + 承诺类必带）
      + ② 时间线 + ③ 记忆合并（印象档）+ ④ 隐私过滤 + ⑤ 真删/可查 + ⑥ 冲突以新为准。

隐私边界（写进实现，不是口号）：手机号 / 身份证 / 银行卡 / 详细住址一律**不写库**。
这里只做号、证、卡、门牌地址这四类；健康 / 家庭这类「对陪伴重要」的信息按老板选的
「中等尺度」保留（不滤），但**群聊里不复述**由上层（跨群隔离）保证。
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

LEVELS = ("off", "basic", "smart")
DEFAULT_LEVEL = "basic"
ENV_LEVEL = "ASTROSWARM_MEMORY_LEVEL"

# 档位**热读**（2026-10-11 老板拍板）：以前只读启动时注入的环境变量，
# 界面上把「智能」改成「无」必须重启机器人 —— 现在每次调用现读配置，
# 带 (mtime,size) 缓存所以就是一次 stat，改完立即生效。
# 优先级：热配置快照 → 环境变量（老装机兜底）→ 默认 basic。
_LIVE_LEVEL_KEYS = ("memory_level", "memory_enabled")

# 三档的中文说明（界面/日志共用同一份口径，别各写各的）
LEVEL_LABELS = {"off": "无", "basic": "基础", "smart": "智能"}
LEVEL_HINTS = {
    "off": "无：完全不记也不读，等于回到没有记忆的状态。",
    "basic": "基础：只记事实和日记，每次带上最近 3 条记忆——现在线上就是这一档。",
    "smart": "智能：在基础之上加权重/承诺必带、时间线、印象档合并、隐私过滤与可查可删。",
}

# 注入条数（basic 与 smart 的「最近」都是 3，保证 basic 的线上行为一字不变）
RECENT_LIMIT = 3
IMPORTANT_LIMIT = 3
DIARY_DAYS = 2
RECALL_LIMIT = 10
EXTRACT_MESSAGES = 25
EXTRACT_MAX_FACTS = 3
DIARY_CONTENT_CHARS = 300
FACT_CHARS = 150


# ---------------------------------------------------------------- 档位

def hotconfig():
    """统一的热读小模块（`core/hotconfig.py`）；拿不到返回 None，调用方回退环境变量。

    为什么要有兜底：这个模块会被**复制到机器人目录**（`ensure_agent_runtime`），
    老装机（还没同步过）里可能没有它 —— 那就老规矩读环境变量，不能崩。
    """
    try:
        from .. import hotconfig as _hc          # qbotmanager.core.hotconfig
        return _hc
    except Exception:  # noqa: BLE001 —— 老装机环境没有它，属预期
        pass
    try:
        import hotconfig as _hc                  # 同目录直接加载（极少见）
        return _hc
    except Exception:  # noqa: BLE001
        return None


def level() -> str:
    """当前记忆档位。

    **每次调用现读配置**（带 mtime+size 缓存，等于一次 ``os.stat``），所以
    在界面上把「智能」改成「无」是**立即生效**的，不需要重启机器人。

    优先级：机器人侧有效开关快照 ``memory_level`` → 环境变量
    ``ASTROSWARM_MEMORY_LEVEL``（老装机兜底 / 进程内 ``set_level``）→ ``basic``。
    """
    hc = hotconfig()
    if hc is not None:
        try:
            live_path = hc.live_path()
            # **只有机器人侧真的存在这份快照时才认它**：既保证测试/构建环境
            # （没有快照）行为不变，也避免"文件被删了却一直读缓存里的旧档位"。
            has_live = bool(live_path) and live_path.is_file()
            raw_live = str((hc.read_live() or {}).get("memory_level") or "").strip().lower()
            if has_live and raw_live in LEVELS:
                return raw_live
            live_enabled = (hc.read_live() or {}).get("memory_enabled")
            if has_live and live_enabled is False:
                return "off"
        except Exception:  # noqa: BLE001 —— 热读坏了绝不能拖垮回话，退环境变量
            pass
    raw = str(os.environ.get(ENV_LEVEL) or "").strip().lower()
    return raw if raw in LEVELS else DEFAULT_LEVEL


def set_level(value: str) -> str:
    """改档位（**仅进程内**，给测试与运行期调试用）。

    真正的档位配置在**机器人侧 aichat_manager.json / 有效开关快照**里；
    界面上改它（`core.ai_config.save_memory_level` + `hotconfig.write_live`）
    就是立即生效。这个函数绝不碰任何配置文件（免得测试改了线上设置），
    它只写进程内环境变量 + 清掉快照缓存。
    """
    raw = str(value or "").strip().lower()
    if raw not in LEVELS:
        return level()
    os.environ[ENV_LEVEL] = raw
    hc = hotconfig()
    if hc is not None:
        try:
            hc.invalidate(hc.live_path())     # 别让缓存把刚设的值盖回去
        except Exception:  # noqa: BLE001
            pass
    return level()


def overridden() -> bool:
    """档位是不是被进程内环境变量钉住了（诊断用）。"""
    return str(os.environ.get(ENV_LEVEL) or "").strip().lower() in LEVELS


def reset_override() -> None:
    """取消进程内钉住（测试 teardown / 想让配置文件说话时调）。

    ``set_level`` 写的是 ``os.environ``，所以这里只能**提示**调用方去
    ``os.environ.pop``；为方便测试，本函数顺手把缓存清掉。
    """
    hc = hotconfig()
    if hc is not None:
        try:
            hc.clear()
        except Exception:  # noqa: BLE001
            pass


def enabled(level_name: Optional[str] = None) -> bool:
    return (level_name or level()) != "off"


def is_smart(level_name: Optional[str] = None) -> bool:
    return (level_name or level()) == "smart"


def level_label(value: Optional[str] = None) -> str:
    return LEVEL_LABELS.get(value or level(), "基础")


def level_hint(value: Optional[str] = None) -> str:
    return LEVEL_HINTS.get(value or level(), LEVEL_HINTS[DEFAULT_LEVEL])


# ---------------------------------------------------------------- 隐私过滤

# 中国大陆手机号：1 开头 11 位（前后不能再挨着数字，避免吃掉长号码的一段）
_RE_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# 身份证：18 位（含 X 结尾）或 15 位，前后不挨数字
_RE_IDCARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)|(?<!\d)\d{15}(?!\d)")
# 银行卡：16~19 位纯数字（前后不挨数字）
_RE_BANKCARD = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
# 详细住址：省/市/区 + 路/街/巷 + 号（含「号院」「号楼」「单元」「室」）
_RE_ADDRESS = re.compile(
    r"[\u4e00-\u9fa5]{2,8}(?:省|市|区|县)[\u4e00-\u9fa5]{0,12}"
    r"(?:路|街|道|巷|弄|村|镇|小区|花园|公寓|大厦)[\u4e00-\u9fa5\d]{0,12}"
    r"\d{1,5}\s*(?:号|号楼|栋|单元|室|门)"
)
_RE_ADDRESS_SHORT = re.compile(
    r"[\u4e00-\u9fa5]{2,10}(?:路|街|巷|弄|小区|花园|公寓|大厦)\s*\d{1,5}\s*(?:号|栋|单元|室|门)")

MASK_PHONE = "[已隐去手机号]"
MASK_IDCARD = "[已隐去证件号]"
MASK_BANKCARD = "[已隐去银行卡号]"
MASK_ADDRESS = "[已隐去详细住址]"


def sanitize(text: str) -> Tuple[str, List[str]]:
    """把敏感信息抹掉再入库。返回 (清洗后的文本, 命中的类别)。

    只在这里做一次：**任何**写库路径都要先过它（`__init__.remember()` 已统一调用）。
    """
    raw = str(text or "")
    hits: List[str] = []
    out = raw
    if _RE_PHONE.search(out):
        out = _RE_PHONE.sub(MASK_PHONE, out)
        hits.append("phone")
    if _RE_IDCARD.search(out):
        before = out
        out = _RE_IDCARD.sub(MASK_IDCARD, out)
        if out != before:
            hits.append("idcard")
    if _RE_BANKCARD.search(out):
        before = out
        out = _RE_BANKCARD.sub(MASK_BANKCARD, out)
        if out != before:
            hits.append("bankcard")
    for pattern in (_RE_ADDRESS, _RE_ADDRESS_SHORT):
        if pattern.search(out):
            out = pattern.sub(MASK_ADDRESS, out)
            if "address" not in hits:
                hits.append("address")
    return out, hits


def has_sensitive(text: str) -> bool:
    return bool(sanitize(text)[1])


# ---------------------------------------------------------------- 分级（kind）

_KIND_RULES: List[Tuple[str, Sequence[str]]] = [
    ("promise", ("答应", "承诺", "约好", "说好", "约定", "保证", "下次", "明天要", "记得给",
                 "别忘了", "欠", "要请", "一起去")),
    # 关系在身份之前：「他妈妈住在老家」是家庭信息，别被「住在」抢成 identity
    ("relation", ("妈妈", "爸爸", "父母", "爸", "妈", "女朋友", "男朋友", "对象", "老婆",
                  "老公", "儿子", "女儿", "哥哥", "姐姐", "弟弟", "妹妹", "同学",
                  "室友", "同事", "朋友", "老师")),
    ("identity", ("叫", "名字", "姓名", "网名", "昵称", "生日", "岁", "属", "住在", "老家",
                  "职业", "工作", "上学", "大学", "公司", "专业", "年级")),
    ("dislike", ("讨厌", "不喜欢", "怕", "过敏", "害怕", "烦", "受不了", "忌口", "不能吃")),
    ("like", ("喜欢", "爱", "偏好", "最爱", "爱吃", "爱好", "想玩", "在玩", "常吃", "迷上")),
]


def classify(fact: str, default: str = "event") -> str:
    """按内容给一条事实定 kind（承诺 > 关系 > 身份 > 厌恶 > 喜好 > 事件）。

    这是**保守**的字符串规则，不调用模型：够用、可解释、零成本；
    模型显式给了 kind 时以模型为准（`__init__.remember` 里优先用传入值），
    程序自己合成的条目（如印象档）也必须显式给 kind——别靠这里猜。
    """
    text = str(fact or "")
    if text.startswith("印象："):
        return "impression"
    for kind, words in _KIND_RULES:
        if any(w in text for w in words):
            return kind
    return default


def default_weight(kind: str) -> int:
    return {"promise": 5, "identity": 4, "relation": 4, "dislike": 3,
            "like": 3, "impression": 4, "event": 3}.get(kind, 3)


# ---------------------------------------------------------------- 时间线

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _local(ts: float, tz: Optional[timezone] = None) -> datetime:
    if tz is not None:
        return datetime.fromtimestamp(ts, tz)
    return datetime.fromtimestamp(ts)


def human_gap(seconds: float) -> str:
    """把秒差说成人话：「3 天 4 小时」。"""
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} 天 {hours} 小时" if hours else f"{days} 天"
    if hours:
        return f"{hours} 小时 {minutes} 分" if minutes else f"{hours} 小时"
    if minutes:
        return f"{minutes} 分钟"
    return "刚刚"


def timeline_parts(qq: str, session: Optional[dict] = None,
                   now: Optional[float] = None, tz: Optional[timezone] = None,
                   last_topic: str = "") -> List[str]:
    """时间线的几段（给 `time_line()` 拼；单测直接断言这几段）。"""
    from . import memory_store as store_mod

    now_ts = float(now if now is not None else __import__("time").time())
    dt = _local(now_ts, tz)
    parts: List[str] = []
    session = session or {}

    last = int(session.get("last_ts") or 0)
    if not last:
        try:
            last = store_mod.default_store().last_seen(qq)
        except Exception:  # noqa: BLE001 —— 时间线读不到不该影响回话
            last = 0
    if last and last < now_ts:
        parts.append(f"距上次说话 {human_gap(now_ts - last)}")
    else:
        parts.append("这是他第一次来")

    visits = int(session.get("visits_today") or 0)
    if visits > 0:
        parts.append(f"这是他今天第 {visits} 次来")
    elif session.get("session_started_today"):
        parts.append("这是他今天第 1 次来")

    parts.append(f"现在 {dt.strftime('%H:%M')}（{_WEEKDAYS[dt.weekday()]}）")
    topic = str(last_topic or session.get("last_topic") or "").strip()
    if topic:
        parts.append(f"他上次提到：{topic[:40]}")
    return parts


def time_line(qq: str, session: Optional[dict] = None, now: Optional[float] = None,
              tz: Optional[timezone] = None, last_topic: str = "") -> str:
    """时间线一行：「距上次说话 3 天 4 小时｜今天第 1 次来｜现在 23:40」。

    ``off`` 档返回空串（无档位不产生任何记忆相关输出）。
    """
    if not enabled():
        return ""
    try:
        return "｜".join(timeline_parts(qq, session=session, now=now, tz=tz,
                                        last_topic=last_topic))
    except Exception:  # noqa: BLE001 —— 时间线是加分项，坏了不能拖垮回话
        return ""


# ---------------------------------------------------------------- 注入

def _fmt_memory(row: Dict[str, Any], with_day: bool = False,
                tz: Optional[timezone] = None) -> str:
    # 2026-10-15（审计 E）：渲染侧再兜一层 —— 记忆是拼进**系统提示**的，
    # 换行会让它看起来像"新的系统指令"（持久提示注入）。写入侧已经折过空白，
    # 这里对**老数据**也生效。
    fact = re.sub(r"\s+", " ", str(row.get("fact") or "").strip())[:FACT_CHARS]
    if fact.startswith("印象："):
        return fact
    if not with_day:
        return f"'{fact}'"
    try:
        dt = _local(float(row.get("ts") or 0), tz)
        stamp = f"{dt.month}月{dt.day}日"
    except (TypeError, ValueError, OSError):
        stamp = ""
    return f"'{fact}'（{stamp}记的）" if stamp else f"'{fact}'"


def select_for_injection(store, qq: str, level_name: Optional[str] = None,
                         tz: Optional[timezone] = None) -> Dict[str, Any]:
    """按档位挑出「要注入的记忆」。返回结构给注入块与单测共用。

    * basic：只挑**最近 3 条**（== 线上现行为）；
    * smart：最近 3 条 + 高权重 3 条 + 承诺类必带（去重后按新→旧排）。
    """
    lv = level_name or level()
    out: Dict[str, Any] = {"level": lv, "recent": [], "important": [], "promises": [],
                           "impressions": [], "diary": [], "picked": []}
    if lv == "off":
        return out
    recent = store.recent(qq, RECENT_LIMIT)
    out["recent"] = recent
    picked: List[Dict[str, Any]] = list(recent)
    if lv == "smart":
        impressions = store.recent(qq, 1, kinds=["impression"])
        out["impressions"] = impressions
        important = store.important(qq, IMPORTANT_LIMIT)
        promises = store.promises(qq)
        out["important"] = important
        out["promises"] = promises
        picked = impressions + promises + important + recent
    seen, merged = set(), []
    for row in picked:
        rid = int(row["id"])
        if rid in seen:
            continue
        seen.add(rid)
        merged.append(row)
    merged.sort(key=lambda r: -int(r["id"]))
    out["picked"] = merged
    out["diary"] = store.diary_recent(DIARY_DAYS) if lv in ("basic", "smart") else []
    return out


def injection_block(store, qq: str, session: Optional[dict] = None,
                    now: Optional[float] = None, level_name: Optional[str] = None,
                    tz: Optional[timezone] = None, person: str = "") -> str:
    """拼出要进提示词的那一段文本。``off`` 档返回空串。

    文案与顺序尽量保持线上原样（老插件注入的就是「记忆 + 日记」），
    这样 ``basic`` 档的行为与现在**逐字一致**，换档只是加东西。
    """
    lv = level_name or level()
    if lv == "off":
        return ""
    picked = select_for_injection(store, qq, lv, tz=tz)
    lines: List[str] = []
    who = person or "他"

    if lv == "smart":
        line = time_line(qq, session=session, now=now, tz=tz)
        if line:
            lines.append(line)

    memories = picked["picked"]
    if memories:
        head = "你记得关于{}的事".format(who) if lv == "smart" else "关于{}的记忆".format(who)
        body = "、".join(_fmt_memory(m, with_day=False) for m in memories)
        lines.append(f"{head}：{body}")

    if lv == "smart" and picked["promises"]:
        # 2026-10-16（对抗性复核 E）：这两条老的承诺行以前**绕过渲染侧折叠** ——
        # 老数据里带换行的 fact 会以"多行"形态混进 system 提示（可持久化注入）。
        # 和 _fmt_memory 用同一个收口。
        promises = "、".join(
            "'%s'" % re.sub(r"\s+", " ", str(p["fact"])).strip()[:FACT_CHARS]
            for p in picked["promises"])
        lines.append(f"你答应过{who}的事（绝不能忘）：{promises}")

    diaries = picked["diary"]
    if diaries:
        lines.append("你最近的日记（记得这些事，聊天可以自然带出来）：")
        for row in diaries:
            content = re.sub(r"\s+", " ",
                             str(row.get("content") or "").strip())[:DIARY_CONTENT_CHARS]
            lines.append(f"- {row.get('day')}: {content}")

    if not lines:
        return ""
    if lv == "smart":
        lines.append("（以上是长期记忆，与当前话题无关时可以忽略；不要生硬复述。）")
    return "\n".join(lines)


def wrap_for_prompt(block: str) -> str:
    """把注入块包成 system 消息内容（与线上那条 system 的口径一致）。"""
    if not block:
        return ""
    return "以下是长期记忆（如与当前对话无关可忽略）：\n" + block


# ---------------------------------------------------------------- 事实提炼 / 合并 的判定

def should_extract(level_name: Optional[str] = None) -> bool:
    """off 档：连提炼都不做（不写库）。"""
    return enabled(level_name)


def extract_hint(level_name: Optional[str] = None) -> str:
    """给提炼用的 system 提示词（与线上 liqinghan 那句保持一致）。"""
    lv = level_name or level()
    base = ("负责从聊天记录里提炼值得长期记住的信息。"
            "只提炼：生日、姓名/昵称、职业、学校、爱好、讨厌的事、重大事件、约定承诺。"
            "忽略寒暄和日常废话。每行一条，最多 3 条；没有值得记的就只回复：无")
    if lv == "smart":
        base += "。不要提炼手机号、身份证号、银行卡号、详细住址。若同一件事与之前不同，以最新的为准"
    return base


def should_merge(level_name: Optional[str] = None) -> bool:
    """只有智能档做记忆整理（合并成印象档）。"""
    return is_smart(level_name)
