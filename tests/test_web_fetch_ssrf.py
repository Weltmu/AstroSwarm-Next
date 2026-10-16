# -*- coding: utf-8 -*-
"""取网页的内网拦截（SSRF）—— 2026-10-15 修，三个绕过点全部堵上。

改前 `is_private_host` **只做字面量判断**：`localhost` / `127.` / `10.` / `169.254.` 这种
一眼能认的才拦，带点的域名一律放行。于是这些全都能过：

  · `127.0.0.1.nip.io`、`foo.127.0.0.1.nip.io` —— 通配 DNS 服务，解析到 127.0.0.1
  · `localhost.` —— **尾点**形式，解析到 ::1
  · `metadata.google.internal` 之类的内网别名
  · `::ffff:127.0.0.1` —— IPv4-mapped IPv6，不落在 is_loopback 上

再加上 `urlopen` **自动跟随跳转且不复查**：一个公网地址 302 到 `169.254.169.254`
就绕过了全部拦截。

别人在群里发一个链接 → 机器人替他读内网服务 / 云元数据。这条就是那次修的内容。

跑法：
    .\\.build_venv_py314\\Scripts\\python.exe -m pytest tests/test_web_fetch_ssrf.py -q
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_FETCH = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai" / "web_fetch.py"

_MOD = None


def wf():
    """按真实源码加载 web_fetch.py（纯标准库，任何环境都能跑）。"""
    global _MOD
    if _MOD is None:
        spec = importlib.util.spec_from_file_location("wf_under_test", WEB_FETCH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["wf_under_test"] = mod
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


def blocked(url: str) -> bool:
    _url, why = wf().check_url(url)
    return why == "blocked_host"


# ================================================================ 裸 IP
def test_plain_private_ips_blocked():
    for u in ("http://127.0.0.1/x", "http://10.0.0.5/", "http://192.168.1.1/",
              "http://169.254.169.254/latest/meta-data/", "http://0.0.0.0/",
              "http://172.16.3.4/", "http://[::1]/"):
        assert blocked(u), "内网/本机地址必须拦住：%s" % u


def test_ipv4_mapped_ipv6_blocked():
    """`::ffff:127.0.0.1` 不落在 is_loopback 上，必须按里面的 IPv4 判。"""
    assert blocked("http://[::ffff:127.0.0.1]:8080/"), "IPv4-mapped IPv6 能绕过的话这条就是漏的"
    assert blocked("http://[::ffff:169.254.169.254]/")


def test_bare_hostnames_blocked():
    """裸主机名走内网解析，一律拒绝。"""
    for u in ("http://localhost/", "http://intranet/", "http://router/"):
        assert blocked(u), "裸主机名必须拦住：%s" % u


def test_trailing_dot_localhost_blocked():
    """`localhost.`（尾点）以前能过 —— 现在要拦。"""
    assert blocked("http://localhost./"), "尾点形式的 localhost 必须拦"
    assert blocked("http://LOCALHOST./"), "大小写 + 尾点也要拦"


# ================================================================ 域名解析后再判
def test_domain_resolving_to_loopback_is_blocked(monkeypatch):
    """通配 DNS 服务（nip.io 这类）解析到 127.0.0.1 —— 必须拦。

    这里把解析结果打桩，测的是「**域名会先解析再判**」这条逻辑本身，不依赖网络。
    """
    mod = wf()
    monkeypatch.setattr(mod, "_resolve_ips", lambda host: ["127.0.0.1"])
    assert blocked("http://127.0.0.1.nip.io/"), "解析到回环的域名必须拦"
    assert blocked("http://anything.example.com/"), "解析到回环的域名必须拦"


def test_domain_resolving_to_metadata_ip_is_blocked(monkeypatch):
    mod = wf()
    monkeypatch.setattr(mod, "_resolve_ips", lambda host: ["169.254.169.254"])
    assert blocked("http://metadata.example.com/latest/meta-data/")


def test_domain_resolving_to_private_is_blocked(monkeypatch):
    mod = wf()
    monkeypatch.setattr(mod, "_resolve_ips", lambda host: ["192.168.1.10"])
    assert blocked("http://nas.example.com/")


def test_public_domain_allowed(monkeypatch):
    """公网域名照旧放行 —— 别把功能一起拦死。"""
    mod = wf()
    monkeypatch.setattr(mod, "_resolve_ips", lambda host: ["93.184.216.34"])
    _url, why = mod.check_url("http://example.com/page")
    assert why == "", "公网地址被误拦了：%s" % why


def test_multi_record_domain_blocked_if_any_private(monkeypatch):
    """一个域名解析出多个 IP，只要有内网的就不放行。"""
    mod = wf()
    monkeypatch.setattr(mod, "_resolve_ips", lambda host: ["93.184.216.34", "10.0.0.1"])
    assert blocked("http://mixed.example.com/")


def test_unresolvable_domain_blocked(monkeypatch):
    """解析不出来（不存在的域名 / DNS 挂了）→ 按"不碰"处理。"""
    mod = wf()
    monkeypatch.setattr(mod, "_resolve_ips", lambda host: [])
    assert blocked("http://does-not-resolve.example/")


# ================================================================ 跳转复检
def test_redirect_handler_refuses_auto_follow():
    """必须禁用 urllib 的自动跟随 —— 跳转目标由 _open_checked 逐跳复查。"""
    mod = wf()
    handler = mod._NoRedirect()
    assert handler.redirect_request(None, None, 302, "", {}, "http://x/") is None, \
        "redirect_request 返回 None 才会让 urllib 抛 HTTPError，我们才能自己复查目标"


def test_redirect_target_is_rechecked():
    """跳转目标走的是同一个 check_url —— 内网目标必须被拦。

    直接验证被复查这一步用的判定：`_open_checked` 内部就是调 `check_url(newurl)`。
    """
    mod = wf()
    for bad in ("http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8080/",
                "http://metadata.example.com/"):
        _u, why = mod.check_url(bad)
        assert why in ("blocked_host", "bad_url"), "跳转目标 %s 没被拦" % bad


def test_scheme_and_host_required():
    mod = wf()
    for bad in ("file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)",
                "gopher://example.com/", "http://", ""):
        _u, why = mod.check_url(bad)
        assert why == "bad_url", "%s 应该按 bad_url 拒绝，实际 %s" % (bad, why)


# ================================================================ 源码级锁
def test_source_lock_no_plain_urlopen_in_fetch():
    """源码锁：不许再用**自动跟随跳转、且不复查**的 urlopen。"""
    src = WEB_FETCH.read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "_open_checked(" in code, "必须走逐跳复查的 _open_checked"
    assert "urlopen(req" not in code, \
        "别再用裸 urlopen（它会自动跟随跳转且不复查目标，302 到内网就绕过了）"
    assert "redirect_request" in code and "_NoRedirect" in code, "缺少禁止自动跟随的 handler"
    assert "_resolve_ips(" in code, "域名必须先解析再判（否则 nip.io / localhost. 这类全放行）"
    assert "ipv4_mapped" in code, "IPv4-mapped IPv6 要按里面的 IPv4 判"


def test_source_lock_no_dead_double_request():
    """源码锁：那段"先丢线程池、再同步跑一次"的死代码不许回来（同请求发两遍）。

    注意要**去掉注释再判** —— 修复说明里会引用这两段字面量，直接扫全文会误伤自己。
    """
    src = WEB_FETCH.read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "if False else" not in code, "死分支又回来了：它会让同一个地址被请求两次"
    assert "run_in_executor" not in code, \
        "同步函数里别再用 run_in_executor —— 调用方该用 asyncio.to_thread"
