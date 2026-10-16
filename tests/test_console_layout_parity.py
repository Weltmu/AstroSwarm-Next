# -*- coding: utf-8 -*-
"""版式对齐闸门：桌面端有的**区块标题**，控制台也要有（同一套说法）。

功能对齐看 `test_console_parity_matrix.py`；这条管"看着像不像同一个软件"：
老板 2026-10-04 看截图说"ui没对齐"——功能都在，但首页缺平台四卡、标题叫"运行数据"
而不是"今日数据"、全局管理缺 DSH 智能体，一眼就不像。

做法很直接：列一批关键标题，两侧的源码里都要能找到（桌面端 PySide6、控制台 JSX）。
新增桌面区块时把它加进这里，控制台就跟着有活干。
"""
from pathlib import Path

# PNG 尺寸用标准库读：`.test_venv` 里没有 Pillow，硬 import PIL 会在收集阶段就报错。
from _png_meta import png_size

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "src" / "qbotmanager" / "ui" / "pages"
CONSOLE = Path(r"D:\ai\AstroSwarm-public\console\src")
CONSOLE_ROOT = Path(r"D:\ai\AstroSwarm-public\console")
API = ROOT / "src" / "astroswarm_linux" / "api.py"

# 页面 → 两侧都该出现的标题（用桌面端的说法为准）
REQUIRED_TITLES = {
    "首页": {
        "desktop": "home_page.py",
        "console": ("App.jsx",),
        "titles": ["今日数据", "最近消息", "当前任务", "去做插件", "启动全部", "停止全部"],
    },
    "接入": {
        "desktop": "access_page.py",
        "console": ("App.jsx",),
        "titles": ["想让机器人在哪个平台说话"],
    },
    "插件": {
        "desktop": "plugins_page.py",
        "console": ("App.jsx", "MarketPanel.jsx"),
        "titles": ["插件商店", "已装能力包", "在线插件市场"],
    },
    "AI 大脑": {
        "desktop": "brain_page.py",
        "console": ("App.jsx",),
        # 人设三块合并成一张卡片 + 胶囊切换（预设/自定义），两侧用词必须一字不差：
        # 这段是 2026-10-07 老板要求"整合到一个框里、做个胶囊切换"后加的锁。
        "titles": ["接口配置", "平台启用", "获取模型",
                   "人设与主动聊天", "预设", "自定义", "当前生效", "还没有下载档案"],
    },
    "全局管理": {
        "desktop": "services_page.py",
        "console": ("App.jsx",),
        "titles": ["日志保留", "DSH 智能体"],
    },
    "消息中心": {
        "desktop": "message_center_page.py",
        "console": ("App.jsx",),
        "titles": ["消息中心", "刷新"],
    },
    "日志": {
        "desktop": "logs_page.py",
        "console": ("App.jsx",),
        # 桌面端叫「导出日志」，控制台叫「下载日志文件」——同一个功能，按各自说法各列一个
        "titles": ["日志", "清空视图"],
    },
}

# 平台四卡：桌面端首页四张卡，控制台首页也要有这四个词
PLATFORM_WORDS = ("QQ", "微信", "飞书", "纸飞机")


def _desktop_text(filename: str) -> str:
    return (DESKTOP / filename).read_text(encoding="utf-8", errors="replace")


def _console_text(files: tuple) -> str:
    parts = []
    for name in files:
        path = CONSOLE / name
        assert path.exists(), f"控制台文件不存在：{path}"
        parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def test_key_block_titles_exist_on_both_sides():
    missing = {}
    for page, spec in REQUIRED_TITLES.items():
        desk = _desktop_text(spec["desktop"])
        cons = _console_text(spec["console"])
        for title in spec["titles"]:
            gaps = []
            if title not in desk:
                gaps.append("桌面端")
            if title not in cons:
                gaps.append("控制台")
            if gaps:
                missing[f"{page}·{title}"] = gaps
    assert not missing, "这些区块标题两侧不一致（缺：谁）：" + repr(missing)


def test_home_shows_platform_cards_on_both_sides():
    """首页要有平台四卡 —— 老板截图里最显眼的差别就是控制台缺这四张。"""
    desk = _desktop_text("home_page.py")
    cons = _console_text(("App.jsx",))
    for word in PLATFORM_WORDS:
        assert word in desk, f"桌面端首页应有 {word} 卡"
        assert word in cons, f"控制台首页缺 {word} 卡"


def test_beginner_mode_is_a_server_setting_not_browser_only():
    """小白模式必须是服务器配置（桌面端在 settings.json），不能只存浏览器 localStorage。

    两边存的地方不同 → 老板桌面端关了、控制台还开着，侧栏一个 10 项一个 6 项，
    看着就是"没对齐"。
    """
    headless = (ROOT / "src" / "astroswarm_linux" / "headless_config.py").read_text(encoding="utf-8")
    assert '"beginner_mode": True' in headless, "无头配置要有 beginner_mode 默认值"
    assert '"beginner_mode": bool' in headless, "要进 SAFE_KEYS 才能被 /api/config 读写"
    cons = _console_text(("App.jsx",))
    assert "/api/config" in cons, "控制台要能从服务器读配置"
    assert "beginner_mode" in cons, "控制台要读写服务器的 beginner_mode"


def test_ui_prefs_are_a_server_setting_not_browser_only():
    """界面偏好（背景/主题/强调色/质感/不透明度）也必须存服务器，不能只存 localStorage。

    2026-10-07 老板报的 bug："换浏览器会出现先前配置归 0" —— 根因就是这一份
    原来整份存在浏览器 localStorage（as_appearance），换浏览器/清缓存就没了。
    和 beginner_mode 一样：服务器存 ui_* 键，本地只留镜像。
    """
    headless = (ROOT / "src" / "astroswarm_linux" / "headless_config.py").read_text(encoding="utf-8")
    assert '"ui_theme": str' in headless, "界面偏好要进 SAFE_KEYS 才能被 /api/config 读写"
    assert "def ui_pref_value(" in headless, "要有值域校验（类型/范围/枚举/长度）"
    # 外观那一份的读写必须走服务器：appearance.js 里要有到 ui_* 的映射
    appearance = (CONSOLE / "appearance.js").read_text(encoding="utf-8")
    assert "appearanceToServer" in appearance and "appearanceFromServer" in appearance, \
        "appearance.js 要能把本地偏好映射成服务器的 ui_* 键"
    assert "ui_theme" in appearance and "ui_bg_mask" in appearance, "键名要对上后端"
    app_jsx = _console_text(("App.jsx",))
    assert "loadUiPrefs" in app_jsx and "applyServerAppearance" in app_jsx, \
        "App 要在进控制台后读服务器偏好，并在服务器没值时把本地这份迁上去"
    # 界面偏好必须走 /api/ui/prefs（只认控制台会话），**不能**挂在 /api/config 上：
    # 后者属于星群账号那一层，没登录账号就 401 → 换浏览器时偏好读不到 = 老板报的 bug。
    assert "/api/ui/prefs" in app_jsx, "界面偏好要走 /api/ui/prefs"
    api_py = API.read_text(encoding="utf-8")
    assert "/api/ui/prefs" in api_py, "后端要有 /api/ui/prefs"
    assert '"ui_theme"' in api_py or "UI_PREF_KEYS" in api_py, "后端要按白名单回 ui_* 键"


def test_console_password_login_exists_on_both_halves():
    """控制台密码登录（2026-10-07 老板要求）：后端要有这套接口，前端要有密码屏。

    老板原话："再给程序加一个密码登录的功能（防止其他人篡改数据，有 15 天免密码登录验证）"。
    """
    api_py = API.read_text(encoding="utf-8")
    for route in ("/api/console/session", "/api/console/setup",
                  "/api/console/login", "/api/console/logout", "/api/console/password"):
        assert route in api_py, f"后端缺少控制台密码接口：{route}"
    assert "console_session_guard" in api_py, "要有保护所有 /api/** 的会话校验中间件"
    session_py = (ROOT / "src" / "astroswarm_linux" / "console_session.py").read_text(encoding="utf-8")
    assert "scrypt" in session_py, "密码必须用 scrypt 哈希（不落明文）"
    assert "REMEMBER_SECONDS = 15 * 24 * 3600" in session_py, "要支持 15 天免密码"
    assert "secrets.token_urlsafe" in session_py, "会话 token 要用 secrets 生成随机值"
    # 前端：密码屏 + 15 天勾选 + 锁定按钮
    clock = (CONSOLE / "ConsoleLogin.jsx").read_text(encoding="utf-8")
    assert "记住这台设备 15 天" in clock, "要有「记住这台设备 15 天」且默认勾上"
    assert "设置控制台密码" in clock, "首次部署要能设置密码"
    assert "锁定" in app_jsx_text(), "控制台要有「锁定」入口"


def test_console_favicon_is_declared_and_present():
    """页签图标（2026-10-10）：index.html 要声明 favicon，且那个文件真的在。

    服务器日志里一直刷 `GET /favicon.ico 404`：浏览器找不到 <link rel="icon">
    就盲请求 /favicon.ico。现在用枝条巢 logo（白底）当页签图标：
      · 源文件 console/public/logo.png（Vite public 目录，build 原样拷进产物根）
      · index.html 里 <link rel="icon" href="/logo.png">
    只声明不放文件 = /logo.png 404，只放文件不声明 = 老问题照旧，所以两条都钉。

    顺带钉住老板上一条要求：密码屏**不放**图片 logo（"那个方块图标删掉"）。
    """
    index = CONSOLE_ROOT / "index.html"
    assert index.exists(), f"控制台入口不存在：{index}"
    html = index.read_text(encoding="utf-8")
    assert 'rel="icon"' in html.replace("'", '"'), "index.html 缺 <link rel=\"icon\">"
    assert "/logo.png" in html, "favicon 要指向 /logo.png"

    logo = CONSOLE_ROOT / "public" / "logo.png"
    assert logo.exists(), f"favicon 文件不存在：{logo}"
    w, h, ok = png_size(logo.read_bytes())
    assert ok, f"favicon 不是合法 PNG：{logo}"
    assert min(w, h) >= 256, f"favicon 至少 256px，实际 {w}x{h}"

    login = (CONSOLE / "ConsoleLogin.jsx").read_text(encoding="utf-8", errors="replace")
    assert "<img" not in login, "密码屏不许放图片 logo（老板要求删掉那个方块图标）"


def app_jsx_text() -> str:
    return _console_text(("App.jsx",))


if __name__ == "__main__":
    test_key_block_titles_exist_on_both_sides()
    test_home_shows_platform_cards_on_both_sides()
    test_beginner_mode_is_a_server_setting_not_browser_only()
    test_ui_prefs_are_a_server_setting_not_browser_only()
    test_console_password_login_exists_on_both_halves()
    test_console_favicon_is_declared_and_present()
    print("OK console layout parity")
