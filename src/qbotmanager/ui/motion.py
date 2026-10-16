# -*- coding: utf-8 -*-
"""界面动效总闸 + 全软件统一的时长/曲线。

设置 →「减少界面动效」一勾上，这里直接把动效关掉：所有过渡改成"直接落位"，
但**状态信息不丢**（长任务的忙碌点变成静止的三点，不是消失）。

词汇表和既有动效保持一致，避免出现第二套节奏：
  · 按下 90ms / 回弹 170ms（ui/press_scale.py）
  · 进度条平滑 220ms（ui/widgets.py TaskProgressBar）
时长参考 animation-principles：微观交互 100–200ms、面板级 200–400ms。
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve

# ---------------------------------------------------------------- 总闸
ENABLED = True


def enabled() -> bool:
    """当前是否允许做过渡（设置里「减少界面动效」未勾选）。"""
    return bool(ENABLED)


def set_enabled(on: bool) -> None:
    """由设置页 / 主窗口调用；立刻生效，不需要重启。"""
    global ENABLED
    ENABLED = bool(on)


# ---------------------------------------------------------------- 时长（毫秒）
# 数值依据：LottieFiles motion-design 技能的「元素时长表 / 交互反馈延迟表」。
# 性格定型 = Premium 曲线 × Corporate 节奏（见 workspace\动效规范-星群桌面端-2026-10-10.md）。
DUR_MICRO = 120         # 悬停 / 聚焦 / 状态点（技能：微反馈 80–120ms）
DUR_PRESS = 90          # 按下（跟手优先，<150ms）
DUR_RELEASE = 220       # 松开回弹（技能：释放 / 落位 200–300ms）
DUR_STANDARD = 240      # 卡片 / 面板 / 图标切换（技能：200–350ms）
DUR_OVERLAY = 320       # 弹窗 / 抽屉（技能：300–400ms）
DUR_PAGE = 200          # 桌面端页面切换（桌内切页按面板级；整站换页才用 400–600ms）
DUR_REVEAL = 950        # 启动动画（技能：戏剧性揭示 600–1200ms）
DUR_REVEAL_OUT = 620    # 退出动画 = 进入的 ~0.65–0.75（技能硬规则：退场比进场短）
DUR_COLLAPSE = 130      # 收起 / 退出（沿用旧值，折叠区还有别处在用）
DUR_TOAST_IN = 200      # 提示条进入
DUR_TOAST_OUT = 140     # 提示条退出
DUR_PROGRESS = 220      # 进度条平滑（与 widgets 里一致）
DUR_BUSY_LOOP = 1000    # 忙碌指示一个循环（连续循环，匀速）
STAGGER_ITEM = 30       # 逐项错峰（技能：单次错峰 20–40ms，总时长必须 <500ms）
STAGGER_BUDGET = 400    # 一次错峰动画的总预算上限

# ---------------------------------------------------------------- 曲线
# 进入用 ease-out（快起慢落），退出用 ease-in（慢起快走），循环才用 linear。
EASE_IN = QEasingCurve.Type.InCubic
EASE_OUT = QEasingCurve.Type.OutCubic
EASE_IN_OUT = QEasingCurve.Type.InOutSine
LINEAR = QEasingCurve.Type.Linear


def duration(ms: int) -> int:
    """关掉动效时统一返回 0，动画类可以照常构造但会立刻落到终点。"""
    return int(ms) if ENABLED else 0
