# -*- coding: utf-8 -*-
"""桌面端 vs Linux 控制台：**对齐矩阵**（防止以后又悄悄跑偏）。

老板 2026-10-04 的原话："我要你100%同步ui和功能！！！！"
"100%"不可能是字面意思（桌面端有窗口外观、视频工具、迁移这类天生只属于桌面），
所以这份用例把"能对齐的部分"钉死：

1. 导航：控制台 `nav.js` 的 4 组 6 项 == 桌面端 `main_window.NAV_GROUPS`
   （小白模式藏哪些也要一致：桌面端不藏「插件工坊」，控制台也不许藏）；
2. 功能点：桌面端每个"服务器侧"能力，控制台必须有对应后端接口 —— 用下面的
   REQUIRED_ENDPOINTS 清单显式列出（新增桌面功能时同步加到这里）；
3. 术语：控制台界面文案里不许出现 openid / NoneBot / config.json 这类开发词。

这份用例跑失败 = 又跑偏了，要么补实现、要么在这里写明为什么不做。
"""
import re
from pathlib import Path

# PNG 尺寸用标准库读：`.test_venv` 里没有 Pillow，硬 import PIL 会让整份用例在
# 收集阶段就 ImportError（实测踩过）。
from _png_meta import png_size

ROOT = Path(__file__).resolve().parents[1]
NAV_JS = Path(r"D:\ai\AstroSwarm-public\console\src\nav.js")
CONSOLE_SRC = Path(r"D:\ai\AstroSwarm-public\console\src")
CONSOLE_ROOT = Path(r"D:\ai\AstroSwarm-public\console")
CONSOLE_INDEX = CONSOLE_ROOT / "index.html"
# favicon 走 Vite 的 public/ 静态目录：console/public/logo.png 会原样拷到 console-dist/logo.png，
# 无头端 StaticFiles 挂在 "/" 上 → /logo.png 直接命中（2026-10-10 加）。
CONSOLE_LOGO = CONSOLE_ROOT / "public" / "logo.png"
CONSOLE_LOGO_NAME = "logo.png"
EXT = ROOT / "src" / "astroswarm_linux" / "console_ext.py"
API = ROOT / "src" / "astroswarm_linux" / "api.py"
MAIN_WINDOW = ROOT / "src" / "qbotmanager" / "ui" / "main_window.py"
# 桌面端各页面（「本机能力」「AI 大脑」这些区块都在这里，两侧对齐的参照物）
DESKTOP_PAGES = ROOT / "src" / "qbotmanager" / "ui" / "pages"

# 桌面端能力 → 控制台必须提供的接口（方法只校验路径，方法写出来便于阅读）
REQUIRED_ENDPOINTS = {
    "首页/运行状态": ["/api/status", "/api/stats/summary"],
    "服务启停": ["/api/services/status", "/api/services/start",
                 "/api/services/stop", "/api/services/restart"],
    "QQ 通道": ["/api/qq/diagnose", "/api/qq/group-state", "/api/qq/official"],
    "微信通道": ["/api/wechat/status", "/api/wechat/relogin", "/api/wechat/qrcode.png"],
    "飞书通道": ["/api/feishu/status", "/api/feishu/config", "/api/feishu/install-adapter"],
    "纸飞机通道": ["/api/telegram/status"],
    "AI 配置/开关": ["/api/ai/settings", "/api/ai/personality", "/api/ai/models"],
    "记忆": ["/api/memory/list", "/api/memory/clear", "/api/memory/export"],
    "知识库": ["/api/knowledge/list", "/api/knowledge/add", "/api/knowledge/delete"],
    "插件": ["/api/plugins", "/api/plugins/installed", "/api/plugins/install",
             "/api/plugins/uninstall", "/api/plugins/redeem", "/api/plugins/market"],
    "能力包": ["/api/tools/market", "/api/tools/status", "/api/tools/install",
               "/api/tools/uninstall"],
    "插件工坊": ["/api/workshop/status", "/api/workshop/build-async",
                 "/api/workshop/install", "/api/workshop/history"],
    "依赖": ["/api/deps/status", "/api/deps/scan", "/api/deps/install"],
    "日志": ["/api/logs/tail", "/api/logs/download", "/api/logs/official",
             "/api/logs/retention", "/api/logs/cleanup-now"],
    "消息中心": ["/api/messages/list"],
    "账号": ["/api/auth/login", "/api/auth/status", "/api/account/profile"],
    "和风天气": ["/api/qweather/config", "/api/qweather/save"],
    "配置读写": ["/api/config"],
    # 2026-10-12 加：桌面端「插件 → 本机能力（可搜索 · 可开关）」那一节的后端。
    # 老板原话：「插件页面看不见好友申请那个功能」「加一个能搜索还带开关的」。
    "本机能力（可搜索·可开关）": ["/api/ai/capabilities"],
    # 2026-10-12 加：好友申请清单（机器人进程落盘、控制台只读展示）。
    "好友申请": ["/api/ai/friend-requests"],
}

# 控制台界面文案里不该出现的开发词（术语统一：openid → 会话标识）
BANNED_WORDS = ("openid", "NoneBot", "config.json", "webhook")

# 这些是"代码里的标识符/包名示例"，不是给用户看的文案，白名单放行
SAFE_PATTERNS = (
    r"[A-Za-z_]*openid[A-Za-z_]*\s*[:=]",        # agent_owner_openid: / g.openid
    r"\.openid",                                  # g.openid
    r"nonebot-plugin-",                           # PyPI 包名示例
    r"'nonebot'|\"nonebot\"",                     # 接口参数名
)


def _visible_copy(path: Path) -> list:
    """挑出"用户看得见的中文文案"行：含中文 + 含被禁词，且不属于代码标识符。"""
    out = []
    raw = path.read_text(encoding="utf-8", errors="replace")
    # JSX 注释 {/* ... */} 与 JS 注释都去掉
    raw = re.sub(r"\{/\*.*?\*/\}", "", raw, flags=re.S)
    raw = _strip_js_comments(raw)
    for i, line in enumerate(raw.splitlines(), 1):
        if not re.search(r"[\u4e00-\u9fff]", line):
            continue                      # 没有中文：不是给用户看的文案
        if any(re.search(pat, line) for pat in SAFE_PATTERNS):
            continue
        for word in BANNED_WORDS:
            if word in line:
                out.append(f"{path.name}:{i} 出现 {word}")
    return out


def test_console_copy_has_no_dev_jargon():
    bad = []
    for path in CONSOLE_SRC.glob("*.jsx"):
        bad += _visible_copy(path)
    assert not bad, "控制台界面文案里有开发词：" + "; ".join(bad[:6])


def _console_endpoints() -> set:
    out = set()
    for path in (EXT, API):
        if path.exists():
            txt = path.read_text(encoding="utf-8", errors="replace")
            out.update(re.findall(r'@app\.(?:get|post|put|delete)\("([^"]+)"', txt))
    return out


def test_nav_matches_desktop():
    """4 组 6 项必须和桌面端一致（含"不藏插件工坊"）。"""
    assert NAV_JS.exists(), f"控制台导航文件不存在：{NAV_JS}"
    js = NAV_JS.read_text(encoding="utf-8")
    # 只解析 NAV_GROUPS 那一段（后面的 ADVANCED_PAGES 里也有 ['a', 'b'] 形状，别混进来）
    block = js.split("export const NAV_GROUPS", 1)[1].split("export const ADVANCED_PAGES", 1)[0]
    groups = re.findall(r"title: '([^']+)'", block)
    assert groups == ["机器人", "工坊", "人格", "设置"], groups
    items = re.findall(r"\['\w+', '([^']+)',", block)
    assert items == ["首页", "接入", "插件", "插件工坊", "AI 大脑",
                     "全局管理", "消息中心", "日志", "依赖", "设置"], items

    desktop = MAIN_WINDOW.read_text(encoding="utf-8")
    m = re.search(r"NAV_GROUPS = \((.*?)\)\n", desktop, re.S)
    assert m, "桌面端找不到 NAV_GROUPS"
    assert "(\"工坊\", (\"插件\", \"插件工坊\"))" in m.group(1), m.group(1)
    assert "(\"设置\", (\"全局管理\", \"消息中心\", \"日志\", \"依赖\", \"设置\"))" in m.group(1), m.group(1)


def test_beginner_mode_hides_the_same_pages():
    """小白模式藏哪些页，两边必须一致 —— 老板为"插件工坊被藏"发过火。"""
    js = NAV_JS.read_text(encoding="utf-8")
    m = re.search(r"ADVANCED_PAGES = \[([^\]]+)\]", js)
    assert m, "控制台找不到 ADVANCED_PAGES"
    console_hidden = {x.strip().strip("'\"") for x in m.group(1).split(",")}
    assert "workshop" not in console_hidden, "插件工坊不许被小白模式藏掉"

    desktop = MAIN_WINDOW.read_text(encoding="utf-8")
    m2 = re.search(r'ADVANCED_PAGES = \(([^)]+)\)', desktop)
    assert m2, "桌面端找不到 ADVANCED_PAGES"
    desktop_hidden = {x.strip().strip('"') for x in m2.group(1).split(",")}
    assert "插件工坊" not in desktop_hidden, "桌面端也不该藏插件工坊"
    # 两边藏的"概念"要对得上（桌面用中文页名，控制台用 id）
    mapping = {"消息中心": "messages", "插件": "plugins", "日志": "logs", "依赖": "deps"}
    for zh, cid in mapping.items():
        assert (zh in desktop_hidden) == (cid in console_hidden), f"{zh}/{cid} 隐藏规则不一致"


def test_every_desktop_capability_has_console_endpoints():
    eps = _console_endpoints()
    missing = {}
    for area, wanted in REQUIRED_ENDPOINTS.items():
        gap = [p for p in wanted if p not in eps]
        if gap:
            missing[area] = gap
    assert not missing, "控制台缺这些接口（桌面端有对应功能）：" + repr(missing)


def _strip_js_comments(text: str) -> str:
    """去掉 /* */ 与 // 注释 —— 注释里出现 NoneBot 是正常的（讲实现），
    只有"用户能看到的文案"才不许有开发词。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in text.splitlines())





def _visible_copy(path: Path) -> list:
    """只挑"用户看得见的文案"：引号里的字符串 + JSX 文本节点，且含中文。

    不整文件扫、也不"剥注释"：注释里讲实现必然出现 NoneBot，而正则剥注释不可靠
    （文件里只要有一个 /* 出现在字符串或正则里，配对就全乱 —— 实测就踩到了）。
    所以反过来：只信"引号里 / 尖括号之间"的内容。
    """
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if line.strip().startswith(("//", "*", "/*")):
            continue
        chunks = re.findall(r"'([^']*)'|\"([^\"]*)\"|>([^<>{}]+)<", line)
        visible = " ".join((a or b or c) for a, b, c in chunks)
        if not visible or not re.search(r"[\u4e00-\u9fff]", visible):
            continue
        if any(re.search(pat, visible) for pat in SAFE_PATTERNS):
            continue
        for word in BANNED_WORDS:
            if word in visible:
                out.append(f"{path.name}:{i} 出现 {word}")
    return out


def test_console_copy_has_no_dev_jargon():
    bad = []
    for path in CONSOLE_SRC.glob("*.jsx"):
        bad += _visible_copy(path)
    assert not bad, "控制台界面文案里有开发词：" + "; ".join(bad[:6])


def test_console_index_declares_an_existing_favicon():
    """控制台页签图标必须是「index.html 里声明 + 静态文件真存在」。

    老板 2026-10-10 让用枝条巢 logo 当程序图标，并且服务器日志里一直有
    `GET /favicon.ico 404`。只放文件不声明，浏览器照样盲请求 /favicon.ico；
    只声明不放文件，就变成 /logo.png 404 —— 所以两头都钉住。

    另外钉一条反向要求：密码屏/顶栏**不许**塞图片 logo
    （老板上次明确要求"密码屏上那个方块图标删掉"）。
    """
    assert CONSOLE_INDEX.exists(), f"控制台入口不存在：{CONSOLE_INDEX}"
    html = CONSOLE_INDEX.read_text(encoding="utf-8")
    m = re.search(r"<link[^>]*rel=[\"'](?:shortcut )?icon[\"'][^>]*>", html, re.I)
    assert m, "index.html 里没有 <link rel=\"icon\" ...>：浏览器会一直盲请求 /favicon.ico"
    href_m = re.search(r"href=[\"']([^\"']+)[\"']", m.group(0))
    assert href_m, f"icon 那行没有 href：{m.group(0)}"
    href = href_m.group(1)
    assert href == f"/{CONSOLE_LOGO_NAME}", f"favicon 应指向 /{CONSOLE_LOGO_NAME}，实际 {href}"

    # 文件本身要在（Vite public/ → 随 build 拷进产物根目录）
    assert CONSOLE_LOGO.exists(), f"favicon 文件不存在：{CONSOLE_LOGO}"
    assert CONSOLE_LOGO.stat().st_size > 1024, "favicon 文件小得不像图"
    w, h, ok = png_size(CONSOLE_LOGO.read_bytes())
    assert ok, f"favicon 不是合法 PNG：{CONSOLE_LOGO}"
    assert min(w, h) >= 256, f"favicon 至少 256px（页签/高分屏都要用），实际 {w}x{h}"

    # 别再往密码屏/顶栏加图片 logo
    login = (CONSOLE_ROOT / "src" / "ConsoleLogin.jsx").read_text(encoding="utf-8", errors="replace")
    assert "<img" not in login, "密码屏不许放图片 logo（老板要求删掉那个方块图标）"


def test_machine_capabilities_panel_exists_on_both_sides():
    """本机能力（可搜索 · 可开关）—— 2026-10-12 桌面端新加的一节，控制台必须跟上。

    老板原话：「插件页面看不见好友申请那个功能」「加一个能搜索还带开关的」。
    桌面端：`ui/pages/plugins_page.py::_build_capability_panel`（标题 + 搜索框 + 计数 +
    每项一个 iOSSwitchRow 开关），数据来自 `core/ai_config.list_capabilities`。
    控制台：`MachineCapabilities.jsx` + `GET/PUT /api/ai/capabilities`
    （后端复用同一份 ai_config / 同一份 disabled_tools，**不另写第二套实现**）。

    这条锁四件事，任何一头被改掉都会红：
      ① 桌面端那节的标题与搜索框文案（参照物，不许漂）；
      ② 控制台有这张卡、有搜索框、用共用控件 ToggleRow（不是自己画一个复选框）；
      ③ 后端有这两个接口，且用的是 ai_config 的 list_capabilities /
         set_tool_enabled（不是自己造一份能力表）；
      ④ "加好友"那两项能力真的在清单里（approve_friend / list_friend_requests）。
    """
    desktop = (DESKTOP_PAGES / "plugins_page.py").read_text(encoding="utf-8")
    for needle in ("本机能力（可搜索 · 可开关）",
                   "搜索能力：输名字、说明或来源里的字都行",
                   "ai_config.list_capabilities",
                   "ai_config.set_tool_enabled"):
        assert needle in desktop, f"桌面端「本机能力」那节缺 {needle!r}（参照物变了要同步这条锁）"

    jsx = (CONSOLE_SRC / "MachineCapabilities.jsx")
    assert jsx.exists(), f"控制台缺 {jsx.name}（桌面端有「本机能力」这一节）"
    text = jsx.read_text(encoding="utf-8")
    for needle in ("本机能力（可搜索 · 可开关）",
                   "搜索能力：输名字、说明或来源里的字都行",
                   "/api/ai/capabilities",
                   "components/Toggle.jsx"):
        assert needle in text, f"控制台「本机能力」卡缺 {needle!r}"
    assert "type=\"checkbox\"" not in text, \
        "本机能力卡不许用裸复选框（老板嫌『开关太 low』，统一控件是 ToggleRow）"
    # 真挂在「插件」页上（不是写好放在那儿没人引用）
    app_jsx = (CONSOLE_SRC / "App.jsx").read_text(encoding="utf-8")
    assert "MachineCapabilities" in app_jsx, "「插件」页没有挂上本机能力卡"
    assert "<MachineCapabilities" in app_jsx, "本机能力卡没被渲染（只 import 了不算）"

    ext = EXT.read_text(encoding="utf-8")
    assert '@app.get("/api/ai/capabilities")' in ext, "后端缺 GET /api/ai/capabilities"
    assert '@app.put("/api/ai/capabilities")' in ext, "后端缺 PUT /api/ai/capabilities"
    assert "cfg.read_disabled_tools" in ext and "cfg.set_tool_enabled" in ext, \
        "后端必须复用 ai_config 的 disabled_tools 读写（别另写一套能力表）"
    # 能力名字要能真读到（读的是同一份 ai 插件声明）
    bridge = (ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
              / "tools_bridge.py").read_text(encoding="utf-8")
    for tool in ('_tool("list_friend_requests"', '_tool("approve_friend"'):
        assert tool in bridge, f"ai 插件缺 {tool}（老板要的『加好友』功能）"


def test_friend_request_and_agent_mode_switches_match_desktop():
    """「自动通过好友申请」+「Agent 模式」两个开关：两侧文案一字不差，且能力不在就不显示。

    桌面端 brain_page.py:673-695（2026-10-12）：
      · `if ai_config.has_capability(self.ctx.settings, "approve_friend")` 才建这个复选框；
      · `if ai_config.has_capability(self.ctx.settings, "send_to")` 才建 Agent 模式；
      · 文案：「自动通过好友申请（卖家/客服用）」「Agent 模式：先动手办事，再说话」，
        两处都是 toggled → ai_config.save_*（拨一下立刻存）。
    控制台这一轮补齐：/api/ai/settings 回这两个值 + 两个 has_* 判据，前端照判据显示。
    """
    brain = (DESKTOP_PAGES / "brain_page.py").read_text(encoding="utf-8")
    for needle in ('has_capability(self.ctx.settings, "approve_friend")',
                   'has_capability(self.ctx.settings, "send_to_my_platform")',
                   "自动通过好友申请（卖家/客服用）",
                   "Agent 模式：先动手办事，再说话",
                   "ai_config.save_auto_approve_friend",
                   "ai_config.save_agent_mode"):
        assert needle in brain, f"桌面端缺 {needle!r}（参照物变了要同步这条锁）"

    ext = EXT.read_text(encoding="utf-8")
    for needle in ('"auto_approve_friend": ai_config.read_auto_approve_friend(s)',
                   '"agent_mode_enabled": ai_config.read_agent_mode(s)',
                   '"has_approve_friend": ai_config.has_capability(s, "approve_friend")',
                   '"has_agent_tools": ai_config.has_capability(s, "send_to")',
                   'ai_config.save_auto_approve_friend(s, bool(data["auto_approve_friend"]))',
                   'ai_config.save_agent_mode(s, bool(data["agent_mode_enabled"]))'):
        assert needle in ext, f"无头端 /api/ai/settings 缺 {needle!r}"

    card = (CONSOLE_SRC / "BotAiCard.jsx").read_text(encoding="utf-8")
    for needle in ("自动通过好友申请（卖家/客服用）",
                   "Agent 模式：先动手办事，再说话",
                   "has_approve_friend", "has_agent_tools"):
        assert needle in card, f"控制台 BotAiCard 缺 {needle!r}"
    # 能力不在就不显示（与桌面端同一个判据），不是"永远显示一个按了没用的开关"
    assert "{draft.has_approve_friend && (" in card, "好友申请开关没有按能力显示"
    assert "{draft.has_agent_tools && (" in card, "Agent 模式开关没有按能力显示"


def test_friend_requests_bridge_is_read_only_from_console():
    """好友申请：机器人进程落盘、控制台只读 —— 控制台**绝不**替机器人做动作。

    为什么这么做：控制台是 uvicorn 进程、插件在 nonebot 进程里，两个进程不共享内存。
    桌面端有 `list_friend_requests` / `approve_friend` 两个工具（模型侧），
    控制台这边只补"看得见"（老板要的是"插件页面看不见好友申请那个功能"），
    "通过某个人"仍然只由机器人侧执行（工具 / 自动通过开关）。
    """
    fr = (ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
          / "friend_requests.py").read_text(encoding="utf-8")
    assert "def pending_file(" in fr, "插件缺 pending_file()（控制台读哪一份要有唯一口径）"
    assert "_write_pending_file()" in fr, "插件没把待处理清单落盘"
    assert "ASTROSWARM_BOT_DIR" in fr, "落盘路径要认 ASTROSWARM_BOT_DIR（部署端注入）"
    deploy = (ROOT / "src" / "astroswarm_linux" / "deploy.py").read_text(encoding="utf-8")
    assert 'env["ASTROSWARM_BOT_DIR"]' in deploy, "部署端要把 ASTROSWARM_BOT_DIR 注入机器人"

    ext = EXT.read_text(encoding="utf-8")
    assert "def friend_requests_state(" in ext, "无头端缺 friend_requests_state()"
    assert 'app.get("/api/ai/friend-requests")' in ext, "无头端缺只读接口"
    # 只读：接口里不许出现"通过/批准"这类会动机器人的字眼
    start = ext.index("def friend_requests_state(")
    body = ext[start:start + 1600]
    for bad in ("set_friend_add_request", "approve(", "approve=True"):
        assert bad not in body, f"控制台那条路径不许调用 {bad!r}（那是机器人侧的事）"
    assert '"read_only": True' in body, "接口要明说自己是只读的"


def test_restart_policy_says_the_same_thing_on_both_sides():
    """「这次改动要不要重启」只有一份口径（桌面端 core/restart_policy.py）。

    老板 2026-10-11 的原话："一些功能能不能加上热重载呀，就不要每次重启机器人了"。
    本轮机动的每一项（能力开关 / 好友申请 / Agent 模式）机器人侧都是每条消息现读，
    所以两侧都必须说"立即生效"，不许一边写"重启生效"。
    """
    pol = (ROOT / "src" / "qbotmanager" / "core" / "restart_policy.py").read_text(encoding="utf-8")
    assert "设置改了立即生效；代码改了才需要重启，且点一下就能重启。" in pol
    ext = EXT.read_text(encoding="utf-8")
    assert "_restart_policy().HOT_LINE" in ext, "无头端保存后要回同一句口径"
    # 本轮机动的两个新开关不许被标成 cold
    for key in ("memory_level", "self_forge_enabled", "image_recognition_enabled"):
        assert f'"{key}": False' in pol, f"{key} 应该是热读（不用重启）"
    card = (CONSOLE_SRC / "BotAiCard.jsx").read_text(encoding="utf-8")
    assert "不用重启机器人" in card or "无需重启" in card, "控制台要写清『立即生效』"


if __name__ == "__main__":
    test_nav_matches_desktop()
    test_beginner_mode_hides_the_same_pages()
    test_every_desktop_capability_has_console_endpoints()
    test_console_copy_has_no_dev_jargon()
    test_console_index_declares_an_existing_favicon()
    test_machine_capabilities_panel_exists_on_both_sides()
    test_friend_request_and_agent_mode_switches_match_desktop()
    test_friend_requests_bridge_is_read_only_from_console()
    test_restart_policy_says_the_same_thing_on_both_sides()
    print("OK console parity matrix")
