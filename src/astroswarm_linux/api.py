"""星巢 Nest（AstroSwarm 无头端）控制 API + React 控制台托管。"""
import base64
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Header as FastAPIHeader
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from qbotmanager.core import ai_config
from qbotmanager.core import restart_policy as restart_policy_mod
from qbotmanager.core import tool_packs as tool_packs_mod
from qbotmanager.core import workshop

from . import (
    __version__,
    auth,
    console_session,
    deploy,
    headless_config,
    logs,
    platform_info,
    plugins,
    services,
    tools,
)

app = FastAPI(title="星巢 Nest（AstroSwarm 无头端）", version=__version__)
log = logging.getLogger("astroswarm.api")


@app.get("/health")
def health():
    """探活接口（客户端/nginx 只用它判断服务活着没）。

    未登录只回 ok/version。machine_id 是权益签名的绑定字段，不给未鉴权的请求。
    """
    return {
        "ok": True,
        "service": "astroswarm-headless",
        "version": __version__,
        "linux": platform_info.is_linux(),
    }


@app.get("/api/status")
def status(authorization: str = FastAPIHeader("", alias="Authorization")):
    """未登录只回最小信息；本机路径/机器码等只给已登录的控制台。

    未登录只回 ok/version；data_home / config_home / 机器码要登录后才给。
    """
    try:
        _require_auth(authorization)
        authed = True
    except HTTPException:
        authed = False
    if not authed:
        return {"ok": True, "version": __version__}
    return {
        "ok": True,
        "version": __version__,
        "machine_id": platform_info.machine_id(),
        "data_home": str(platform_info.data_home()),
        "config_home": str(platform_info.config_home()),
        "log_home": str(platform_info.log_home()),
        "services": {"bot": "pending", "dsh": "pending"},
    }


class LoginBody(BaseModel):
    email: str
    # 密码与验证码二选一（2026-10-06：官网是验证码登录，控制台也对齐）
    password: str = ""
    code: str = ""


class SendCodeBody(BaseModel):
    email: str


class CheckEmailBody(BaseModel):
    email: str


class RegisterBody(BaseModel):
    email: str
    code: str
    password: str


class LoginOrRegisterBody(BaseModel):
    email: str
    password: str = ""
    code: str = ""


class ClaimBody(BaseModel):
    machine_id: str


class ServiceBody(BaseModel):
    service: str


# 人格设定字数上限：桌面端本就没有这个限制，插件侧 tools_bridge._TEXT_LIMIT 也是
# 20000，请求体另有 2MB 上限（20000 字约 60KB，绰绰有余）。别再用 2000 卡人设。
PERSONALITY_MAX_CHARS = 20000


class PersonalityBody(BaseModel):
    personality: str


class ToolInstallBody(BaseModel):
    id: str


@app.post("/api/auth/check-email")
def auth_check_email(body: CheckEmailBody, request: Request):
    """查这个邮箱注册过没有（老板 2026-10-10：控制台登录要按"注册过没有"分流）。

    返回 `{ok: true, registered: bool}`；问不出来时 `{ok: false, reason: "中文原因"}`
    （HTTP 仍是 200，好让前端把"查询失败"和"确定没注册"分开显示）。

    ⚠ 这是**有意**的取舍：它等于允许枚举"某邮箱是否注册过"。老板要的体验是
    "老用户直接看到密码框、新用户才看到注册"，前端必须先知道答案；而这个能力
    账号服务本来就给（register 409「该邮箱已注册」、login 404「该邮箱未注册」、
    forgot-code 404 同理）。这里的边界是：**只读、无副作用**（不发验证码邮件、
    不动任何账号数据）、**按 IP 单独限流**，不给人拿去批量扫邮箱。
    """
    client_ip = _client_ip(request)
    addr = body.email.strip()
    # 格式不对先在本地拦掉：不发请求、也不占限流的桶（前端也有一道同样的本地校验）。
    if "@" not in addr:
        raise HTTPException(400, "邮箱格式不对")
    _check_email_rate_limit(client_ip)
    try:
        return auth.check_email(addr, client_ip=client_ip)
    except Exception as exc:  # noqa: BLE001 —— 问不到就如实说，绝不当成"未注册"
        log.warning("查邮箱注册状态失败：%s", exc)
        return {"ok": False, "reason": str(exc)}


@app.post("/api/auth/send-code")
def auth_send_code(body: SendCodeBody, request: Request):
    """给邮箱发 6 位验证码（透传给账号服务；官网登录用的是同一条路）。

    2026-10-06 加：老板要求控制台登录弹窗支持"邮箱 + 验证码"，与官网一致。
    限流沿用登录那套（按真实来源 IP），避免被拿来刷短信/邮件。
    """
    client_ip = _client_ip(request)
    _login_rate_limit(client_ip)
    try:
        return auth.send_code(body.email.strip(), client_ip=client_ip)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"验证码没发出去：{exc}")


@app.post("/api/auth/login-or-register")
def auth_login_or_register(body: LoginOrRegisterBody, request: Request):
    """一条路：没注册就自动注册，然后登录（老板 2026-10-06 要的体验）。

    · 密码对 → 登录（`mode="login"`）；
    · 登录失败且带了 6 位验证码 → 拿验证码注册（密码用他填的）→ `mode="register"`；
    · 注册也不行（账号其实已存在、只是密码打错）→ 原样回登录那条错。
    """
    client_ip = _client_ip(request)
    _login_rate_limit(client_ip)
    try:
        result = auth.login_or_register(body.email.strip(), body.password,
                                       code=body.code, client_ip=client_ip)
        result["token"] = str(headless_config.load().get("account_token") or "")
        return result
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"登录失败：{exc}")


@app.post("/api/auth/register")
def auth_register(body: RegisterBody, request: Request):
    """注册星群账号（邮箱 + 6 位验证码 + 密码 ≥8 位），成功即登录。

    2026-10-06 加：账号服务的验证码那条路是**注册**，不是登录 ——
    控制台弹窗里那个页签改成"注册新账号"后走这里。
    """
    client_ip = _client_ip(request)
    _login_rate_limit(client_ip)
    try:
        result = auth.register(body.email.strip(), body.code, body.password,
                               client_ip=client_ip)
        result["token"] = str(headless_config.load().get("account_token") or "")
        return result
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"注册失败：{exc}")


@app.post("/api/auth/login")
def auth_login(body: LoginBody, request: Request):
    # 限流桶按真实来源 IP 建：用常量 key 时，一个人打几次就能把所有人（含机主）锁在门外。
    client_ip = _client_ip(request)
    _login_rate_limit(client_ip)
    # 账号服务的登录只认"邮箱 + 密码"：验证码那条路是**注册**（/api/account/register）。
    # 旧版控制台会拿只剩验证码的请求来登录，这里直接给人话，别把 pydantic 原文抛给用户。
    if not str(body.password or "").strip():
        raise HTTPException(400, "验证码只用于注册：登录请用邮箱 + 密码；"
                                 "新账号请点「注册新账号」")
    try:
        # 透传真实来访 IP：无头端是从本机再 POST 一次账号服务，不带转发头的话
        # 账号服务只看到 127.0.0.1，所有控制台登录会共用一个限流桶。
        result = auth.login(body.email.strip(), body.password, client_ip=client_ip,
                            code=(body.code or "").strip())
        result["token"] = str(headless_config.load().get("account_token") or "")
        return result
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(401, f"登录失败：{exc}")


@app.post("/api/auth/claim")
def auth_claim(
    body: ClaimBody,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    _require_auth(authorization)
    try:
        return auth.claim(body.machine_id.strip())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc))


# ════════════════════════════════════════════ 控制台密码登录（2026-10-07）
# 老板原话："再给程序加一个密码登录的功能（防止其他人篡改数据，有 15 天免密码登录验证）"。
# 这一层和星群账号是两件事：账号管"买了什么权益"，控制台密码管
# "坐在这台浏览器前的人有没有资格动这台服务器"。
# 实现全在 console_session.py（scrypt 哈希 / 会话只存 sha256 / 过期服务端判定）。

class ConsoleSetupBody(BaseModel):
    password: str


class ConsoleLoginBody(BaseModel):
    password: str
    remember: bool = True


class ConsolePasswordBody(BaseModel):
    old: str = ""
    new: str = ""


def _console_token(request: Request) -> str:
    """当前请求带的控制台会话 token。

    两种来源：
      · X-Console-Token 头（正常 fetch，前端一律走这个）；
      · ?console=（浏览器不给带 header 的两个例外：日志 EventSource 流、微信二维码 <img>。
        那两个 URL 上已经有账号 token 占着 ?token=，所以控制台会话单独用 ?console=，
        两个参数互不顶替）。
      为了兼容手敲/脚本调试，?token= 也在没有 ?console= 时被当成控制台会话试一次。
    """
    headers = getattr(request, "headers", None)
    tok = str(headers.get("x-console-token") or "").strip() if headers is not None else ""
    if tok:
        return tok
    try:
        params = request.query_params
        return str(params.get("console") or params.get("token") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _device_label(request: Request) -> str:
    """从 User-Agent 里认个粗略的设备名，只用于"这是哪台设备"的展示。

    刻意不做指纹：只取「浏览器 + 系统」两个词，够老板认出自己的电脑就行。
    """
    ua = ""
    try:
        ua = str(request.headers.get("user-agent") or "")
    except Exception:  # noqa: BLE001
        ua = ""
    browser = next((n for n, k in (
        ("Edge", "Edg/"), ("Chrome", "Chrome/"), ("Firefox", "Firefox/"),
        ("Safari", "Safari/"), ("微信内置浏览器", "MicroMessenger"),
    ) if k in ua), "浏览器")
    system = next((n for n, k in (
        ("Windows", "Windows"), ("macOS", "Mac OS"), ("iPhone", "iPhone"),
        ("iPad", "iPad"), ("Android", "Android"), ("Linux", "Linux"),
    ) if k in ua), "未知系统")
    return "%s · %s" % (browser, system)


@app.get("/api/console/session")
def console_session_state(request: Request):
    """控制台自己的会话状态。**未登录也能问**（前端靠它决定显示密码屏还是控制台）。"""
    return console_session.session_info(_console_token(request))


@app.post("/api/console/setup")
def console_setup(body: ConsoleSetupBody, request: Request):
    """首次部署设密码。**只在还没设过密码时可用**；设过之后一律拒绝（要改用 /password）。"""
    ip = _client_ip(request)
    try:
        console_session.rate_limit(ip)
    except RuntimeError as exc:
        raise HTTPException(429, str(exc))
    if console_session.is_password_set():
        # 已经设过：不能让人从这条路把密码换掉（否则等于没门槛）
        raise HTTPException(400, "已经设置过控制台密码了；改密码请先登录，再在设置里改")
    try:
        console_session.set_password(body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    sess = console_session.create_session(
        remember=True, device_label=_device_label(request), client_ip=ip)
    log.info("控制台密码已设置（首次部署），来源 %s", ip or "unknown")
    return {"ok": True, "token": sess["token"], "expires_at": sess["expires_at"],
            "device_label": _device_label(request), "remember": True}


@app.post("/api/console/login")
def console_login(body: ConsoleLoginBody, request: Request):
    """密码登录。密码错 → 401；试太多 → 429。日志里**只有 IP，没有密码**。"""
    ip = _client_ip(request)
    try:
        console_session.rate_limit(ip)
    except RuntimeError as exc:
        raise HTTPException(429, str(exc))
    if not console_session.is_password_set():
        raise HTTPException(400, "还没有设置控制台密码，请先设置")
    try:
        ok = console_session.verify_password_for_login(body.password)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("astroswarm").error("控制台登录校验失败：%s", exc)
        raise HTTPException(500, "控制台鉴权文件读不出来，请看服务器日志")
    if not ok:
        logging.getLogger("astroswarm").warning("控制台密码错误，来源 %s", ip or "unknown")
        raise HTTPException(401, "密码不对")
    sess = console_session.create_session(
        remember=bool(body.remember), device_label=_device_label(request), client_ip=ip)
    return {
        "ok": True,
        "token": sess["token"],
        "expires_at": sess["expires_at"],
        "remember": sess["remember"],
        "ttl": sess["ttl"],
        "device_label": _device_label(request),
    }


@app.post("/api/console/logout")
def console_logout(request: Request):
    """退出控制台会话。

    刻意**不动星群账号**（account_token 还在）：老板要的是"锁一下屏幕"，
    顺手把账号也退掉会害他重新走一遍邮箱登录。账号退出在 /api/auth/logout。
    """
    token = _console_token(request)
    removed = console_session.revoke(token)
    return {"ok": True, "removed": bool(removed), "account_logged_out": False}


@app.get("/api/ui/prefs")
def ui_prefs_get(request: Request):
    """界面偏好（外观/主题/强调色…）。**只要控制台会话，不要星群账号。**

    为什么不复用 /api/config：那个接口是"星群账号"那一层的，没登录账号就 401。
    而"界面偏好"属于**这台服务器的控制台**，跟账号权益无关 ——
    老板报的 bug 正是这个组合：新浏览器打开 → 控制台密码过了，但账号那层还没登录 →
    /api/config 401 → 外观回落到默认值，看着就是"配置归 0"。
    所以给偏好单开一条只认控制台会话的路：地址栏能打开控制台，偏好就该读得到。

    只回 ui_* 白名单键，**绝不带任何密钥类字段**（SECRET_FIELDS 一个都不在这里面）。

    ⚠ 它受不受控制台密码保护？（2026-10-10 复核，老板让查的那一条）
    受 —— console_session_guard 拦的是"/api/ 下、且不在 CONSOLE_OPEN_PREFIXES 里"的全部路径，
    /api/ui/prefs 不在放行名单里，所以**设过密码之后**未带会话一律 401
    （与 PUT 同一条规则，读写都不漏）。
    那为什么线上实测未登录还能拿到 200？因为那台机器 `need_setup: true` ——
    **还没设过控制台密码**。这是首次部署的引导口子，写死在中间件里：
    没密码就没人能证明自己是机主，此时若把 /api/** 全锁死，连"设置控制台密码"
    这一屏都进不去（前端要靠 /api/console/session 拿 need_setup，靠本接口拿主题，
    否则首屏是一片没有样式的默认界面）。这个口子的代价是"首设密码前同局域网可读偏好"，
    换来的价值是"开箱即用"；所以这里必须保证**即使在这个窗口期也读不到任何敏感值**：
    返回体只有 UI_PREF_KEYS（背景/遮罩/不透明度/主题/强调色/质感）这几个界面键，
    SECRET_FIELDS（qq_app_secret / qq_app_token / ai_api_key / vision_api_key）
    与 account_token 一个都不在里面。tests/test_console_session.py 把这条钉死了：
    设过密码未登录 → 401；未设密码 → 200 且响应里不含任何密钥键。
    """
    cfg = headless_config.load()
    out = {"ok": True}
    for key in headless_config.UI_PREF_KEYS:
        if key in cfg:
            out[key] = cfg[key]
    return out


@app.put("/api/ui/prefs")
def ui_prefs_put(body: dict, request: Request):
    """写界面偏好。值和类型都过 headless_config.ui_pref_value() 的值域校验。

    未知键**直接 400**（不像 /api/config 那样静默忽略）：这条接口的契约就是"白名单键"，
    前端写错键名要立刻看得见，而不是"保存成功但没生效"。
    """
    if not isinstance(body, dict) or not body:
        raise HTTPException(400, "没有要保存的界面偏好")
    unknown = [k for k in body if k not in headless_config.UI_PREF_DEFAULTS]
    if unknown:
        raise HTTPException(400, "不认识的界面偏好项：%s" % "、".join(sorted(unknown)[:5]))
    cfg = headless_config.load()
    for key, raw in body.items():
        try:
            cfg[key] = headless_config.ui_pref_value(key, raw)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    headless_config.save(cfg)
    out = {"ok": True}
    for key in headless_config.UI_PREF_KEYS:
        if key in cfg:
            out[key] = cfg[key]
    return out


@app.post("/api/console/password")
def console_password(body: ConsolePasswordBody, request: Request):
    """改密码。要求已登录 + 旧密码对；改完**作废当前会话以外的所有会话**。"""
    token = _console_token(request)
    rec = console_session.verify(token)
    if rec is None:
        raise HTTPException(401, "控制台会话已过期，请重新输入密码")
    ip = _client_ip(request)
    try:
        console_session.rate_limit(ip)
    except RuntimeError as exc:
        raise HTTPException(429, str(exc))
    try:
        console_session.set_password(body.new, require_old=body.old)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    killed = console_session.revoke_others(token)
    log.info("控制台密码已修改，来源 %s；同时作废了 %d 个其它会话", ip or "unknown", killed)
    return {"ok": True, "revoked_others": killed}


# 不需要控制台会话就能访问的接口前缀。
#   /api/status  —— 前端靠它显示版本号，也是探活；里面本来就只回 ok/version（未登录时）
#   /api/console —— 登录流程本身（不然就成了"要先登录才能登录"）
CONSOLE_OPEN_PREFIXES = ("/api/console/", "/api/status")


@app.middleware("http")
async def console_session_guard(request: Request, call_next):
    """保护范围：除 /api/status、/api/console/* 和静态资源外，**所有 /api/** 都要控制台会话。

    两层鉴权并存、互不替代：
      · 这一层（控制台会话）挡住"同局域网随便谁打开网页就能改配置"；
      · 各接口里的 _require_auth（星群账号 token）继续管权益相关的数据。
    哪一层不过都是 401，但 detail 分开写 —— 前端要据此决定弹密码屏还是账号弹窗。
    """
    path = str(getattr(request, "url", None).path or "") if getattr(request, "url", None) else ""
    if path.startswith("/api/") and not path.startswith(CONSOLE_OPEN_PREFIXES):
        # 还没设过密码（首次部署）：不能把人关在门外，否则连"设密码"这一屏都进不去。
        # 前端拿到 need_setup=true 会直接显示"设置控制台密码"。
        if console_session.is_password_set() and console_session.verify(
                _console_token(request)) is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "需要控制台密码", "console_locked": True},
                headers={"Cache-Control": "no-store"},
            )
    response = await call_next(request)
    # /api/** 一律不许缓存（2026-10-10）：接口回的是"此刻的服务器状态"，
    # 被浏览器或中间代理缓存住，界面上显示的就是过期的配置/会话状态 ——
    # 尤其 /api/console/session 决定"显示密码屏还是控制台"，缓存住会直接进错界面。
    # no-store 比 no-cache 更严：连磁盘副本都不留。
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/auth/logout")
def auth_logout(authorization: str = FastAPIHeader("", alias="Authorization")):
    _require_auth(authorization)
    auth.logout()
    return {"ok": True}


@app.get("/api/auth/status")
def auth_status(authorization: str = FastAPIHeader("", alias="Authorization")):
    """未登录只回「没登录」；账号邮箱/套餐/已购插件只给已登录的控制台。

    未登录不返回本机账号邮箱与套餐。

    未登录分支与 auth.feature_gate() 保持同一组字段（含 member / all_plugins）：
    前端按 member 判「通道是否开通」、按 all_plugins 判「能不能装」，少一个字段
    就会被读成 undefined，界面会退回「未开通」。
    """
    try:
        _require_auth(authorization)
    except HTTPException:
        return {"full": False, "member": False, "all_plugins": False,
                "plan": "none", "plan_expires_at": 0,
                "email": "", "logged_in": False, "reason": "未登录"}
    return auth.feature_gate()


@app.get("/api/config")
def get_config(authorization: str = FastAPIHeader("", alias="Authorization")):
    _require_auth(authorization)
    cfg = headless_config.load()
    out = {}
    for key in headless_config.SAFE_KEYS:
        value = cfg.get(key)
        out[key] = _mask(value) if key in SECRET_FIELDS and value else value
    # 配置坏过要让前端看得见，否则界面一切正常，用户不知道登录态是从坏文件里抢救的。
    for key in ("_config_error", "_config_backup", "_config_last_good"):
        if cfg.get(key):
            out[key] = str(cfg[key])
    if cfg.get("_config_rescued"):
        out["_config_rescued"] = list(cfg["_config_rescued"])
    return out


@app.post("/api/config/restore-last-good")
def post_config_restore(authorization: str = FastAPIHeader("", alias="Authorization")):
    """把上一份好配置（config.json.bak）回滚回来。

    回滚到上一份能正常解析的配置（需登录）。日志里提示的 restore_last_good() 就是这个入口。
    """
    _require_auth(authorization)
    result = headless_config.restore_last_good()
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "回滚失败")
    return result


@app.put("/api/config")
def put_config(body: dict, authorization: str = FastAPIHeader("", alias="Authorization")):
    _require_auth(authorization)
    cfg = headless_config.load()
    for key, cast in headless_config.SAFE_KEYS.items():
        if key in body:
            try:
                value = body[key]
                if (
                    key in SECRET_FIELDS
                    and isinstance(value, str)
                    and (value.startswith("****") or not value.strip())
                    and str(cfg.get(key) or "")
                ):
                    continue
                # 界面偏好（ui_*）走自己的值域校验：类型错 → 400，
                # 数值越界 / 文案超长 → 夹取（滑杆拖到边界是正常操作，不该报错）。
                if key in headless_config.UI_PREF_DEFAULTS:
                    cfg[key] = headless_config.ui_pref_value(key, value)
                    continue
                cfg[key] = cast(value)
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            except Exception:  # noqa: BLE001
                raise HTTPException(400, f"{key} 格式不正确")
    headless_config.save(cfg)
    # 2026-10-11 热重载：这些设置里有一批是**热读**的（平台开关 / AI 总开关 /
    # 主人 QQ / 唤醒词 / 群白名单）。它们真正的载体是机器人侧 `.env` 与
    # `config/ai/astroswarm_live.json`，由 bot.write_env()/write_live_gates() 生成 ——
    # 以前只有"启动机器人"时才刷，所以控制台改完必须重启。这里存完就刷一次，
    # 机器人进程下次读就是新值（**不用重启**）。
    try:
        s_now = deploy.build_settings()
        from qbotmanager.core import bot as bot_mod

        bot_mod.write_env(s_now)
        bot_mod.write_live_gates(s_now)
        log.info("控制台保存配置后已刷新机器人侧热配置（平台开关/主人/唤醒词改完立即生效）")
    except Exception as exc:  # noqa: BLE001 —— 刷不动就让用户重启，不能把保存弄失败
        log.warning("刷新机器人侧热配置失败（这次改动可能要重启才生效）：%s", exc)
    out = {}
    for key in headless_config.SAFE_KEYS:
        value = cfg.get(key)
        out[key] = _mask(value) if key in SECRET_FIELDS and value else value
    # 一句人话口径（桌面端/控制台共用 core.restart_policy），前端据此标注
    out["restart_policy"] = restart_policy_mod.summary()
    # 这些键全是"设置类"，本轮已全部做成热读 → 不需要重启
    out["needs_restart"] = False
    return out


@app.get("/api/services/status")
def services_status(authorization: str = FastAPIHeader("", alias="Authorization")):
    # 回的是 bot_dir / 日志绝对路径与状态，要登录
    _require_auth(authorization)
    return services.status()


@app.post("/api/services/start")
def services_start(
    body: ServiceBody,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    _require_auth(authorization)
    try:
        return services.start(body.service)
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, str(exc))


@app.post("/api/services/stop")
def services_stop(
    body: ServiceBody,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    _require_auth(authorization)
    try:
        return services.stop(body.service)
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, str(exc))


@app.post("/api/services/restart")
def services_restart(
    body: ServiceBody,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    _require_auth(authorization)
    try:
        return services.restart(body.service)
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, str(exc))


@app.get("/api/bot/restart-policy")
def bot_restart_policy(authorization: str = FastAPIHeader("", alias="Authorization")):
    """「这次改动要不要重启机器人」的唯一口径（前端标注按钮/提示都用它）。

    口径一句话：**设置改了立即生效；代码改了才需要重启，且点一下就能重启。**
    """
    _require_auth(authorization)
    data = restart_policy_mod.summary()
    data["hot_reload_line"] = restart_policy_mod.HOT_LINE
    return data


@app.post("/api/bot/restart")
def bot_restart(authorization: str = FastAPIHeader("", alias="Authorization")):
    """一键重启机器人（控制台上所有"需要重启"的改动都点这个）。

    日志写清楚**每一步的耗时**（停止 / 启动 / 反向 WS 重连成功），
    就是为了避免"以为没重启"（老板 2026-10-11 明确要求）。
    """
    _require_auth(authorization)
    t0 = time.time()
    try:
        services.stop("bot")
    except Exception as exc:  # noqa: BLE001 —— 没在跑也算"停好了"
        log.warning("重启机器人：停止阶段异常（继续启动）：%s", exc)
    t_stop = time.time()
    try:
        res = services.start("bot")
    except Exception as exc:  # noqa: BLE001
        log.error("重启机器人失败：%s", exc)
        raise HTTPException(500, f"重启机器人失败：{exc}") from exc
    t_start = time.time()
    # 等到反向 WS 真连上（最多 30 秒）：连上了才算"重启完成"
    connected, waited = False, 0.0
    while waited < 30:
        try:
            if str(services.status().get("bot", {}).get("qq") or "") == "connected":
                connected = True
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
        waited = time.time() - t_start
    log.info(
        "重启机器人完成：停止 %.1fs｜启动 %.1fs｜反向 WS 重连 %s（%s）｜总 %.1fs",
        t_stop - t0, t_start - t_stop,
        "成功" if connected else "未在 30 秒内确认",
        f"{waited:.1f}s", time.time() - t0)
    return {
        "ok": True,
        "restarted": True,
        "pid": (res or {}).get("pid"),
        "seconds": {"stop": round(t_stop - t0, 1), "start": round(t_start - t_stop, 1),
                    "reconnect": round(waited, 1), "total": round(time.time() - t0, 1)},
        "qq_connected": connected,
        "note": ("机器人已重启，反向 WS 已重连"
                 if connected else
                 "机器人已重启，但反向 WS 还没连上（协议端稍后会自动重连；看日志确认）"),
    }


@app.get("/api/ai/personality")
def ai_personality_get(authorization: str = FastAPIHeader("", alias="Authorization")):
    _require_auth(authorization)
    s = deploy.build_settings()
    try:
        text = ai_config.read_personality(s)
    except Exception:  # noqa: BLE001
        text = ""
    return {"ok": True, "personality": str(text or "")}


@app.put("/api/ai/personality")
def ai_personality_put(
    body: PersonalityBody,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    _require_auth(authorization)
    text = (body.personality or "").strip()
    if len(text) > PERSONALITY_MAX_CHARS:
        raise HTTPException(400, f"人格设定最长 {PERSONALITY_MAX_CHARS} 字")
    s = deploy.build_settings()
    if not ai_config.save_personality(s, text):
        raise HTTPException(500, "保存失败")
    return {"ok": True, "needs_restart": True}


class AIModelsBody(BaseModel):
    api_url: str = ""
    api_key: str = ""
    protocol: str = "openai"


@app.post("/api/ai/models")
def ai_models_fetch(
    body: AIModelsBody,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    """按「接口地址 + API 密钥」拉服务商**真实**模型列表（与桌面端同一套逻辑）。

    服务商上新/改名很快，写死的预设很快就不够用（2026-09-29 桌面端反馈 DeepSeek
    预设已过期）。填完密钥自动拉一次，也可以点「获取模型」手动重拉；拉不到不阻断，
    预设与手填都留着，并把「哪一步失败」原样回给界面。
    """
    _require_auth(authorization)
    from qbotmanager.core import model_list as model_list_mod

    cfg = headless_config.load()
    url = (body.api_url or "").strip() or str(cfg.get("ai_base_url") or "").strip()
    key = (body.api_key or "").strip()
    # 面板回填的是打码值（****1234）：那不是真密钥，用服务器上存的那把
    if not key or key.startswith("****"):
        key = str(cfg.get("ai_api_key") or "").strip()
    if not key:
        raise HTTPException(400, "还没填 API 密钥")
    if not url:
        raise HTTPException(400, "还没填接口地址")
    try:
        result = model_list_mod.fetch_models(
            url, key, protocol=body.protocol or "openai")
    except Exception as exc:  # noqa: BLE001 —— 失败原因直接给用户看
        raise HTTPException(400, str(exc))
    models = [str(m) for m in (result.get("models") or [])][:200]
    if models:
        cache = cfg.get("ai_models_cache")
        if not isinstance(cache, dict):
            cache = {}
        cache[ai_config.normalize_api_url(url)] = models
        cfg["ai_models_cache"] = cache
        try:
            headless_config.save(cfg)
        except Exception:  # noqa: BLE001 —— 只是缓存，写不进去不拦用户
            pass
    return {
        "ok": True,
        "models": models,
        "count": len(models),
        "endpoint": str(result.get("endpoint") or ""),
    }


@app.get("/api/tools/status")
def tools_status(authorization: str = FastAPIHeader("", alias="Authorization")):
    # packs_dir 是绝对路径（未登录者不用知道服务器目录结构）
    _require_auth(authorization)
    return {
        "ok": True,
        "installed": tools.installed(),
        "packs_dir": str(tools.packs_dir()),
    }


@app.get("/api/tools/market")
def tools_market():
    try:
        plugins = _fetch_market()
    except Exception as exc:  # noqa: BLE001 —— 清单拿不到就明确报错，不假装成空市场
        return {"ok": False, "error": str(exc), "packs": []}
    packs = [p for p in plugins if p.get("kind") in ("tool-pack", "behavior-pack")]
    return {"ok": True, "packs": packs}


@app.post("/api/tools/install")
def tools_install(
    body: ToolInstallBody,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    _require_auth(authorization)
    entry = next(
        (
            p
            for p in _fetch_market()
            if p.get("id") == body.id
            and p.get("kind") in ("tool-pack", "behavior-pack")
        ),
        None,
    )
    if entry is None:
        raise HTTPException(404, "工具包不存在")
    try:
        return tools.install(entry)
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, str(exc))


@app.post("/api/tools/uninstall")
def tools_uninstall(
    body: ToolInstallBody,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    _require_auth(authorization)
    if str(body.id) not in {str(p.get("id")) for p in tools.installed()}:
        raise HTTPException(404, "能力包不存在")
    return tools.uninstall(body.id)


# ---- AI 插件工坊（生成模型独立于「AI 大脑」；生成的插件只跑沙箱）----


class WorkshopConfigBody(BaseModel):
    provider: str = ""
    api_key: str = ""
    api_url: str = ""
    model: str = ""


class WorkshopBuildBody(BaseModel):
    need: str = ""
    channels: list[str] = []


class WorkshopBuildRef(BaseModel):
    build_id: str = ""


class WorkshopPackBody(BaseModel):
    id: str = ""


# 生成结果只在内存里放一会儿：装的是"刚校验过的这一份"，
# 不让客户端把任意文件塞回来当插件装；最多留 5 份。
_workshop_builds: dict = {}
_MAX_BUILDS = 5


@app.get("/api/workshop/status")
def workshop_status(authorization: str = FastAPIHeader("", alias="Authorization")):
    """工坊状态：能不能生成 / 用哪家模型 / 已装清单 / 当前通道。"""
    _require_auth(authorization)
    s = deploy.build_settings()
    out = {
        "ok": True,
        "channels": workshop.active_channels(s),
        "installed": workshop.generated(s),
    }
    out.update(workshop.status(s))
    return out


@app.put("/api/workshop/config")
def workshop_config_put(
    body: WorkshopConfigBody,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    """保存工坊生成模型；非强模型（DeepSeek/ChatGPT/Claude/GLM/通义/Kimi）直接 400。"""
    _require_auth(authorization)
    s = deploy.build_settings()
    data = body.model_dump()
    key = str(data.get("api_key") or "")
    # 前端回填的是掩码（****1234），别把掩码当新 key 存进去
    if key.startswith("****") or not key.strip():
        data["api_key"] = str(workshop.config.load(s).get("api_key") or "")
    try:
        workshop.config.save(s, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    out = {"ok": True}
    out.update(workshop.status(s))
    return out


@app.post("/api/workshop/build")
def workshop_build(
    body: WorkshopBuildBody,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    """说需求 → 方案 → 生成 → 自动检查 → 自动重写 → 沙箱试跑（这一步还不装）。"""
    _require_auth(authorization)
    need = (body.need or "").strip()
    if not need:
        raise HTTPException(400, "先说说你想要什么功能")
    s = deploy.build_settings()
    channels = [str(c) for c in (body.channels or [])] or workshop.active_channels(s)
    try:
        built = workshop.build(s, need, channels=channels or None)
    except workshop.generator.WorkshopError as exc:
        raise HTTPException(400, exc.plain or str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, str(exc))
    token = secrets.token_urlsafe(9)
    _workshop_builds[token] = built
    while len(_workshop_builds) > _MAX_BUILDS:
        _workshop_builds.pop(next(iter(_workshop_builds)))
    out = {k: v for k, v in built.items() if k != "files"}
    out["build_id"] = token
    out["files"] = sorted((built.get("files") or {}).keys())
    return out


@app.post("/api/workshop/install")
def workshop_install(
    body: WorkshopBuildRef,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    _require_auth(authorization)
    built = _workshop_builds.get(str(body.build_id or ""))
    if built is None:
        raise HTTPException(404, "这次生成的结果已经过期，请重新生成一次")
    s = deploy.build_settings()
    try:
        result = workshop.install(s, built)
    except workshop.installer.InstallError as exc:
        raise HTTPException(400, str(exc))
    result["needs_restart"] = True
    return result


@app.get("/api/workshop/history")
def workshop_history(
    pack_id: str = "",
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    _require_auth(authorization)
    s = deploy.build_settings()
    items = workshop.history(s, pack_id) if pack_id else []
    return {"ok": True, "id": pack_id, "history": items}


@app.post("/api/workshop/rollback")
def workshop_rollback(
    body: WorkshopPackBody,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    _require_auth(authorization)
    s = deploy.build_settings()
    try:
        result = workshop.rollback(s, str(body.id or ""))
    except workshop.installer.InstallError as exc:
        raise HTTPException(400, str(exc))
    result["needs_restart"] = True
    return result


@app.post("/api/workshop/uninstall")
def workshop_uninstall(
    body: WorkshopPackBody,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    _require_auth(authorization)
    s = deploy.build_settings()
    try:
        result = workshop.uninstall(s, str(body.id or ""))
    except workshop.installer.InstallError as exc:
        raise HTTPException(400, str(exc))
    result["needs_restart"] = True
    return result


# ---------------- 工坊：分享 / 投稿 / 运行期自修复（2026-09-28） ----------------


class WorkshopNoteBody(BaseModel):
    id: str = ""
    note: str = ""
    code: str = ""        # 本次上传的邮箱验证码（每次都要验）
    username: str = ""    # 作者名（默认账号邮箱前缀）


class WorkshopImportBody(BaseModel):
    zip_b64: str = ""
    filename: str = "plugin.zip"


class WorkshopAccountBody(BaseModel):
    email: str = ""
    password: str = ""


class WorkshopSubmissionBody(BaseModel):
    id: int = 0


@app.get("/api/workshop/errors")
def workshop_errors(authorization: str = FastAPIHeader("", alias="Authorization")):
    """跑挂过的工坊插件（按插件汇总），界面拿它显示「让 AI 修一版」。"""
    _require_auth(authorization)
    s = deploy.build_settings()
    return {"ok": True, "errors": workshop.runtime_errors(s)}


@app.post("/api/workshop/repair")
def workshop_repair(
    body: WorkshopPackBody,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    """按真实报错让 AI 改一版；结果同样只能走 /api/workshop/install 安装。"""
    _require_auth(authorization)
    s = deploy.build_settings()
    try:
        built = workshop.fix_from_errors(s, str(body.id or ""))
    except workshop.repair.RepairError as exc:
        raise HTTPException(400, str(exc))
    except workshop.generator.WorkshopError as exc:
        raise HTTPException(400, exc.plain or str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, str(exc))
    token = secrets.token_urlsafe(9)
    _workshop_builds[token] = built
    while len(_workshop_builds) > _MAX_BUILDS:
        _workshop_builds.pop(next(iter(_workshop_builds)))
    out = {k: v for k, v in built.items() if k != "files"}
    out["build_id"] = token
    out["files"] = sorted((built.get("files") or {}).keys())
    return out


@app.post("/api/workshop/export")
def workshop_export(
    body: WorkshopPackBody,
    download: bool = False,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    """把装好的插件打包成 zip（发人 / 传官网都用这一份）。

    `?download=1` 时直接把 zip 字节回给浏览器（控制台点「下载到本地」走这条）；
    不带这个参数就照旧只回服务器上的路径（老调用方不受影响）。
    """
    _require_auth(authorization)
    s = deploy.build_settings()
    try:
        result = workshop.export_zip(s, str(body.id or ""))
    except workshop.publisher.PublishError as exc:
        raise HTTPException(400, str(exc))
    if not download:
        return result
    path = Path(str(result.get("archive") or result.get("path") or ""))
    if not path.is_file():
        raise HTTPException(404, "打包好了但找不到文件，请重新导出一次")
    pid = str(body.id or "pack")
    return Response(
        content=path.read_bytes(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{pid}.zip"'},
    )


# ---------------------------------------------------------- 工坊：异步生成 + 进度 + 取消
# 生成要跑模型，可能几分钟；同步接口下浏览器只能干等，也没法取消（老板 2026-10-03 提的）。
# 这里开个后台线程跑，把日志一行行攒起来给前端轮询；取消 = 在下一个检查点停住
# （**正在进行的模型调用没法中途掐断**，这点会在返回里说清楚，不假装秒停）。
_workshop_jobs: dict = {}
_workshop_jobs_lock = threading.Lock()
_MAX_JOBS = 20


class _WorkshopCancelled(Exception):
    """用户点了取消（在日志回调里抛出，worker 捕获后收尾）。"""


def _job_trim_locked() -> None:
    while len(_workshop_jobs) > _MAX_JOBS:
        _workshop_jobs.pop(next(iter(_workshop_jobs)))


@app.post("/api/workshop/build-async")
def workshop_build_async(
    body: WorkshopBuildBody,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    """开始生成（立刻返回 job_id，前端用 /api/workshop/job 轮询进度）。"""
    _require_auth(authorization)
    need = (body.need or "").strip()
    if not need:
        raise HTTPException(400, "先说说你想要什么功能")
    s = deploy.build_settings()
    channels = [str(c) for c in (body.channels or [])] or workshop.active_channels(s)
    job_id = secrets.token_urlsafe(9)
    job = {
        "id": job_id, "state": "running", "logs": [], "result": None,
        "error": "", "cancel": False, "started": time.time(), "need": need,
    }
    with _workshop_jobs_lock:
        _workshop_jobs[job_id] = job
        _job_trim_locked()

    def worker() -> None:
        def on_log(line) -> None:
            with _workshop_jobs_lock:
                job["logs"].append(str(line)[:300])
                del job["logs"][:-200]          # 只留最近 200 行
                if job["cancel"]:
                    raise _WorkshopCancelled()

        try:
            built = workshop.build(s, need, channels=channels or None, on_log=on_log)
        except _WorkshopCancelled:
            with _workshop_jobs_lock:
                job["state"] = "cancelled"
                job["error"] = "已取消"
            return
        except Exception as exc:  # noqa: BLE001
            with _workshop_jobs_lock:
                job["state"] = "error"
                job["error"] = str(exc)[:300]
            return
        token = secrets.token_urlsafe(9)
        _workshop_builds[token] = built
        while len(_workshop_builds) > _MAX_BUILDS:
            _workshop_builds.pop(next(iter(_workshop_builds)))
        out = {k: v for k, v in built.items() if k != "files"}
        out["build_id"] = token
        out["files"] = sorted((built.get("files") or {}).keys())
        with _workshop_jobs_lock:
            job["state"] = "done"
            job["result"] = out

    threading.Thread(target=worker, name=f"workshop-{job_id}", daemon=True).start()
    return {"ok": True, "job_id": job_id, "state": "running",
            "note": "生成要几分钟，可以关掉页面，回来再点「看结果」"}


@app.get("/api/workshop/job")
def workshop_job(
    job_id: str = "",
    since: int = 0,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    """看进度：state=running/done/error/cancelled，logs 是新增的日志行。"""
    _require_auth(authorization)
    with _workshop_jobs_lock:
        job = _workshop_jobs.get(str(job_id))
        if job is None:
            raise HTTPException(404, "这次生成记录已经过期，请重新生成")
        start = max(0, int(since or 0))
        logs = list(job["logs"][start:])
        return {
            "ok": True, "id": job["id"], "state": job["state"],
            "logs": logs, "log_count": len(job["logs"]),
            "stage": (job["logs"][-1] if job["logs"] else "准备中"),
            "elapsed": round(time.time() - float(job["started"] or 0), 1),
            "result": job["result"], "error": job["error"],
        }


@app.post("/api/workshop/cancel")
def workshop_cancel(
    body: WorkshopBuildRef,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    """取消这次生成（下一个检查点停下；正在跑的模型调用会自己结束）。"""
    _require_auth(authorization)
    job_id = str(body.build_id or "")
    with _workshop_jobs_lock:
        job = _workshop_jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "这次生成记录已经过期")
        if job["state"] != "running":
            return {"ok": True, "state": job["state"], "cancelled": False,
                    "note": "它已经结束了"}
        job["cancel"] = True
    return {"ok": True, "state": "cancelling", "cancelled": True,
            "note": "已发出取消：正在进行的模型调用会自己结束，随后就停"}


@app.post("/api/workshop/import")
def workshop_import(
    body: WorkshopImportBody,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    """导入别人给的插件包：静态检查 + 沙箱试跑全过才装。"""
    _require_auth(authorization)
    s = deploy.build_settings()
    raw = str(body.zip_b64 or "").strip()
    if "," in raw[:64] and raw.lstrip().startswith("data:"):
        raw = raw.split(",", 1)[1]
    if not raw:
        raise HTTPException(400, "没有收到插件包")
    try:
        data = base64.b64decode(raw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "插件包不是合法的 base64 数据") from exc
    name = re.sub(r"[^A-Za-z0-9._-]", "_", str(body.filename or "plugin.zip"))[:60]
    if not name.lower().endswith(".zip"):
        name += ".zip"
    tmp_dir = Path(tempfile.mkdtemp(prefix="ws_import_"))
    try:
        path = tmp_dir / name
        path.write_bytes(data)
        result = workshop.import_zip(s, path)
    except workshop.publisher.PublishError as exc:
        raise HTTPException(400, str(exc))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    result["needs_restart"] = True
    return result


@app.post("/api/workshop/upload")
def workshop_upload(
    body: WorkshopNoteBody,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    """上传到官网等站长审核（要用星群账号登录 + 本次上传的邮箱验证码）。"""
    _require_auth(authorization)
    s = deploy.build_settings()
    try:
        return workshop.upload(s, str(body.id or ""), note=str(body.note or ""),
                               code=str(body.code or ""),
                               username=str(body.username or ""))
    except workshop.publisher.PublishError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/workshop/upload-code")
def workshop_upload_code(authorization: str = FastAPIHeader("", alias="Authorization")):
    """要一条上传用的邮箱验证码（每次上传都要验，防刷）。"""
    _require_auth(authorization)
    s = deploy.build_settings()
    try:
        data = workshop.request_upload_code(s)
    except workshop.publisher.PublishError as exc:
        raise HTTPException(400, str(exc))
    data["account_email_masked"] = workshop.publisher.mask_email(data.get("email") or "")
    data["default_author"] = workshop.publisher.default_author()
    return data


@app.get("/api/workshop/submissions")
def workshop_submissions(authorization: str = FastAPIHeader("", alias="Authorization")):
    """我上传过的插件（含审核状态与驳回原因）。"""
    _require_auth(authorization)
    try:
        data = workshop.submissions()
    except workshop.publisher.PublishError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True,
            "submissions": workshop.publisher.describe(data.get("submissions") or [])}


@app.post("/api/workshop/submissions/withdraw")
def workshop_submissions_withdraw(
    body: WorkshopSubmissionBody,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    """撤回自己还没审的投稿。"""
    _require_auth(authorization)
    try:
        return workshop.withdraw_submission(int(body.id or 0))
    except workshop.publisher.PublishError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/workshop/account")
def workshop_account(authorization: str = FastAPIHeader("", alias="Authorization")):
    """当前登录的星群账号（上传插件要用它）。"""
    _require_auth(authorization)
    from qbotmanager.core import account as _account

    data = _account.load_account() or {}
    return {"ok": True, "email": str(data.get("email") or ""),
            "logged_in": bool(data.get("token"))}


@app.post("/api/workshop/account")
def workshop_account_login(
    body: WorkshopAccountBody,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    """无头端登录星群账号（只用来给插件投稿，跟控制台自己的登录无关）。"""
    _require_auth(authorization)
    from qbotmanager.core import account as _account

    email = str(body.email or "").strip()
    if not email or not body.password:
        raise HTTPException(400, "邮箱和密码都要填")
    data = _account.login(email, str(body.password))
    if not data.get("ok"):
        raise HTTPException(400, str(data.get("detail") or "登录失败"))
    return {"ok": True, "email": str(data.get("email") or email)}


@app.post("/api/workshop/account/logout")
def workshop_account_logout(authorization: str = FastAPIHeader("", alias="Authorization")):
    _require_auth(authorization)
    from qbotmanager.core import account as _account

    _account.clear_account()
    return {"ok": True}
@app.get("/api/qweather/config")
def qweather_config(authorization: str = FastAPIHeader("", alias="Authorization")):
    _require_auth(authorization)
    s = deploy.build_settings()
    cfg = tool_packs_mod.load_qweather_config(s)
    key_path = cfg.get("private_key_path") or ""
    return {
        "ok": True,
        "configured": bool(
            cfg.get("project_id") and cfg.get("credential_id")
            and key_path and Path(key_path).exists()),
        "project_id": cfg.get("project_id") or "",
        "credential_id": cfg.get("credential_id") or "",
        "api_host": cfg.get("api_host") or "",
        "private_key_path": key_path,
    }


@app.post("/api/qweather/generate-key")
def qweather_generate_key(
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    _require_auth(authorization)
    s = deploy.build_settings()
    cfg = tool_packs_mod.load_qweather_config(s)
    key_path = str(Path(s.root) / "qweather_ed25519_private.pem")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
    except Exception:  # noqa: BLE001
        raise HTTPException(500, "服务器缺少 cryptography，请先安装依赖")
    priv = Ed25519PrivateKey.generate()
    try:
        Path(key_path).parent.mkdir(parents=True, exist_ok=True)
        with open(key_path, "wb") as f:
            f.write(priv.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()))
        os.chmod(key_path, 0o600)
    except OSError as exc:  # noqa: BLE001
        raise HTTPException(500, f"私钥写入失败: {exc}")
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    tool_packs_mod.save_qweather_config(
        s, cfg.get("project_id", ""), cfg.get("credential_id", ""), key_path,
        cfg.get("api_host", ""))
    return {"ok": True, "public_key": pub_pem, "private_key_path": key_path}


class QWeatherSaveBody(BaseModel):
    api_host: str = ""
    project_id: str = ""
    credential_id: str = ""


@app.post("/api/qweather/save")
def qweather_save(
    body: QWeatherSaveBody,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    _require_auth(authorization)
    s = deploy.build_settings()
    cfg = tool_packs_mod.load_qweather_config(s)
    tool_packs_mod.save_qweather_config(
        s,
        body.project_id or cfg.get("project_id", ""),
        body.credential_id or cfg.get("credential_id", ""),
        cfg.get("private_key_path", ""),
        body.api_host or cfg.get("api_host", ""),
    )
    return {"ok": True}


@app.get("/api/logs/stream")
def logs_stream(
    token: str = "",
    name: str = logs.LOG_FILE_DEFAULT,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    """实时日志流。日志里有 AI key / 微信 token / 用户对话，必须鉴权。

    EventSource 不能自定义请求头，所以额外接受 ?token=<登录 token>；
    控制台通过 src/api.js 的 authUrl() 自动拼上。

    `?name=` 选日志来源（headless / nonebot）。**只接受名字，不接受路径**：
    名字到文件的映射在 logs.LOG_FILES 这张白名单里，非法名字直接 400。

    必须用异步生成器（logs.afollow）：同步生成器交给 StreamingResponse 会走
    anyio.to_thread，日志空闲时线程卡在 next() 里，客户端断开也收不回（每次断开漏一个线程）。
    afollow 在事件循环上等待，取消立刻生效，并自带 10 分钟上限（到点结束，前端重连）。
    """
    _require_auth(token or authorization)
    try:
        src, log_path = logs.resolve_log(name)
    except ValueError:
        raise HTTPException(400, f"未知的日志名，只能是：{logs.LOG_NAME_LIST}") from None

    async def gen():
        yield "data: " + logs.tail(log_path, 200).replace("\n", "\ndata: ") + "\n\n"
        async for chunk in logs.afollow(log_path):
            yield "data: " + chunk.replace("\n", "\ndata: ") + "\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# 带内容哈希的文件名（Vite 产物）：index-BwGvgdAR.js / index-7NLoSNam.css / logo-a1b2c3d4.png。
# 名字里带哈希 = 内容一变名字就变，所以可以放心让浏览器缓存一年。
_HASHED_ASSET_RE = re.compile(
    r"-[A-Za-z0-9_-]{8,}\.(?:js|mjs|css|map|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|otf)$",
    re.IGNORECASE,
)


class ConsoleStaticFiles(StaticFiles):
    """托管 console-dist，并给不同种类的文件配不同的缓存策略。

    ⚠ 为什么必须这么干（2026-10-10 老板报的 bug）：
      老板「用以前的浏览器打开控制台，没显示登录面」。根因不是前端缺密码屏，
      而是 starlette 的 StaticFiles **只发 etag / last-modified，一个 Cache-Control
      都不发**。浏览器对这种响应走"启发式缓存"（RFC 9111 §4.2.2：按 Last-Modified
      往后推算 10% 的时长），升级后 index.html 还是旧的 —— 旧 HTML 指向旧 bundle，
      于是新版界面（含密码屏）压根没被加载。加一条 no-cache 就让浏览器每次
      带 If-None-Match 回源问一次：没变就 304（省流量），变了立刻拿到新页面。

    三档策略：
      · 入口 HTML（/、*.html）→ `no-cache, must-revalidate`
        "no-cache" 不是"不缓存"，是"每次用之前必须回源校验"；配合 etag 仍是 304 秒回。
      · `assets/*-<hash>.{js,css,…}` → 一年 immutable（哈希变了名字就变，永远不会读到旧内容）
      · 其它（favicon、不带哈希的图标等）→ 同样 no-cache：升级后可能被替换，不能长缓存
    """

    async def get_response(self, path: str, scope):
        # ⚠ 必须 async：starlette 1.6 的 StaticFiles.get_response 是协程，
        #    写成同步函数会拿到"未被 await 的协程"当响应，静态资源整片 500。
        response = await super().get_response(path, scope)
        name = str(path or "").rsplit("/", 1)[-1]
        ctype = str(response.headers.get("content-type") or "").lower()
        if "text/html" in ctype or name.lower().endswith(".html") or str(path) in (".", ""):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        elif _HASHED_ASSET_RE.search(name):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


def _console_dist() -> Path:
    """控制台前端产物目录。

    控制台前端产物的查找顺序：环境变量 → 代码包旁的 console-dist → 开发机默认路径。
    都没有时 `GET /` 直接 404，所以这个顺序必须覆盖安装脚本铺产物的位置。
    """
    env = os.environ.get("ASTROSWARM_CONSOLE_DIST", "").strip()
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for base in (here.parents[1], here.parents[2]):      # <安装目录>/console-dist
        cand = base / "console-dist"
        if cand.exists():
            return cand
    return here.parents[2] / "console-dist"              # 兜底：不存在时 GET / 会 404 并写日志


CONSOLE_DIST = _console_dist()
# 市场清单走 HTTPS + Ed25519 验签（清单地址可用 ASTROSWARM_MARKET_URL 覆盖，
# 自建镜像要把域名加进 QBM_MARKET_ALLOW_HOSTS；签名始终用客户端内置公钥校验）
MARKET_URL = os.environ.get(
    "ASTROSWARM_MARKET_URL", "https://astroswarm.cn/plugins/market.json"
)
_market_cache = {"at": 0.0, "data": []}
_login_hits = {}
# 「查邮箱注册状态」的按 IP 限流桶（2026-10-10）。比登录那套更严：这是**枚举**接口，
# 正常用户一次登录最多点两三次「下一步」，而扫邮箱的人会一秒一次。
_CHECK_EMAIL_MAX = 10          # 每个 IP 每窗口最多 10 次
_CHECK_EMAIL_WINDOW = 600      # 窗口：10 分钟
_check_email_hits = {}

SECRET_FIELDS = {"qq_app_secret", "qq_app_token", "ai_api_key", "vision_api_key"}


def _mask(value) -> str:
    value = str(value or "")
    if not value:
        return ""
    return "****" + (value[-4:] if len(value) > 4 else "")


def _require_auth(authorization: str) -> None:
    token = (authorization or "").removeprefix("Bearer ").strip()
    expected = str(headless_config.load().get("account_token") or "")
    if not expected or not token or not secrets.compare_digest(expected, token):
        raise HTTPException(401, "请先登录")


def _login_rate_limit(ip: str) -> None:
    now = time.time()
    lst = [t for t in _login_hits.get(ip, []) if now - t < 60]
    if len(lst) >= 5:
        raise HTTPException(429, "登录尝试过于频繁，请稍后再试")
    lst.append(now)
    _login_hits[ip] = lst


def _check_email_rate_limit(ip: str) -> None:
    """查邮箱注册状态的按 IP 限流（老板要求：别被人拿去批量扫邮箱）。

    单独一个桶、不复用登录那套：登录是 5 次/分钟且要留着给人试密码，
    这个接口只该被点几次，所以按 10 次/10 分钟卡。
    """
    now = time.time()
    lst = [t for t in _check_email_hits.get(ip, []) if now - t < _CHECK_EMAIL_WINDOW]
    if len(lst) >= _CHECK_EMAIL_MAX:
        raise HTTPException(429, "查询太频繁了，过几分钟再试")
    lst.append(now)
    _check_email_hits[ip] = lst


def _client_ip(request) -> str:
    """真实客户端 IP：只有对端是本机（nginx 反代）时才信转发头。

    为什么不能无条件信 XFF：服务直接暴露时任何人伪造一个 XFF 就能绕过限流，
    等于没限流；而只信 socket 对端的话，反代后面所有用户又共用一个桶。

    取 XFF 的**最后一段**，并优先用 nginx 的 X-Real-IP。
    nginx 用的是 `$proxy_add_x_forwarded_for`（客户端自带值 + 真实 IP 追加在末尾），首段
    完全由调用方控制 —— 取首段等于伪造一个 X-Forwarded-For 就能无限次试密码。
    """
    peer = str(getattr(getattr(request, "client", None), "host", "") or "")
    if peer in ("127.0.0.1", "::1", "localhost"):
        headers = getattr(request, "headers", None)
        real = str(headers.get("x-real-ip") or "").strip() if headers is not None else ""
        if real:
            return real
        xff = str(headers.get("x-forwarded-for") or "") if headers is not None else ""
        segments = [s.strip() for s in xff.split(",") if s.strip()]
        if segments:
            return segments[-1]
    return peer or "unknown"


def _fetch_market() -> list:
    now = time.time()
    if _market_cache["data"] and now - _market_cache["at"] < 60:
        return _market_cache["data"]
    from qbotmanager.core import plugin_market

    plugins = plugin_market.fetch_market(MARKET_URL, timeout=10)
    if not plugins:
        raise RuntimeError(
            plugin_market.LAST_ERROR or "插件市场清单不可用（签名校验失败或网络不通）"
        )
    _market_cache.update(at=now, data=plugins)
    return plugins


@app.get("/api/plugins")
def plugins_list():
    try:
        items = [
            p for p in _fetch_market() if p.get("kind") != "tool-pack"
        ]
        return {"ok": True, "plugins": items}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "plugins": []}


@app.post("/api/plugins/install")
def plugins_install(
    body: dict,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    _require_auth(authorization)
    # 匹配规则（2026-10-04 加固）：id 优先，其次 name（大小写不敏感，去空格）。
    # 为什么：控制台「官网市场」列表走的是白名单字段，早期没带 id，前端只能拿名字来装；
    # 只认 id 的话那颗「安装」按钮会 404 —— 前端已经按 {id, name} 发，这里两种都认更稳。
    want_id = str((body or {}).get("id") or "").strip()
    want_name = str((body or {}).get("name") or "").strip().lower()
    entry = None
    for p in _fetch_market():
        if p.get("kind") == "tool-pack":
            continue
        pid = str(p.get("id") or "").strip()
        pname = str(p.get("name") or "").strip().lower()
        if (want_id and pid == want_id) or (want_name and pname == want_name):
            entry = p
            break
    if entry is None:
        raise HTTPException(404, "插件不存在（市场里没有这个 id 或名字）")
    try:
        result = plugins.install(entry)
        services.restart("bot")
        return result
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, str(exc))


@app.post("/api/plugins/uninstall")
def plugins_uninstall(
    body: dict,
    authorization: str = FastAPIHeader("", alias="Authorization"),
):
    _require_auth(authorization)
    pid = str(body.get("id") or "")
    # 不存在的 id 直接 404：不要返回 ok，也不要顺手把机器人重启一遍
    if pid not in {str(p.get("id")) for p in plugins.installed()}:
        raise HTTPException(404, "插件不存在")
    try:
        result = plugins.uninstall(pid)
        services.restart("bot")
        return result
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, str(exc))


# 控制台 1:1 之后补的接口（已装插件清单 / 依赖扫描与安装 / 日志下载）。
# 必须挂在 StaticFiles 之前：它挂在 "/" 上，后注册的路由永远匹配不到。
from . import console_ext  # noqa: E402

console_ext.register(app)


if CONSOLE_DIST.exists():
    app.mount(
        "/",
        # ConsoleStaticFiles（不是原版 StaticFiles）：入口 HTML 必须 no-cache，
        # 否则浏览器启发式缓存会把旧 index.html 一直用下去（老板 2026-10-10 的 bug）。
        ConsoleStaticFiles(directory=str(CONSOLE_DIST), html=True),
        name="console",
    )
else:
    # 不再静默：客户机上最可能的原因就是没把前端产物放进来
    logging.getLogger("astroswarm").warning(
        "控制台前端产物目录不存在：%s —— 浏览器打开会 404。"
        "请把 console-dist 放到该路径，或设置 ASTROSWARM_CONSOLE_DIST 指向它。",
        CONSOLE_DIST,
    )

    @app.get("/")
    def _console_missing():
        return {
            "ok": False,
            "error": "控制台前端产物未找到",
            "hint": "把 console-dist 放到 %s，或设置环境变量 ASTROSWARM_CONSOLE_DIST" % CONSOLE_DIST,
        }
