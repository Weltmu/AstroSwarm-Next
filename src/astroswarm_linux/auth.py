"""星群账号授权：登录/认领/本地授权状态（对接现有账号服务）。"""
import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("astroswarm.auth")

from . import headless_config, plans

# 账号服务地址：配置 account_base > 环境变量 ASTROSWARM_ACCOUNT_BASE > 官方托管服务。
# 不要写死本机开发地址：客户机器上没人监听那个端口，登录会一路 401。
ACCOUNT_BASE_ENV = "ASTROSWARM_ACCOUNT_BASE"
ACCOUNT_BASE_DEFAULT = "https://astroswarm.cn/api/account"
ACCOUNT_PATH_PREFIX = "/api/account"

# 微信通道免费开放（2026-10-01 老板拍板）：True = 所有人可用，不再需要开通验证。
# 与桌面端 core/license.py 的 WECHAT_FREE_FOR_ALL 同一口径，改回 False 就恢复按档位判定。
WECHAT_FREE_FOR_ALL = True


def account_base() -> str:
    """当前生效的账号服务地址（原样返回，可能带 /api/account 后缀）。

    优先级：config.json 的 account_base > 环境变量 ASTROSWARM_ACCOUNT_BASE > 官方默认。

    注意「配置里存着官方默认地址」这件事：老版本 headless_config.DEFAULTS 把官方地址
    写进了默认值，于是**每个客户机的 config.json 里都有一个"配置值"**，
    环境变量与 systemd 的 Environment= 会被它压住。
    所以这里把"等于官方默认"的配置值也当成没配 —— 已经装过的机器同样能靠环境变量改。
    """
    try:
        cfg = str(headless_config.load().get("account_base") or "").strip()
    except Exception:  # noqa: BLE001 —— 配置读不出来就退回环境变量/默认值
        cfg = ""
    if cfg and cfg.rstrip("/") != ACCOUNT_BASE_DEFAULT.rstrip("/"):
        return cfg.rstrip("/")
    env = str(os.environ.get(ACCOUNT_BASE_ENV) or "").strip()
    if env:
        return env.rstrip("/")
    return ACCOUNT_BASE_DEFAULT


def account_url(path: str) -> str:
    """拼接口 URL（path 形如 /api/account/login）。

    account_base 允许两种写法：`https://astroswarm.cn` 或
    `https://astroswarm.cn/api/account`（默认值就是后者），这里统一去重前缀。
    """
    base = account_base()
    if base.endswith(ACCOUNT_PATH_PREFIX):
        base = base[: -len(ACCOUNT_PATH_PREFIX)]
    return base + path


def _open_json(req) -> dict:
    """发请求并解析 JSON；把「连不上账号服务」和「账号密码错」分开报。"""
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8", "replace"))
            detail = str(body.get("detail") or body.get("error") or body.get("message") or "")
        except Exception:  # noqa: BLE001
            detail = ""
        raise RuntimeError(detail or f"账号服务返回 HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"连不上星群账号服务（{account_base()}）：{exc.reason}。"
            f"自建账号服务请改配置 account_base 或环境变量 {ACCOUNT_BASE_ENV}"
        ) from exc


def _post(path, body, token=None, client_ip=""):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if client_ip:
        # 带上真实来访 IP：无头端从本机再 POST 一次账号服务，不带转发头的话账号服务
        # 只看得到 127.0.0.1，所有控制台登录会共用一个限流桶。
        # 账号服务只在直连方是回环时才采信这两个头，且取 XFF 的最后一段。
        headers["X-Real-IP"] = str(client_ip)
        headers["X-Forwarded-For"] = str(client_ip)
    req = urllib.request.Request(
        account_url(path),
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    return _open_json(req)


def _loads(raw) -> dict:
    """响应体 → dict（解析不了就回空字典，别让一句 HTML 报错页炸掉调用方）。"""
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _detail_of(data: dict) -> str:
    """账号服务的中文原因（HTTPException 的 detail）。"""
    return str(data.get("detail") or data.get("error") or data.get("message") or "")


def _post_raw(path, body, client_ip=""):
    """POST 一次，把「状态码 + 响应体」原样带回来（4xx 不抛异常）。

    为什么要有它：查注册状态要看**状态码**（409 = 已注册），而 `_post`/`_open_json`
    会把 4xx 折成一句 RuntimeError、状态码就丢了。
    """
    headers = {"Content-Type": "application/json"}
    if client_ip:
        headers["X-Real-IP"] = str(client_ip)
        headers["X-Forwarded-For"] = str(client_ip)
    req = urllib.request.Request(
        account_url(path),
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, _loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _loads(exc.read())
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"连不上星群账号服务（{account_base()}）：{exc.reason}"
        ) from exc


# ── 查邮箱注册状态（2026-10-10 老板要求：控制台登录按"注册过没有"分流）──────────
# 账号服务**没有**"查注册状态"接口（2026-10-10 全量探过：/api/account/check、
# /exists、/check-email、/registered、/lookup… 一律 404，/me 要 token）。能问出
# 注册状态的只有三条路，取舍如下：
#   · send-code / forgot-code → 对**已注册**邮箱会真发一封信（副作用），不用；
#   · login                   → 每问一次记一次失败，同一邮箱错 5 次锁 15 分钟
#                               （账号服务 LOGIN_FAIL_LIMIT=5）—— 用户多点几次
#                               「下一步」就把自己锁在门外，绝不能用；
#   · register                → **先查库、后验码**：已注册直接 409「该邮箱已注册」，
#                               没注册才回 400「验证码错误或已过期」。不发信、
#                               不写库、不碰任何账号数据 —— 就用它。
# 探测器固定用一个 6 位**非数字**验证码：账号服务的验证码永远是 6 位数字
# （send-code 里 f"{random.randint(0, 999999):06d}"），compare_digest 必然不相等，
# 所以这条路**不可能**真把账号建出来。唯一代价是账号服务按 IP 记一次"今日注册
# 次数"（DAILY_REGISTER_LIMIT=20/天），频率由 api.py 的按 IP 限流压住。
# ⚠ 契约依赖：账号服务 register() 里"先查 users 再验码"的**顺序**不能反 ——
#   反了会把老用户误判成未注册（那边改动要一起改这里）。
PROBE_CODE = "------"
PROBE_PASSWORD = "AstroProbe-000000"      # ≥8 位；只在 register 分支用，永远不落库


def check_email(email: str, client_ip: str = "") -> dict:
    """查这个邮箱注册过没有：{"ok": True, "registered": bool, "email": addr}。

    ⚠ 安全取舍（老板 2026-10-10 拍板：体验优先）：这等于**允许枚举某邮箱是否注册过**。
    这个能力账号服务本来就给（register 409「该邮箱已注册」、login 404「该邮箱未注册」、
    forgot-code 404 同理），控制台只是把它做成一步可见的体验；为了不变成批量扫邮箱的
    工具，api.py 那边对这个接口**单独按 IP 限流**（_check_email_rate_limit）。

    问不出来（网络不通 / 账号服务报错 / 契约变了）一律**抛错**，绝不返回
    registered=False —— 把"问不到"当成"没注册"，老用户会看到注册界面。
    """
    addr = str(email or "").strip()
    if not addr or "@" not in addr:
        raise RuntimeError("邮箱格式不对")
    status, data = _post_raw(
        "/api/account/register",
        {"email": addr, "code": PROBE_CODE, "password": PROBE_PASSWORD},
        client_ip=client_ip,
    )
    detail = _detail_of(data)
    if status == 409 or "已注册" in detail:
        return {"ok": True, "registered": True, "email": addr}
    if status == 400 and "验证码" in detail:
        return {"ok": True, "registered": False, "email": addr}
    if status == 200 and data.get("token"):
        # 理论上到不了（非数字验证码不可能匹配 6 位数字验证码）。真到了说明账号服务
        # 改了验证码规则、而且**已经建了号** —— 必须报出来，不能装作"查到了"。
        raise RuntimeError("查询结果异常，请稍后再试")
    raise RuntimeError(detail or f"账号服务返回 HTTP {status}")


def send_code(email: str, client_ip: str = "") -> dict:
    """给邮箱发 6 位验证码（账号服务负责发信）。

    2026-10-06 加：官网登录是"邮箱 + 验证码"，控制台以前只能密码登录 ——
    老板要求控制台也走同一套（弹窗里要有验证码），所以把发码透传过来。
    """
    addr = str(email or "").strip()
    if not addr or "@" not in addr:
        raise RuntimeError("邮箱格式不对")
    data = _post("/api/account/send-code", {"email": addr}, client_ip=client_ip)
    return {"ok": True, **(data or {})}


def register(email: str, code: str, password: str, client_ip: str = "") -> dict:
    """注册星群账号（邮箱 + 6 位验证码 + 密码 ≥8 位），成功后顺手登录。

    2026-10-06 加：查清账号服务的真实契约 —— `/api/account/login` 只认邮箱+密码，
    验证码那条路是 **注册**（`/api/account/register`）。所以控制台里那个页签
    应该是"注册新账号"，不是"验证码登录"。
    """
    addr = str(email or "").strip()
    c = str(code or "").strip()
    pw = str(password or "")
    if "@" not in addr:
        raise RuntimeError("邮箱格式不对")
    if len(c) != 6:
        raise RuntimeError("验证码是 6 位数字")
    if len(pw) < 8:
        raise RuntimeError("密码至少 8 位")
    data = _post("/api/account/register",
                 {"email": addr, "code": c, "password": pw}, client_ip=client_ip)
    cfg = headless_config.load()
    cfg["account_email"] = data.get("email") or addr
    cfg["account_token"] = data.get("token") or ""
    cfg["plan"] = data.get("plan") or "none"
    cfg["plan_expires_at"] = float(data.get("plan_expires_at") or 0)
    cfg["entitlement_sig"] = str(data.get("entitlement_sig") or "")
    cfg["entitlement_machine"] = str(data.get("entitlement_machine") or "")
    headless_config.save(cfg)
    _store_account(cfg, data)      # 同时写本机账号文件，昵称/头像才同步得到
    return {
        "ok": True,
        "email": cfg["account_email"],
        "plan": cfg["plan"],
        "plan_expires_at": cfg["plan_expires_at"],
    }


def _store_account(acc: dict, data: dict) -> None:
    """把登录结果同时写进"本机账号文件"（qbotmanager.core.account）。

    为什么（2026-10-06 老板反馈"控制台昵称头像没跟官网同步"）：控制台原来只写了
    headless_config 里的 account_token，而读昵称/头像走的是 account 模块的
    account.json + avatar.jpg，没有 token 就 refresh_profile() 失败 ——
    表现就是账号卡片里昵称头像一直是「—」。
    """
    try:
        from qbotmanager.core import account as account_mod

        merged = dict(acc or {})
        merged.update({
            "email": acc.get("account_email") or acc.get("email") or "",
            "token": acc.get("account_token") or "",
            "plan": acc.get("plan") or "none",
            "plan_expires_at": acc.get("plan_expires_at") or 0,
            "entitlement_sig": acc.get("entitlement_sig") or "",
            "entitlement_machine": acc.get("entitlement_machine") or "",
        })
        # 账号服务返回的昵称/签名（有就带上，没有就留空，随后 refresh_profile 会补）
        for key in ("nickname", "signature"):
            if isinstance(data, dict) and data.get(key):
                merged[key] = str(data.get(key))
        account_mod.save_account(merged)
    except Exception as exc:  # noqa: BLE001 —— 写不进去不影响登录本身
        log.warning("写本机账号文件失败（昵称/头像可能同步不到）：%s", exc)


def login_or_register(email: str, password: str, code: str = "",
                      client_ip: str = "") -> dict:
    """一条路走完："没注册就自动注册，然后登录"。

    老板 2026-10-06 要的体验：用户只管填邮箱 + 密码（+ 邮箱验证码），
    不用先想"我是要登录还是注册"。
    做法：**先按登录试** ——
      · 密码对 → 直接登录，`mode="login"`；
      · 登录失败且带了验证码 → 说明多半还没这个账号，拿验证码去注册（密码就用他填的），
        注册成功 `mode="register"`（前端可提示"账号已创建并登录"）；
      · 注册也失败（例如账号其实已存在、只是密码打错）→ **把登录那条错原样抛回**，
        不能拿"注册失败"误导用户。
    """
    addr = str(email or "").strip()
    pw = str(password or "")
    c = str(code or "").strip()
    if "@" not in addr:
        raise RuntimeError("邮箱格式不对")
    if not pw:
        raise RuntimeError("请填密码（至少 8 位）")
    try:
        out = login(addr, pw, client_ip=client_ip)
        out["mode"] = "login"
        return out
    except Exception as login_err:  # noqa: BLE001
        if not c:
            # 没带验证码：只有"这个邮箱还没注册"才提示发码自动注册；
            # 密码打错就照实说密码错，别让人以为再发个码就能进。
            msg = str(login_err)
            if "未注册" in msg or "不存在" in msg:
                msg += "（点「发送验证码」拿到 6 位码再点登录，会自动帮你注册）"
            raise RuntimeError(msg)
        if len(c) != 6:
            raise RuntimeError("验证码是 6 位数字")
        try:
            out = register(addr, c, pw, client_ip=client_ip)
        except Exception as reg_err:  # noqa: BLE001
            # 带了码还注册不上，多半是码不对/过期 —— 这时注册的错更有用，
            # 别再拿"邮箱或密码错误"糊弄人。
            raise RuntimeError(str(reg_err))
        out["mode"] = "register"
        return out


def login(email: str, password: str = "", client_ip: str = "",
          code: str = "") -> dict:
    """登录。密码与验证码二选一（验证码对应账号服务的 code 字段）。

    2026-10-06 修：不能把空 password 一起发过去 —— 账号服务对 password 有
    min_length=1 校验，验证码登录时会回一坨 pydantic 报错（老板实测踩到：
    "登录失败：[{'type': 'string_too_short', 'loc': ['body','password']…"）。
    规矩：**哪个有值发哪个**，两个都没有才让本地报错。
    """
    body = {"email": email}
    if str(password or "").strip():
        body["password"] = password
    if str(code or "").strip():
        body["code"] = str(code).strip()
    if "password" not in body and "code" not in body:
        raise RuntimeError("要么填密码，要么填验证码")
    data = _post("/api/account/login", body, client_ip=client_ip)
    cfg = headless_config.load()
    cfg["account_email"] = data.get("email") or email
    cfg["account_token"] = data.get("token") or ""
    cfg["plan"] = data.get("plan") or "none"
    cfg["plan_expires_at"] = float(data.get("plan_expires_at") or 0)
    cfg["entitlement_sig"] = str(data.get("entitlement_sig") or "")
    cfg["entitlement_machine"] = str(data.get("entitlement_machine") or "")
    headless_config.save(cfg)
    _store_account(cfg, data)      # 同时写本机账号文件，昵称/头像才同步得到
    return {
        "ok": True,
        "email": cfg["account_email"],
        "plan": cfg["plan"],
        "plan_expires_at": cfg["plan_expires_at"],
    }


def claim(machine_id: str) -> dict:
    cfg = headless_config.load()
    token = cfg.get("account_token") or ""
    if not token:
        raise RuntimeError("未登录星群账号")
    data = _post(
        "/api/account/device/claim",
        {"machine_id": machine_id},
        token=token,
    )
    # 权益签名由账号服务下发，认领后再拉一次 me（claim 的响应里没有签名）
    try:
        got = me(token)
        if isinstance(got, dict) and got.get("ok") is not False:
            data = {**data, **{k: v for k, v in got.items() if k.startswith("entitlement_")}}
    except Exception:  # noqa: BLE001
        pass
    cfg["plan"] = data.get("plan") or "none"
    cfg["plan_expires_at"] = float(data.get("plan_expires_at") or 0)
    cfg["entitlement_sig"] = str(data.get("entitlement_sig") or "")
    cfg["entitlement_machine"] = str(data.get("entitlement_machine") or "")
    headless_config.save(cfg)
    return {
        "ok": True,
        "plan": cfg["plan"],
        "plan_expires_at": cfg["plan_expires_at"],
        "license": bool(data.get("license")),
    }


def me(token: str) -> dict:
    """拉当前账号信息（含权益签名）。"""
    req = urllib.request.Request(
        account_url("/api/account/me"),
        headers={"Authorization": "Bearer " + token},
    )
    return _open_json(req)


def verify_entitlement(cfg: dict | None = None) -> bool:
    """校验无头配置里的 plan / 已购插件是否带账号服务的 Ed25519 签名。

    与桌面端同一套规范文本（license.entitlement_payload），验签不过一律按未开通处理。
    """
    cfg = headless_config.load() if cfg is None else cfg
    sig = str(cfg.get("entitlement_sig") or "").strip()
    if not sig:
        return False
    try:
        import base64

        from nacl.signing import VerifyKey

        from qbotmanager.core.license import PUBLIC_KEY_HEX, entitlement_payload

        payload = entitlement_payload(
            cfg.get("entitlement_machine") or "",
            cfg.get("plan"), cfg.get("plan_expires_at"), cfg.get("owned_plugins"))
        VerifyKey(bytes.fromhex(PUBLIC_KEY_HEX)).verify(payload.encode(), base64.b64decode(sig))
        return True
    except Exception:  # noqa: BLE001
        return False


def feature_gate(cfg: dict | None = None) -> dict:
    """本机权益闸门。

    两个互不替代的判定：

    - `member` = 微信通道是否可用。
    - `full` / `all_plugins` = 历史字段：2026-09 起能力包全部免费，
      运行时不再按档位放行，保留只为兼容旧调用方。

    2026-10-01 老板拍板：**微信通道不再需要开通验证，所有人都能用**（与桌面端同一口径）。
    要恢复收费闸门，把 WECHAT_FREE_FOR_ALL 改成 False 即可（下面的签名校验与档位判断都还在）。
    """
    cfg = headless_config.load() if cfg is None else cfg
    signed = verify_entitlement(cfg)
    plan = str(cfg.get("plan") or "none").lower() if signed else "none"
    exp = float(cfg.get("plan_expires_at") or 0) if signed else 0.0
    allp = plans.all_plugins(plan, exp) if signed else False
    member = (plans.is_member(plan, exp) or allp) if signed else False
    if WECHAT_FREE_FOR_ALL:
        return {
            "full": allp,
            "member": True,
            "all_plugins": allp,
            "plan": plan,
            "plan_expires_at": exp,
            "email": cfg.get("account_email") or "",
            "reason": "微信通道已免费开放（扫码即用，不需要开通）",
        }
    if not signed:
        return {
            "full": False,
            "member": False,
            "all_plugins": False,
            "plan": "none",
            "plan_expires_at": 0,
            "email": cfg.get("account_email") or "",
            "reason": "QQ 通道可用；微信通道未开通（本机权益未通过签名校验）",
        }
    return {
        # 微信通道看 member（见 deploy/_env 与 wechat_state）；full 只作兼容保留
        "full": allp,
        "member": member,
        "all_plugins": allp,
        "plan": plan,
        "plan_expires_at": exp,
        "email": cfg.get("account_email") or "",
        "reason": "" if member else "QQ 通道可用；微信通道未开通",
    }


def logout() -> None:
    cfg = headless_config.load()
    cfg["account_token"] = ""
    cfg["account_email"] = ""
    cfg["plan"] = "none"
    cfg["plan_expires_at"] = 0
    headless_config.save(cfg)
