# -*- coding: utf-8 -*-
"""出站媒体的来源校验（2026-10-15 安全加固）。

挡的是两件真事：
1. **任意本地文件被当"媒体"发出去** —— 模型只有一个字符串参数，适配器却把它
   原样交给协议端（OneBot 的 `upload_group_file` 认本地路径、跨平台转发直接
   `read_bytes()`）。群里一句提示注入就能把 `.env`、密钥配置、日志发进群。
2. **拿 http(s) 地址当跳板打内网（SSRF）** —— 地址常常是**协议端**替我们取的
   （NapCat / QQ 官方），内网口等于把内网服务当图片源。

同时也锁住"别把功能一起拦死"：协议端自己给的图片地址（往往长在本机口上）
必须还能转发 —— 那是老板在用的功能。

跑法：
    .\\.build_venv_py314\\Scripts\\python.exe -m pytest tests/test_media_guard.py -q
"""
import asyncio
import importlib.util
import logging
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
PKG = "ai_guard_pkg"

_MOD = {}


def _load(name: str):
    """按路径加载 ai 插件里的模块（构建环境没有 nonebot，补最小替身）。"""
    if name in _MOD:
        return _MOD[name]
    # ⚠ 不能只判 `if "nonebot" not in sys.modules`：全套跑时前面某个测试文件可能
    # 已经塞了个**残缺**的替身（比如没有 get_bots），那种情况下这里会 ImportError，
    # 而单跑本文件却是绿的。缺哪个属性补哪个，真 nonebot 有就不动它。
    nb = sys.modules.get("nonebot")
    if nb is None:
        nb = types.ModuleType("nonebot")
        sys.modules["nonebot"] = nb
    if not hasattr(nb, "logger"):
        nb.logger = logging.getLogger("nonebot")
    if not hasattr(nb, "get_bots"):
        nb.get_bots = lambda: {}
    # ⚠ 也别只判 `if ... not in sys.modules`：前面某个测试文件可能已经塞了个
    # **不完整**的替身（只有 get_plugin_config_file），那样 memory.py 一 import 就炸。
    # 缺哪个属性补哪个；真模块有就不动它。
    ls = sys.modules.get("nonebot_plugin_localstore")
    if ls is None:
        try:
            import nonebot_plugin_localstore as ls  # noqa: PLC0415
        except Exception:  # noqa: BLE001 —— 构建环境没装，给个替身（只影响本测试）
            ls = types.ModuleType("nonebot_plugin_localstore")
            sys.modules["nonebot_plugin_localstore"] = ls
    tmp_file = Path(tempfile.gettempdir())
    if not hasattr(ls, "get_plugin_config_file"):
        ls.get_plugin_config_file = lambda n: tmp_file / ("qbm_g_" + str(n))
    if not hasattr(ls, "get_plugin_data_file"):
        ls.get_plugin_data_file = lambda n: tmp_file / ("qbm_g_" + str(n))
    if not hasattr(ls, "get_plugin_data_dir"):
        ls.get_plugin_data_dir = lambda: tmp_file

    if PKG not in sys.modules:
        pkg = types.ModuleType(PKG)
        pkg.__path__ = [str(AI_DIR)]
        sys.modules[PKG] = pkg

    spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", AI_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{PKG}.{name}"] = mod
    spec.loader.exec_module(mod)
    _MOD[name] = mod
    return mod


def guard():
    return _load("media_guard")


def bridge():
    return _load("tools_bridge")


class _Channel:
    def __init__(self, kinds=("image", "file", "voice", "card")):
        self.platform = "qq:group"
        self._kinds = set(kinds)
        self.sent = []

    def supports(self, kind):
        return kind in self._kinds

    async def send_media(self, kind, target, *args):
        self.sent.append((kind, target))
        return True

    async def send_text(self, text):
        self.sent.append(("text", text))
        return True


class _Ctx:
    def __init__(self, channel, is_superuser=False):
        self.channel = channel
        self.is_superuser = is_superuser
        self.platform = "qq"
        self.scene = "group"


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ================================================================ 本地路径白名单
def test_local_file_outside_roots_rejected(tmp_path, monkeypatch):
    """本机任意文件（.env、密钥配置、桌面文件）不许当媒体发出去。"""
    monkeypatch.setattr(guard(), "_data_root", lambda: tmp_path / "data")
    secret = tmp_path / "secret.env"
    secret.write_text("API_KEY=1", encoding="utf-8")
    target, why, hint = guard().check_source(str(secret))
    assert why == "path_not_allowed", "本机任意文件必须拒绝，实际 %s" % why
    assert "data/downloads" in hint


def test_local_file_inside_downloads_allowed(tmp_path, monkeypatch):
    """机器人自己下载/生成的文件照旧能发（别把功能一起拦死）。"""
    monkeypatch.setattr(guard(), "_data_root", lambda: tmp_path / "data")
    good = tmp_path / "data" / "downloads" / "pic.jpg"
    good.parent.mkdir(parents=True, exist_ok=True)
    good.write_bytes(b"x")
    target, why, _hint = guard().check_source(str(good))
    assert why == "", "下载目录里的文件被误拦：%s" % why
    assert target.endswith("pic.jpg")


def test_local_file_traversal_out_of_downloads_rejected(tmp_path, monkeypatch):
    """`data/downloads/../ai/keys.json` 这种绕法必须拦住。"""
    monkeypatch.setattr(guard(), "_data_root", lambda: tmp_path / "data")
    keys = tmp_path / "data" / "ai" / "keys.json"
    keys.parent.mkdir(parents=True, exist_ok=True)
    keys.write_text("{}", encoding="utf-8")
    sneaky = tmp_path / "data" / "downloads" / ".." / "ai" / "keys.json"
    (tmp_path / "data" / "downloads").mkdir(parents=True, exist_ok=True)
    _t, why, _h = guard().check_source(str(sneaky))
    assert why == "path_not_allowed", "用 .. 绕出下载目录必须拒绝，实际 %s" % why


def test_workshop_data_allowed(tmp_path, monkeypatch):
    """插件自己画出来的图（data/workshop_data）算合法媒体。"""
    monkeypatch.setattr(guard(), "_data_root", lambda: tmp_path / "data")
    board = tmp_path / "data" / "workshop_data" / "board.png"
    board.parent.mkdir(parents=True, exist_ok=True)
    board.write_bytes(b"x")
    _t, why, _h = guard().check_source(str(board))
    assert why == ""


def test_superuser_may_send_any_local_file(tmp_path, monkeypatch):
    """管理员本人（微信私聊那条通道）要发本机某张表 —— 允许；群里则不允许。"""
    monkeypatch.setattr(guard(), "_data_root", lambda: tmp_path / "data")
    sheet = tmp_path / "报表.xlsx"
    sheet.write_bytes(b"x")
    _t, why, _h = guard().check_source(str(sheet), allow_any_local=True)
    assert why == "", "管理员本人发本机文件被误拦：%s" % why
    _t, why2, _h2 = guard().check_source(str(sheet), allow_any_local=False)
    assert why2 == "path_not_allowed"


def test_missing_and_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(guard(), "_data_root", lambda: tmp_path / "data")
    assert guard().check_source("")[1] == "missing_source"
    assert guard().check_source(str(tmp_path / "nope.bin"))[1] == "not_found"


# ================================================================ 协议 / 特殊写法
def test_unc_and_bad_schemes_rejected():
    for bad in (r"\\nas\share\secret.txt", "//nas/share/secret.txt"):
        assert guard().check_source(bad)[1] == "bad_path", "网络共享路径不许读：%s" % bad
    for bad in ("ftp://example.com/x", "gopher://example.com/", "data:text/plain,x"):
        assert guard().check_source(bad)[1] == "bad_scheme", "%s 不许当媒体" % bad


def test_file_url_converted_then_judged_as_local(tmp_path, monkeypatch):
    """`file://` 换算成本地路径后按本地规矩判 —— 不是"一律拒绝"，也不是"一律放行"。"""
    monkeypatch.setattr(guard(), "_data_root", lambda: tmp_path / "data")
    secret = tmp_path / "secret.env"
    secret.write_text("x", encoding="utf-8")
    _t, why, _h = guard().check_source("file:///" + str(secret).replace("\\", "/"))
    assert why == "path_not_allowed", "file:// 不能成为绕过白名单的后门"

    good = tmp_path / "data" / "downloads" / "a.png"
    good.parent.mkdir(parents=True, exist_ok=True)
    good.write_bytes(b"x")
    target, why2, _h2 = guard().check_source("file:///" + str(good).replace("\\", "/"))
    assert why2 == "" and target.endswith("a.png")


def test_windows_drive_path_not_read_as_scheme(tmp_path, monkeypatch):
    """`D:\\x` 里的 `D:` 不能当成协议名（urlparse 会把它当 scheme）。"""
    monkeypatch.setattr(guard(), "_data_root", lambda: tmp_path / "data")
    f = tmp_path / "data" / "downloads" / "w.png"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x")
    assert guard()._scheme_of(str(f)) == ""
    _t, why, _h = guard().check_source(str(f))
    assert why == ""


# ================================================================ 网址：内网拦截
def test_private_urls_blocked_by_authoritative_check():
    for bad in ("http://127.0.0.1:3000/x.jpg", "http://10.0.0.5/x", "[::1]",
                "http://169.254.169.254/latest/meta-data/", "http://localhost/x"):
        url = bad if bad.startswith("http") else "http://" + bad + "/x"
        assert guard().check_source(url)[1] == "blocked_host", "内网地址必须拦：%s" % url


def test_private_domain_resolved_then_blocked(monkeypatch):
    """通配 DNS（解析到 127.0.0.1 的域名）也要拦 —— 解析后再判。"""
    wf = _load("web_fetch")
    monkeypatch.setattr(wf, "_resolve_ips", lambda host: ["127.0.0.1"])
    assert guard().check_source("http://127.0.0.1.nip.io/x.jpg")[1] == "blocked_host"


def test_public_url_allowed(monkeypatch):
    wf = _load("web_fetch")
    monkeypatch.setattr(wf, "_resolve_ips", lambda host: ["93.184.216.34"])
    _t, why, _h = guard().check_source("https://example.com/a.jpg")
    assert why == "", "公网地址被误拦：%s" % why


def test_precheck_defers_dns_but_blocks_local():
    """同步预筛：网址原样放行（留给权威检查），本地/协议当场判。

    为什么要分开：预筛跑在事件循环的同步代码里（模型填完地址就要给回执），
    一次卡住的域名解析能把整个机器人拖住好几秒。
    """
    assert guard().precheck_source("http://127.0.0.1.nip.io/x.jpg")[1] == "", \
        "预筛不查 DNS，内网域名的拦截交给投递前的权威检查"
    assert guard().precheck_source("http://example.com/x.jpg")[1] == ""
    assert guard().precheck_source("ftp://example.com/x")[1] == "bad_scheme"
    assert guard().precheck_source(r"\\nas\share\x.txt")[1] == "bad_path"


# ================================================================ 协议端给的地址
def test_trusted_origin_allows_loopback_media_server(monkeypatch):
    """NapCat 图片地址常常就在本机口上 —— 机器人自己递出去的地址必须能转发。"""
    g = guard()
    g._clear_trusted()
    url = "http://127.0.0.1:3000/api/file/a.jpg"
    assert g.check_source(url)[1] == "blocked_host", "没登记过就应该拦"
    g.trust_origin(url)
    assert g.check_source(url)[1] == "", "登记过（机器人自己给的）就必须放行"
    # 同出处不同文件也要放行：模型转述地址时常改查询串
    assert g.check_source("http://127.0.0.1:3000/api/file/b.jpg?x=1")[1] == ""
    # 别的出处不受影响
    assert g.check_source("http://127.0.0.1:9999/x")[1] == "blocked_host"
    g._clear_trusted()


def test_trusted_origins_capped():
    g = guard()
    g._clear_trusted()
    for i in range(60):
        g.trust_origin(f"http://127.0.0.1:{8000 + i}/x")
    assert len(g._trusted_origins) <= g._TRUSTED_MAX, "登记表要有上限，别当内存垃圾桶"
    g._clear_trusted()


# ================================================================ 动作层：端到端
def test_send_media_action_rejects_local_secret(tmp_path, monkeypatch):
    """`send_file` 动作：本机文件当场拒绝，且**一个字节都没发出去**。"""
    b = bridge()
    monkeypatch.setattr(guard(), "_data_root", lambda: tmp_path / "data")
    secret = tmp_path / "secret.env"
    secret.write_text("API_KEY=1", encoding="utf-8")
    ch = _Channel()
    res = run(b._act_send_media({"path": str(secret)}, _Ctx(ch), "file"))
    assert res["ok"] is False and res["error"] == "path_not_allowed"
    assert ch.sent == [], "被拒的东西绝不能落到通道上"


def test_send_media_action_allows_download_dir(tmp_path, monkeypatch):
    b = bridge()
    monkeypatch.setattr(guard(), "_data_root", lambda: tmp_path / "data")
    good = tmp_path / "data" / "downloads" / "a.png"
    good.parent.mkdir(parents=True, exist_ok=True)
    good.write_bytes(b"x")
    ch = _Channel()
    res = run(b._act_send_media({"path": str(good)}, _Ctx(ch), "image"))
    assert res["ok"] is True
    assert ch.sent and ch.sent[0][0] == "image"


def test_send_file_to_platform_action_rejects_local_secret(tmp_path, monkeypatch):
    """跨平台转发是**更容易出事**的一条：发出去就收不回来，所以排在最前面拦。"""
    b = bridge()
    monkeypatch.setattr(guard(), "_data_root", lambda: tmp_path / "data")
    secret = tmp_path / "secret.env"
    secret.write_text("API_KEY=1", encoding="utf-8")
    res = run(b._act_send_file_to_platform(
        {"platform": "wechat", "file": str(secret), "kind": "file"}, _Ctx(_Channel())))
    assert res["ok"] is False and res["error"] == "path_not_allowed"


def test_compress_image_rejects_local_secret(tmp_path, monkeypatch):
    """`compress_image` 曾经是白名单的**旁路**：把本机任意图片压一下，产物就落进
    `data/downloads/`（白名单目录），再发出去一路畅通。现在来源也要过闸。"""
    b = bridge()
    monkeypatch.setattr(guard(), "_data_root", lambda: tmp_path / "data")
    secret = tmp_path / "私人照片.png"
    secret.write_bytes(b"\x89PNG\r\n\x1a\n")          # 内容不重要，闸在更前面
    res = run(b._act_compress_image({"path": str(secret)}, _Ctx(_Channel())))
    assert res["ok"] is False and res["error"] == "path_not_allowed", res


def test_compress_image_allows_download_dir(tmp_path, monkeypatch):
    """下载目录里的图照旧能压（别把功能一起拦死）。"""
    b = bridge()
    monkeypatch.setattr(guard(), "_data_root", lambda: tmp_path / "data")
    good = tmp_path / "data" / "downloads" / "a.png"
    good.parent.mkdir(parents=True, exist_ok=True)
    good.write_bytes(b"\x89PNG\r\n\x1a\n")
    res = run(b._act_compress_image({"path": str(good)}, _Ctx(_Channel())))
    assert res.get("error") != "path_not_allowed", res


def test_http_request_goes_through_checked_opener():
    """源码锁：通用 HTTP 动作不许再用裸 `urlopen`（我自动跟随跳转、不复查）。
    一个公网地址 302 到 169.254.169.254 就能把内网拦截整个绕过去。"""
    tb = (AI_DIR / "tools_bridge.py").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in tb.splitlines())
    assert "urlopen(" not in code, "别再用裸 urlopen（跳转不复查 = SSRF 旁路）"
    assert "web_fetch.open_checked(" in code, "通用 HTTP 动作必须走逐跳复查的出口"
    wf = (AI_DIR / "web_fetch.py").read_text(encoding="utf-8")
    assert "def open_checked(" in wf and "method" in wf.split("def open_checked(")[1][:400], \
        "open_checked 要支持非 GET（否则通用 HTTP 动作没法复用）"


def test_enqueue_precheck_rejects_local_secret(tmp_path, monkeypatch):
    """跨平台排队的同步预筛（runtime.py 那条路）：本机文件排在队外。"""
    cp = _load("cross_platform")
    monkeypatch.setattr(guard(), "_data_root", lambda: tmp_path / "data")
    secret = tmp_path / "secret.env"
    secret.write_text("x", encoding="utf-8")
    res = cp.enqueue(None, "qq:1", "wechat", "file", source=str(secret))
    assert res["ok"] is False and res["error"] == "path_not_allowed"


# ================================================================ 源码级锁
def test_source_lock_send_media_goes_through_guard():
    """源码锁：三条出站路都必须过 media_guard（别再出现"这条路查了、那条没查"）。"""
    tb = (AI_DIR / "tools_bridge.py").read_text(encoding="utf-8")
    assert "media_guard.check_source" in tb, "tools_bridge 的发媒体动作必须过闸"
    cp = (AI_DIR / "cross_platform.py").read_text(encoding="utf-8")
    assert "media_guard.check_source" in cp, "跨平台投递前必须过权威检查"
    assert "media_guard.precheck_source" in cp, "排队时要有同步预筛"
    assert "open_checked" in cp, "取远程字节必须走逐跳复查的 open_checked"
    code = "\n".join(ln.split("#", 1)[0] for ln in cp.splitlines())
    assert "urlopen(req" not in code, "别再用裸 urlopen（不查内网、不复查跳转）"


def test_source_lock_protocol_side_urls_are_registered():
    """机器人在提示词里递给模型的地址必须登记出处，否则转发会被内网拦截误伤。"""
    for name in ("adapters/qq.py", "adapters/wechat.py"):
        src = (AI_DIR / name).read_text(encoding="utf-8")
        assert "trust_origin" in src, "%s 少了 trust_origin（图片转发会被误拦）" % name
    assert "trust_origin" in (AI_DIR / "file_reader.py").read_text(encoding="utf-8")


if __name__ == "__main__":
    print("请用 pytest 跑：pytest tests/test_media_guard.py -q")
