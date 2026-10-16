"""AstroSwarm 星群视觉主题：颜色令牌 + 全局 QSS。克制、深色、玻璃质感。"""

# ---- 颜色令牌（单一强调色：深蓝）----
ACCENT = "#3D6BFF"
ACCENT_HOVER = "#5A85FF"
ACCENT_ACTIVE = "#2F5CE8"
ACCENT_SOFT = "rgba(61,107,255,0.16)"
ACCENT_GLOW = "rgba(61,107,255,0.45)"

BG_0 = "#0B0E14"
BG_1 = "#111722"
PANEL = "rgba(17,22,34,0.62)"
PANEL_STRONG = "rgba(19,26,40,0.78)"
PANEL_SOLID = "#141A26"
BORDER_LIGHT = "rgba(255,255,255,0.08)"
BORDER_STRONG = "rgba(255,255,255,0.14)"

TEXT = "#F2F5FA"
TEXT_2 = "#A7B1C2"
TEXT_3 = "#7E8A9C"

SUCCESS = "#34D399"
DANGER = "#F87171"
WARN = "#FBBF24"
INFO = "#60A5FA"
NEUTRAL = "#8A94A6"

RADIUS = 12
RADIUS_SM = 8
WINDOW_RADIUS = 14       # 主窗口四角圆角半径（平滑圆角，非区域遮罩）

FONT_FAMILY = '"Microsoft YaHei UI", "Segoe UI", sans-serif'
MONO_FAMILY = '"Cascadia Mono", Consolas, monospace'
# 昵称/签名用的手写体（与官网银行卡卡背签名同款；字体没装上时会自动跳到后面的兜底）
HAND_FAMILY = ('"Dancing Script", "Liu Jian Mao Cao", "Segoe UI Emoji", '
               '"Segoe Script", "Bradley Hand", "STXingkai", "KaiTi", "楷体"')

STATUS_COLORS = {
    "running": SUCCESS,
    "connected": SUCCESS,
    "success": SUCCESS,
    "ok": SUCCESS,
    "stopped": DANGER,
    "failed": DANGER,
    "error": DANGER,
    "warning": WARN,
    "warn": WARN,
    "pending": WARN,
    "info": INFO,
    "unknown": NEUTRAL,
    "idle": NEUTRAL,
}

# ---- 皮肤主题（多套强调色） ----
THEMES = {
    "default": {
        "name": "深邃蓝",
        "ACCENT": "#3D6BFF",
        "ACCENT_HOVER": "#5A85FF",
        "ACCENT_ACTIVE": "#2F5CE8",
        "ACCENT_SOFT": "rgba(61,107,255,0.16)",
        "ACCENT_GLOW": "rgba(61,107,255,0.45)",
    },
    "midnight": {
        "name": "暗夜紫",
        "ACCENT": "#8B5CF6",
        "ACCENT_HOVER": "#A78BFA",
        "ACCENT_ACTIVE": "#7C3AED",
        "ACCENT_SOFT": "rgba(139,92,246,0.18)",
        "ACCENT_GLOW": "rgba(139,92,246,0.45)",
    },
    "cyber": {
        "name": "赛博青",
        "ACCENT": "#22D3EE",
        "ACCENT_HOVER": "#67E8F9",
        "ACCENT_ACTIVE": "#0891B2",
        "ACCENT_SOFT": "rgba(34,211,238,0.15)",
        "ACCENT_GLOW": "rgba(34,211,238,0.40)",
    },
    "sunset": {
        "name": "暖阳橙",
        "ACCENT": "#F59E0B",
        "ACCENT_HOVER": "#FBBF24",
        "ACCENT_ACTIVE": "#D97706",
        "ACCENT_SOFT": "rgba(245,158,11,0.15)",
        "ACCENT_GLOW": "rgba(245,158,11,0.40)",
    },
    "forest": {
        "name": "薄荷绿",
        "ACCENT": "#34D399",
        "ACCENT_HOVER": "#6EE7B7",
        "ACCENT_ACTIVE": "#059669",
        "ACCENT_SOFT": "rgba(52,211,153,0.15)",
        "ACCENT_GLOW": "rgba(52,211,153,0.40)",
    },
    "slate": {
        "name": "深空绿（新色板）",
        "ACCENT": "#22C55E",
        "ACCENT_HOVER": "#4ADE80",
        "ACCENT_ACTIVE": "#16A34A",
        "ACCENT_SOFT": "rgba(34,197,94,0.16)",
        "ACCENT_GLOW": "rgba(34,197,94,0.42)",
        "TEXT": "#F8FAFC",
        "TEXT_2": "#94A3B8",
        "TEXT_3": "#7E8A9C",
    },
}

# ---- UI 框玻璃质感（default=原样 / liquid=液态玻璃 / frosted=颗粒磨砂） ----
GLASS = {
    "default": {
        "PANEL": PANEL,
        "PANEL_STRONG": PANEL_STRONG,
        "PANEL_SOLID": PANEL_SOLID,
        "BORDER_LIGHT": BORDER_LIGHT,
        "BORDER_STRONG": BORDER_STRONG,
    },
    "liquid": {
        "PANEL": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 rgba(255,255,255,0.14), "
                 "stop:0.45 rgba(17,22,34,0.42), stop:1 rgba(10,14,22,0.60))",
        "PANEL_STRONG": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 rgba(255,255,255,0.17), "
                        "stop:0.45 rgba(19,26,40,0.52), stop:1 rgba(10,14,22,0.74))",
        "PANEL_SOLID": "#141A26",
        "BORDER_LIGHT": "rgba(255,255,255,0.18)",
        "BORDER_STRONG": "rgba(255,255,255,0.32)",
    },
    "frosted": {
        "PANEL": "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(255,255,255,0.10), "
                 "stop:0.5 rgba(17,22,34,0.62), stop:1 rgba(255,255,255,0.06))",
        "PANEL_STRONG": "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(255,255,255,0.12), "
                        "stop:0.5 rgba(19,26,40,0.74), stop:1 rgba(255,255,255,0.08))",
        "PANEL_SOLID": "#151B27",
        "BORDER_LIGHT": "rgba(255,255,255,0.10)",
        "BORDER_STRONG": "rgba(255,255,255,0.16)",
    },
}

CURRENT_GLASS = "default"  # GlassPanel 自绘时读取的当前质感模式
UI_OPACITY = 0.70          # UI 框透明度（GlassPanel 自绘读取，0.0-1.0）
SUPPRESS_BACKDROP_REFRESH = False  # 主题切换期间暂停玻璃面板抓屏，切完统一刷新

# ---- UI 主题：现在只有「极光玻璃」一套（布局 + 导航 + 配色 + 质感） ----
UI_THEMES = {
    "glass": {"name": "极光玻璃", "style": "glass", "layout": "sidebar"},
}

TOKEN_KEYS = (
    "ACCENT", "ACCENT_HOVER", "ACCENT_ACTIVE", "ACCENT_SOFT", "ACCENT_GLOW",
    "BG_0", "BG_1", "TEXT", "TEXT_2", "TEXT_3",
    "PANEL", "PANEL_STRONG", "PANEL_SOLID", "BORDER_LIGHT", "BORDER_STRONG",
)

_QSS_CACHE: dict = {}


def status_color(key: str) -> str:
    return STATUS_COLORS.get((key or "").lower(), NEUTRAL)


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _build_qss_inner() -> str:
    return f"""
* {{
    font-family: {FONT_FAMILY};
    outline: none;
}}
/* 左上角账号胶囊的昵称：银行卡卡背签名同款手写体。
   为什么写在这里而不是给按钮 setStyleSheet：
   ① 写法必须带按钮选择器 —— 直接 `* {{}}` 写进控件样式表会连控件本身一起盖掉；
   ② 也不能用 setFont —— 上面那条 `* {{ font-family }}` 优先级更高，会把 setFont 压掉；
   ③ 更不能给按钮挂控件级样式表：Qt 的气泡（tooltip）是按钮的子控件，
      会继承那条 font-family，昵称的手写体就跑到气泡上去了（2026-10-06 用户反馈"气泡字看不清"）。
   放在主窗口样式表里、只对这一个按钮生效，气泡不受影响。 */
QPushButton[class="accountPill"] {{
    font-family: {HAND_FAMILY};
    font-size: 20px;
    font-weight: 500;
}}
QPushButton:focus {{
    border-color: {ACCENT};
}}
QListWidget:focus {{
    border-color: {ACCENT};
}}
QCheckBox:focus::indicator {{
    border-color: {ACCENT};
}}
QMainWindow, QWidget {{
    background: transparent;
    color: {TEXT};
}}
QLabel {{
    background: transparent;
    color: {TEXT};
}}
QLabel#pageTitle {{
    font-size: 22px;
    font-weight: 600;
    color: {TEXT};
    letter-spacing: 0.5px;
}}
QLabel#pageSub {{
    font-size: 13px;
    color: {TEXT_2};
}}
QLabel#sectionTitle {{
    font-size: 15px;
    font-weight: 600;
    color: {TEXT};
}}
QLabel#caption {{
    font-size: 12px;
    color: {TEXT_3};
}}
QLabel#value {{
    font-size: 14px;
    font-weight: 600;
    color: {TEXT};
}}

/* ---- 玻璃面板 ---- */
QFrame#glass {{
    background: {PANEL};
    border: 1px solid {BORDER_LIGHT};
    border-top: 1px solid {BORDER_STRONG};
    border-radius: {RADIUS}px;
}}
QFrame#glassStrong {{
    background: {PANEL_STRONG};
    border: 1px solid {BORDER_LIGHT};
    border-top: 1px solid {BORDER_STRONG};
    border-radius: {RADIUS}px;
}}

/* ---- 按钮 ---- */
QPushButton {{
    background: rgba(255,255,255,0.06);
    border: 1px solid {BORDER_LIGHT};
    border-radius: 8px;
    color: {TEXT};
    padding: 7px 14px;
    font-size: 13px;
}}
QPushButton:hover {{
    background: rgba(255,255,255,0.10);
    border-color: {BORDER_STRONG};
}}
QPushButton:pressed {{
    background: rgba(255,255,255,0.05);
}}
QPushButton:disabled {{
    color: {TEXT_3};
    background: rgba(255,255,255,0.03);
    border-color: rgba(255,255,255,0.04);
}}
QPushButton#primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_ACTIVE});
    border: none;
    color: white;
    font-weight: 600;
    padding: 8px 18px;
}}
QPushButton#primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT_HOVER}, stop:1 {ACCENT});
}}
QPushButton#primary:disabled {{
    background: {rgba(ACCENT, 0.35)};
    color: rgba(255,255,255,0.7);
}}
QPushButton#danger {{
    background: rgba(248,113,113,0.12);
    border: 1px solid rgba(248,113,113,0.35);
    color: #FCA5A5;
}}
QPushButton#danger:hover {{
    background: rgba(248,113,113,0.22);
}}
QPushButton#ghost {{
    background: transparent;
    border: 1px solid {BORDER_LIGHT};
}}
QPushButton#ghost:hover {{
    background: rgba(255,255,255,0.06);
}}
QPushButton.winbtn {{
    background: transparent;
    border: none;
    border-radius: 0;
    color: {TEXT_2};
    font-size: 14px;
    padding: 0 14px;
    min-width: 40px;
    min-height: 36px;
}}
QPushButton.winbtn:hover {{
    background: rgba(255,255,255,0.10);
    color: {TEXT};
}}
QPushButton.winbtn#winClose:hover {{
    background: #E81123;
    color: white;
}}
/* 标题栏更新按钮：红色胶囊「有新版本」，只在发现新版本时显示 */
QPushButton.winbtn#winUpdate {{
    padding: 0 10px;
    margin: 0;
    border-radius: 13px;
    font-size: 12px;
    font-weight: 600;
    color: #FFFFFF;
    background: #E5484D;
}}
QPushButton.winbtn#winUpdate:hover {{
    background: #F0575C;
}}
QPushButton.winbtn#winUpdate:pressed {{
    background: #C93C41;
}}
QPushButton.winbtn#winUpdate[state="busy"] {{
    background: rgba(255,255,255,0.10);
    color: {TEXT_2};
    font-weight: 400;
}}
QPushButton.winbtn#winUpdate[state="ready"] {{
    background: #2FA84F;
}}
QPushButton.winbtn#winUpdate[state="ready"]:hover {{
    background: #37BC5C;
}}

/* 侧边栏左上角账号入口：圆头像 + 昵称，未登录是默认头像 + 待登录 */
QPushButton.accountPill,
QPushButton#ghost[class="accountPill"] {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 27px;
    padding: 0 12px;
    text-align: left;
    font-size: 15px;
    color: {TEXT};
}}
QPushButton.accountPill:hover,
QPushButton#ghost[class="accountPill"]:hover {{
    background: rgba(255,255,255,0.08);
    border-color: {BORDER_LIGHT};
}}
QPushButton.accountPill:pressed,
QPushButton#ghost[class="accountPill"]:pressed {{
    background: {rgba(ACCENT, 0.18)};
}}

/* ---- 输入 ---- */
QLineEdit, QComboBox {{
    background: rgba(10,14,22,0.6);
    border: 1px solid {BORDER_LIGHT};
    border-radius: 8px;
    color: {TEXT};
    padding: 7px 10px;
    font-size: 13px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_2};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {PANEL_SOLID};
    border: 1px solid {BORDER_LIGHT};
    border-radius: 8px;
    color: {TEXT};
    selection-background-color: {ACCENT_SOFT};
}}

/* ---- 进度条 ---- */
QProgressBar {{
    background: rgba(255,255,255,0.06);
    border: none;
    border-radius: 5px;
    min-height: 8px;
    max-height: 8px;
    text-align: center;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_HOVER});
    border-radius: 5px;
}}

/* ---- 日志 ---- */
QPlainTextEdit#logView {{
    background: rgba(9,12,19,0.62);
    border: 1px solid {BORDER_LIGHT};
    border-radius: 10px;
    color: #C9D4E4;
    font-family: {MONO_FAMILY};
    font-size: 12px;
    padding: 8px;
    selection-background-color: rgba(61,107,255,0.4);
}}
QPlainTextEdit {{
    background: rgba(9,12,19,0.62);
    border: 1px solid {BORDER_LIGHT};
    border-radius: 10px;
    color: {TEXT};
    font-family: {MONO_FAMILY};
    font-size: 12px;
    padding: 6px;
}}

/* ---- 滚动条 ---- */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: rgba(255,255,255,0.16);
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: rgba(255,255,255,0.28);
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: rgba(255,255,255,0.16);
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0; height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* ---- 其他 ---- */
QCheckBox {{
    color: {TEXT_2};
    font-size: 13px;
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border-radius: 4px;
    /* 未选中也要看得见：以前边框太暗、底色 4% 白，深色背景上像"没有勾选框" */
    border: 1px solid rgba(255,255,255,0.34);
    background: rgba(255,255,255,0.10);
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: rgba(255,255,255,0.10);
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 14px; height: 14px;
    margin: -5px 0;
    border-radius: 7px;
    background: {ACCENT};
    border: 2px solid #FFFFFF;
}}
QToolTip {{
    background: {PANEL_SOLID};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 5px 8px;
    /* 气泡固定用宋体（2026-10-06 用户要求）：常规印刷体最清楚；
       以前没指定，气泡会跟着按钮的样式表变成手写体，又细又飘。 */
    font-family: "SimSun", "宋体", "Microsoft YaHei", "微软雅黑";
    font-size: 13px;
}}
QMessageBox {{
    background: {PANEL_SOLID};
}}
QMenu {{
    background: {PANEL_SOLID};
    color: {TEXT};
    border: 1px solid {BORDER_LIGHT};
    border-radius: 8px;
}}
QMenu::item:selected {{
    background: {ACCENT_SOFT};
}}

/* ---- 侧边栏 ---- */
QListWidget#sidebar {{
    background: rgba(10,14,22,0.55);
    border: none;
    padding: 10px 8px;
    font-size: 14px;
    color: {TEXT_2};
    outline: none;
}}
QListWidget#sidebar::item {{
    border-radius: 8px;
    padding: 10px 14px;
    margin: 2px 0;
}}
QListWidget#sidebar::item:hover {{
    background: rgba(255,255,255,0.06);
    color: {TEXT};
}}
QListWidget#sidebar::item:selected {{
    background: {ACCENT_SOFT};
    color: white;
    font-weight: 600;
}}
QLabel#sidebarGroupTitle {{
    font-size: 16px;
    font-weight: 800;
    color: {TEXT_2};
    padding-left: 12px;
    padding-top: 6px;
}}

/* ---- 普通列表 ---- */
QListWidget {{
    /* 列表是文字最密的地方：底色必须压得住，否则叠在玻璃上会发糊 */
    background: rgba(10,14,22,0.74);
    border: 1px solid {BORDER_LIGHT};
    border-radius: 8px;
    color: {TEXT};
    font-size: 13px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    border-radius: 6px;
    padding: 6px 8px;
    margin: 1px;
}}
QListWidget::item:hover {{
    background: rgba(255,255,255,0.05);
}}
QListWidget::item:selected {{
    background: {ACCENT_SOFT};
    color: white;
}}

/* ---- 标题栏 ---- */
QFrame#titlebar {{
    background: rgba(10,13,20,0.72);
    border-bottom: 1px solid {BORDER_LIGHT};
    border-top-left-radius: {WINDOW_RADIUS}px;
    border-top-right-radius: {WINDOW_RADIUS}px;
}}
QFrame#titlebar[corner="square"] {{
    border-top-left-radius: 0px;
    border-top-right-radius: 0px;
}}
QLabel#appTitle {{
    font-size: 14px;
    font-weight: 600;
    color: {TEXT};
    letter-spacing: 0.5px;
}}
QLabel#appSub {{
    font-size: 11px;
    color: {TEXT_3};
}}

/* ---- Toast ---- */
QLabel#toast {{
    background: rgba(19,26,40,0.92);
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 16px;
    padding: 8px 18px;
    font-size: 13px;
}}

/* ---- 日志等只读文本框 ---- */
QPlainTextEdit {{
    selection-background-color: {ACCENT};
}}
"""


def build_qss(theme_key: str = "glass", glass: str = "default", accent: str = "default") -> str:
    """生成全局 QSS（只有极光玻璃一套：accent 选强调色，glass 选框质感）。"""
    key = (theme_key, glass, accent)
    cached = _QSS_CACHE.get(key)
    if cached is not None:
        return cached
    saved = {k: globals()[k] for k in TOKEN_KEYS}
    try:
        color_cfg = THEMES.get(accent, THEMES["default"])
        for k in TOKEN_KEYS:
            if k in color_cfg:
                globals()[k] = color_cfg[k]
        glass_cfg = GLASS.get(glass, GLASS["default"])
        for k in TOKEN_KEYS:
            if k in glass_cfg:
                globals()[k] = glass_cfg[k]
        text = _build_qss_inner()
        _QSS_CACHE[key] = text
        return text
    finally:
        for k, val in saved.items():
            globals()[k] = val
