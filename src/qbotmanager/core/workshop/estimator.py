# -*- coding: utf-8 -*-
"""工坊开工前估算：工程量 + token 消耗（防止用户花了钱才后悔）。

2026-10-13 老板要的：点「生成插件」之前先说清"这是多大的活、大概要烧多少 token"，
生成完再报一次**实际**消耗（generator 会把服务商返回的 usage 累加起来）。

粗算口径（刻意保守，宁可高估）：
- token ≈ 字符数 ÷ 1.6（中英混合的粗换算；中文差不多 1 字 0.7~1 token）；
- 调用次数：方案 1 次；代码 1~N 次（小插件整包一次写成；大插件分文件：
  manifest 1 次 + 每个工具 1 次）；自动重写最多 2 次；
- "预期重写次数"按复杂度折算：小 0.3 / 中 0.8 / 大 1.3（只进 min/max 范围与期望值）；
- 价格表只是**参考价**（服务商随时会调，以账单为准）；不认识的模型只报 token。

这个模块不联网、不读文件之外的东西，纯估算 —— 估算失败也绝不能挡住生成（调用方兜底）。
"""
from __future__ import annotations

# 与 generator.CHUNK_TOOLS_THRESHOLD 必须一致（有测试钉着）：
# 工具数 ≥ 这个值 → 走"分文件写"模式
CHUNK_TOOLS_THRESHOLD = 3

# 工程量档位：行数范围 / 工具数范围
_LEVELS = {
    "small": {"label": "小工具", "lines": (60, 160)},
    "medium": {"label": "中等", "lines": (150, 400)},
    "large": {"label": "偏大", "lines": (300, 700)},
}

# 参考价（元 / 百万 token，输入价, 输出价）：2026-10 口径，仅用于给用户一个数量级
_PRICES = {
    "deepseek": (2.0, 8.0),
    "openai": (10.0, 60.0),
    "anthropic": (21.0, 105.0),
    "zhipu": (4.0, 16.0),
    "dashscope": (3.0, 12.0),
    "moonshot": (4.0, 16.0),
}

_MULTI_KEYWORDS = ("排行", "统计", "记账", "金币", "签到", "积分", "多用户",
                   "历史", "记录", "储存", "存档", "抓取", "解析", "采集", "定时")


def _tok(chars: int) -> int:
    """字符数 → token 的粗换算。"""
    return int(max(0, chars) / 1.6) + 1


def _prompts():
    from . import prompts as _p

    return _p


def classify(need: str, plan: dict | None = None) -> str:
    """粗判工程量档位：small / medium / large。"""
    need = str(need or "")
    if isinstance(plan, dict) and isinstance(plan.get("tools"), list) and plan["tools"]:
        tools = len([t for t in plan["tools"] if isinstance(t, dict) and t.get("name")])
    else:
        tools = 1
        if any(k in need for k in _MULTI_KEYWORDS):
            tools += 1
        if len(need) >= 60:
            tools += 1
    tools = max(1, min(5, tools))
    if tools >= 3:
        return "large"
    if tools == 2:
        return "medium"
    return "small"


def _tools_count(need: str, plan: dict | None) -> int:
    if isinstance(plan, dict) and isinstance(plan.get("tools"), list) and plan["tools"]:
        n = len([t for t in plan["tools"] if isinstance(t, dict) and t.get("name")])
        return max(1, min(5, n))
    level = classify(need, plan)
    return {"small": 1, "medium": 2, "large": 3}[level]


def estimate(settings, need: str, plan: dict | None = None) -> dict:
    """估算这次生成要花多少（工程量 + token 范围 + 参考费用）。绝不抛异常。"""
    try:
        level = classify(need, plan)
        tools = _tools_count(need, plan)
        lines_lo, lines_hi = _LEVELS[level]["lines"]
        mode = "chunked" if tools >= CHUNK_TOOLS_THRESHOLD else "single"
        p = _prompts()

        # 每次"代码调用"的输入 ≈ 系统提示词 + 需求/方案；输出 ≈ 行数 × 每行 token
        sys_chars = len(getattr(p, "CODE_SYSTEM", "")) + len(str(need)) + 1200
        sys_tok = _tok(sys_chars)
        out_lo = int(lines_lo * 11) + 300
        out_hi = int(lines_hi * 11) + 300

        # 调用次数：方案 1 + 代码（single 1 次 / chunked 1+工具数个）+ 重写 0~2 轮
        code_calls_lo = 1 if mode == "single" else 1 + tools
        calls_lo = 1 + code_calls_lo
        calls_hi = calls_lo + 2 * (1 if mode == "single" else 1 + tools)

        plan_in = _tok(len(getattr(p, "PLAN_SYSTEM", "")) + len(str(need)) + 800)
        code_in_lo = sys_tok * code_calls_lo
        code_in_hi = sys_tok * code_calls_lo
        # 重写轮：整包模式要把原文件回灌（输入翻倍级）；分文件模式只重发问题文件
        repair_in = (code_in_lo + out_hi) if mode == "single" else int(sys_tok * tools * 0.8)
        repair_out = out_hi if mode == "single" else int(out_hi * 0.8)
        expect = {"small": 0.3, "medium": 0.8, "large": 1.3}[level]

        in_est = plan_in + code_in_lo + int(repair_in * expect)
        out_est = int((out_lo + out_hi) / 2) + int(repair_out * expect)
        in_min, in_max = plan_in + code_in_lo, plan_in + code_in_hi + repair_in * 2
        out_min, out_max = out_lo + 200, out_hi + repair_out * 2

        money = None
        try:
            from . import config as ws_config

            provider = str((ws_config.resolved(settings) or {}).get("provider") or "")
            price = _PRICES.get(provider)
            if price:
                pin, pout = price

                def _yuan(tin, tout):
                    return round(tin / 1e6 * pin + tout / 1e6 * pout, 3)

                money = {
                    "currency": "CNY",
                    "min": _yuan(in_min, out_min),
                    "max": _yuan(in_max, out_max),
                    "expected": _yuan(in_est, out_est),
                    "note": "参考价（服务商随时会调，以账单为准）",
                }
        except Exception:  # noqa: BLE001 —— 价格算不出来就算了，token 照报
            money = None

        return {
            "level": level,
            "level_label": _LEVELS[level]["label"],
            "tools": tools,
            "files": tools + 1,
            "lines": [lines_lo, lines_hi],
            "mode": mode,
            "calls": [calls_lo, calls_hi],
            "tokens": {
                "input": [int(in_min), int(in_max)],
                "output": [int(out_min), int(out_max)],
                "total": [int(in_min + out_min), int(in_max + out_max)],
                "expected": int(in_est + out_est),
            },
            "money": money,
        }
    except Exception as e:  # noqa: BLE001 —— 估算失败不能挡住生成
        return {"level": "small", "level_label": "小工具", "tools": 1, "files": 2,
                "lines": [60, 160], "mode": "single", "calls": [2, 6],
                "tokens": {"input": [3000, 20000], "output": [800, 8000],
                           "total": [3800, 28000], "expected": 12000},
                "money": None, "error": str(e)[:120]}


def _fmt_k(n: int) -> str:
    """12000 → 1.2万；4580 → 4580。"""
    n = int(n or 0)
    if n >= 10000:
        return "%.1f万" % (n / 10000.0)
    return str(n)


def format_estimate(est: dict, short: bool = False) -> str:
    """给人看的一句话（short=True）或多行（默认，用于确认弹窗）。"""
    est = est or {}
    label = est.get("level_label") or "小工具"
    tools = est.get("tools") or 1
    lo, hi = (est.get("lines") or [60, 160])[:2]
    tk = est.get("tokens") or {}
    total = tk.get("total") or [0, 0]
    money = est.get("money") or {}
    mode = est.get("mode") or "single"
    mode_text = "分文件写（大工程更稳）" if mode == "chunked" else "一次写成"

    if short:
        tip = f"工程量{label}（约 {lo}~{hi} 行），预计消耗 {_fmt_k(total[0])}~{_fmt_k(total[1])} tokens"
        if money:
            tip += "，费用约 ¥%.2f~%.2f（参考价）" % (money.get("min", 0), money.get("max", 0))
        return tip

    lines = [
        f"工程量：{label} —— {tools} 个工具 · 约 {est.get('files', tools + 1)} 个文件 / "
        f"{lo}~{hi} 行代码",
        f"生成方式：{mode_text}（输出被截断会自动改成分文件写）",
        f"预计消耗：{_fmt_k(total[0])} ~ {_fmt_k(total[1])} tokens"
        f"（输入 {_fmt_k((tk.get('input') or [0, 0])[0])}~{_fmt_k((tk.get('input') or [0, 0])[1])}"
        f" / 输出 {_fmt_k((tk.get('output') or [0, 0])[0])}~{_fmt_k((tk.get('output') or [0, 0])[1])}）",
    ]
    if money:
        lines.append("费用约：¥%.2f ~ ¥%.2f（%s）"
                     % (money.get("min", 0), money.get("max", 0),
                        money.get("note") or "参考价"))
    else:
        lines.append("费用：工坊模型没配价格表里的服务商，按你的服务商账单算")
    lines.append("说明：包含出方案 1 次、写代码，以及最多 2 次自动重写；沙箱试跑不消耗 token。")
    return "\n".join(lines)


def usage_line(usage: dict, est: dict | None = None) -> str:
    """生成完的"实际消耗"一句话（金额按服务商参考价粗算）。"""
    usage = usage or {}
    tin = int(usage.get("input") or 0)
    tout = int(usage.get("output") or 0)
    calls = int(usage.get("calls") or 0)
    if not (tin or tout):
        return ""
    line = (f"本次实际消耗：{_fmt_k(tin)} 输入 + {_fmt_k(tout)} 输出 = "
            f"{_fmt_k(tin + tout)} tokens（{calls} 次调用）")
    money = (est or {}).get("money") or {}
    try:
        if money:
            provider = ""  # 用 est 里的参考单价（已经是该服务商的）
            # est.money 是范围值：用比例法把实际 tokens 折进同一参考价
            lo, hi = (est.get("tokens") or {}).get("total") or [0, 0]
            if hi > lo:
                share = min(1.0, max(0.0, (tin + tout - lo) / float(hi - lo)))
                yuan = money.get("min", 0) + share * (money.get("max", 0) - money.get("min", 0))
                line += "，约 ¥%.2f（参考价）" % yuan
    except Exception:  # noqa: BLE001
        pass
    return line
