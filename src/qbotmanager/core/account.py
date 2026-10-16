# -*- coding: utf-8 -*-
"""星群账号：程序端登录网站账号，并同步本机激活状态到服务器。"""
import base64
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from .license import SERVER_WEB, _appdata_dir

logger = logging.getLogger("qbotmanager")
TIMEOUT = 15
# 头像统一到 1024×1024 / JPEG 质量 88 —— 与服务端 AVATAR_SIDE=1024、官网裁剪器
# AV_SIDE=1024 + AV_QUALITY=0.88 完全一致（三处对齐，任何一端换了尺寸都要一起改）。
# 本机上传也按这个尺寸压，服务端就不会再重新编码一次、画质不二次损失。
AVATAR_SIDE = 1024
AVATAR_MAX_BYTES = 3 * 1024 * 1024
AVATAR_JPEG_QUALITY = 88
# 服务端上限 2MB（解出来的原始字节）且只收 data:image/(png|jpe?g|webp)
AVATAR_UPLOAD_MAX_BYTES = 2 * 1024 * 1024
# 头像显示尺寸：**账号窗口**用 96（与官网个人中心 .me__av 的 96px 对齐，用户明确要求）。
# 侧栏那个小胶囊要单独用小尺寸：侧栏固定 180 宽，96 的圆放进去比例失调、会把导航顶下去
# （2026-10-07 用户反馈"头像咋还变大了"），所以侧栏用 SIDEBAR 这个数。
AVATAR_PREVIEW_SIDE = 96
# 侧栏胶囊：官网个人中心的圆 ≈88 逻辑像素，侧栏只有 180 宽，取 64 最接近官网比例又不挤导航
# （实测 44/56/64/72 四档：胶囊高 62/74/82/90，侧栏溢出都为 0；64 是观感与占位最平衡的一档）
AVATAR_SIDEBAR_SIDE = 44   # 侧栏那一个（2026-10-10 老板反馈 64 太大，改回 44；
# 账号窗口里用的是 AVATAR_PREVIEW_SIDE=96，两者别混）


def account_file():
    return _appdata_dir() / "account.json"


def avatar_file():
    """头像单独落盘（JPEG）：account.json 保持小文件，热路径读取不受拖累。"""
    return _appdata_dir() / "avatar.jpg"


def load_account() -> dict | None:
    p = account_file()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("email") and data.get("token"):
            return data
    except (ValueError, OSError) as e:
        logger.warning("读取账号失败: %s", e)
    return None


def save_account(data: dict):
    """写入账号文件（只保留已知字段，避免把服务端响应整包摊到磁盘上）。"""
    clean = {
        "email": str(data.get("email") or ""),
        "token": str(data.get("token") or ""),
        "login_at": float(data.get("login_at") or time.time()),
        "nickname": str(data.get("nickname") or ""),
        "signature": str(data.get("signature") or ""),
        "plan": str(data.get("plan") or ""),
    }
    try:
        account_file().write_text(json.dumps(clean, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        logger.warning("保存账号失败: %s", e)


def clear_account():
    """退出登录：账号与头像一起清掉，否则下次未登录还会显示上一个人的头像。"""
    try:
        p = account_file()
        if p.exists():
            p.unlink()
        a = avatar_file()
        if a.exists():
            a.unlink()
    except OSError as e:
        logger.warning("清除账号失败: %s", e)


def save_avatar(data_url: str) -> bool:
    """把服务端返回的 data URL（base64 图片）解成文件。失败只记日志，不影响登录。"""
    raw = str(data_url or "")
    if "," in raw and raw.lstrip().startswith("data:"):
        raw = raw.split(",", 1)[1]
    if not raw:
        return False
    try:
        blob = base64.b64decode(raw, validate=False)
    except (ValueError, TypeError) as e:
        logger.warning("头像解码失败: %s", e)
        return False
    return _write_avatar(blob)


def _write_avatar(blob: bytes) -> bool:
    """头像落盘。内容没变就不写 —— 前台每 60 秒对一次资料，别每分钟都去动这个文件。"""
    if not blob or len(blob) > AVATAR_MAX_BYTES:
        logger.warning("头像体积异常，已忽略：%s 字节", len(blob or b""))
        return False
    try:
        path = avatar_file()
        if path.exists() and path.read_bytes() == blob:
            return True
        path.write_bytes(blob)
        _drop_stale_avatars(keep=path)
        return True
    except OSError as e:
        logger.warning("保存头像失败: %s", e)
        return False


def _drop_stale_avatars(keep) -> None:
    """换了新头像就把旧的删掉，同目录只留当前这一张。

    老板 2026-10-07 要求："刷新出新头像旧的就自动删除" ——
    否则界面、缩略图缓存或别的进程可能读到上一个版本，看起来就是"没换过来"。
    """
    try:
        for path in keep.parent.glob("avatar*"):
            if path == keep or not path.is_file():
                continue
            try:
                path.unlink()
                logger.info("删掉旧头像文件: %s", path.name)
            except OSError as e:  # noqa: PERF203 —— 删不掉不影响使用
                logger.warning("旧头像删不掉（不影响使用）: %s %s", path.name, e)
    except OSError as e:
        logger.warning("清理旧头像失败（不影响使用）: %s", e)


def _avatar_blob(value: str) -> bytes:
    """把资料里的 avatar 字段变成字节。

    data URL 直接解；http(s) 地址或站内绝对路径就去下一张（官网个人中心那种写法）。
    拉不到一律回空字节 —— 头像失败绝不能影响昵称/签名这些文字信息。
    """
    raw = str(value or "").strip()
    if not raw:
        return b""
    if raw.startswith("data:"):
        body = raw.split(",", 1)[1] if "," in raw else ""
        if not body:
            return b""
        try:
            return base64.b64decode(body, validate=False)
        except (ValueError, TypeError):
            return b""
    if raw.startswith("http://") or raw.startswith("https://") or raw.startswith("/"):
        url = raw if raw.startswith("http") else SERVER_WEB + raw
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AstroSwarm/1.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read(AVATAR_MAX_BYTES + 1)
        except Exception as e:  # noqa: BLE001
            logger.info("头像地址下载失败（不影响昵称）：%s", e)
            return b""
    return b""


def avatar_data_url(path) -> tuple[str, str]:
    """把本地图片压成服务端要的 data URL（正方形 1024×1024 JPEG）。

    返回 (data_url, error)：成功时 error 为空串。压缩放在客户端做，
    否则手机拍的原图（几 MB）会被服务端 2MB 上限直接拒掉。
    """
    try:
        from PIL import Image, ImageOps  # 只在真传头像时加载，避免拖慢启动
    except Exception as e:  # noqa: BLE001
        return "", "本机缺少图片处理组件，请更新到最新版：" + str(e)[:80]
    try:
        with Image.open(path) as src:
            src.load()
            img = ImageOps.exif_transpose(src) or src
            img = img.convert("RGB")
            w, h = img.size
            if w * h > 40_000_000:
                return "", "图片尺寸过大（超过 4000 万像素），请换一张"
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            img = img.crop((left, top, left + side, top + side)).resize(
                (AVATAR_SIDE, AVATAR_SIDE), Image.LANCZOS)
            import io

            out = io.BytesIO()
            img.save(out, format="JPEG", quality=AVATAR_JPEG_QUALITY, optimize=True)
            blob = out.getvalue()
    except Exception as e:  # noqa: BLE001
        return "", "这张图片读不出来，请换一张：" + str(e)[:80]
    if not blob:
        return "", "图片内容为空，请换一张"
    if len(blob) > AVATAR_UPLOAD_MAX_BYTES:
        return "", "图片过大，请换一张小一点的"
    return "data:image/jpeg;base64," + base64.b64encode(blob).decode(), ""


def upload_avatar(token: str, data_url: str) -> dict:
    """把头像传到星群账号（与官网个人中心同一个接口），服务端会再裁一次。"""
    return _post("/api/account/avatar", {"avatar": data_url}, token=token)


def display_name(acc: dict | None = None) -> str:
    """界面上显示的名字：昵称优先，其次邮箱前缀。"""
    acc = acc if acc is not None else load_account()
    if not acc:
        return ""
    name = str(acc.get("nickname") or "").strip()
    if name:
        return name
    email = str(acc.get("email") or "")
    return email.split("@", 1)[0] if "@" in email else email


def _apply_profile(data: dict) -> dict | None:
    """把服务端响应里的昵称/头像写回本地账号，返回更新后的账号。"""
    acc = load_account()
    if not acc:
        return None
    # 昵称按服务端为准（对方清空了就跟着清空），键不在响应里才不动
    if "nickname" in data:
        acc["nickname"] = str(data.get("nickname") or "").strip()
    if "signature" in data:
        acc["signature"] = str(data.get("signature") or "").strip()
    if data.get("plan"):
        acc["plan"] = str(data.get("plan"))
    save_account(acc)
    avatar = str(data.get("avatar") or "")
    if avatar:
        blob = _avatar_blob(avatar)
        if blob:
            _write_avatar(blob)
    return acc


def _looks_like_profile(data) -> bool:
    """这份响应是不是一份可用的账号资料。

    ⚠ 2026-10-06 修（老板反馈"官网换了头像，软件客户端没变"）：账号服务的
    `/api/account/me` 成功时**只回资料字段**（email / nickname / signature / plan / avatar…），
    没有 `ok` 字段；失败时（`_get` 的兜底）才带 `ok: False`。
    老代码写的是 `if not data.get("ok"): return None` —— 于是每一次**成功**的 /me
    都被当成失败丢掉，昵称与头像在本机永远同步不过来，表现就是"只在启动时读一次"。
    """
    if not isinstance(data, dict) or not data:
        return False
    if data.get("ok") is False:
        return False
    return bool(data.get("email") or data.get("nickname") or data.get("token")
                or data.get("display_name") or data.get("ok"))


def refresh_profile(acc: dict | None = None) -> dict | None:
    """拉一次 /me，把昵称与头像同步到本地（官网或个人中心换的，这边跟上）。

    未登录（没有账号文件 / 没有 token）直接返回 None：**一个请求都不发**。
    网络不通、服务端报错一律静默返回 None —— 界面继续用上一次的资料，不弹错。
    """
    acc = acc if acc is not None else load_account()
    if not acc or not str(acc.get("token") or ""):
        return None
    try:
        data = me(str(acc["token"]))
    except Exception as e:  # noqa: BLE001 —— 拉不到就当没这回事，绝不打断界面
        logger.info("同步账号资料失败（保留本机那份）：%s", e)
        return None
    if not _looks_like_profile(data):
        return None
    return _apply_profile(data)


def _post(path: str, payload: dict, token: str = ""):
    """POST 到星群账号服务。

    失败时返回值里带 `status`（HTTP 状态码）：调用方靠它区分
    「这个邮箱还没注册（404）」和「密码错了（401）」，别用错误文案去猜。
    """
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(SERVER_WEB + path, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode("utf-8", "replace"))
            if isinstance(data, dict):
                data.setdefault("status", e.code)
                return data
            return {"ok": False, "detail": f"HTTP {e.code}", "status": e.code}
        except Exception:  # noqa: BLE001
            return {"ok": False, "detail": f"HTTP {e.code}", "status": e.code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": "无法连接星群服务器: " + str(e), "network": True}


def _get(path: str, token: str):
    req = urllib.request.Request(SERVER_WEB + path, headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return {"ok": False, "detail": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": "无法连接星群服务器: " + str(e), "network": True}


def login(email: str, password: str) -> dict:
    """用网站账号登录；成功后在本地保存 token（10 天免登录，与服务端一致）。

    邮箱没注册时服务端返回 404（`status == 404`）—— 调用方据此进入"注册"流程，
    密码错是 401，两者不能混。详见 2026-10-06 的「登录即注册」。
    """
    data = _post("/api/account/login", {"email": (email or "").strip(), "password": password or ""})
    if data.get("ok"):
        save_account({
            "email": data.get("email", ""),
            "token": data.get("token", ""),
            "login_at": time.time(),
        })
        # 登录响应就带昵称/头像，直接落盘，界面不用再等一次请求
        _apply_profile(data)
    return data


def needs_register(res: dict) -> bool:
    """服务端是不是在说「这个邮箱还没注册」——只有这种情况才允许去注册。

    两道保险：优先看 HTTP 状态码 404；老的兜底接口没有状态码时，
    再看 detail 里有没有「未注册」字样。已注册的邮箱绝不会走到注册分支。
    """
    if not isinstance(res, dict) or res.get("ok"):
        return False
    if int(res.get("status") or 0) == 404:
        return True
    detail = str(res.get("detail") or res.get("error") or "")
    return "未注册" in detail


def send_code(email: str) -> dict:
    """注册/重置密码用的邮箱验证码（服务端真发一封邮件，5 分钟有效）。"""
    return _post("/api/account/send-code", {"email": (email or "").strip()})


def register(email: str, code: str, password: str, signature: str = "") -> dict:
    """注册并直接登录：成功后按登录同款落盘 token（用户不用再登一次）。"""
    data = _post("/api/account/register", {
        "email": (email or "").strip(),
        "code": (code or "").strip(),
        "password": password or "",
        "signature": (signature or "").strip(),
    })
    if data.get("ok"):
        save_account({
            "email": data.get("email", ""),
            "token": data.get("token", ""),
            "login_at": time.time(),
        })
        _apply_profile(data)
    return data


def me(token: str) -> dict:
    return _get("/api/account/me", token)


def redeem(token: str, code: str) -> dict:
    """兑换码兑换：只有账号服务知道码是真是假，本机只负责转发 + 之后重拉权益。

    与无头端 /api/plugins/redeem 走同一个账号服务接口，桌面端与 Linux 端行为一致。
    """
    return _post("/api/account/redeem", {"code": (code or "").strip()}, token=token)


def sync(token: str, machine_id: str, license_key: str, expires_at: float = 0) -> dict:
    """把本机激活状态（机器码/激活码/到期时间）同步到网站账号。"""
    return _post("/api/account/program-sync", {
        "machine_id": machine_id,
        "license": license_key,
        "expires_at": float(expires_at or 0),
    }, token=token)


def claim_device(token: str, machine_id: str) -> dict:
    """邮箱登录后认领本机：绑定授权，新设备认领自动踢掉旧设备。"""
    return _post("/api/account/device/claim", {"machine_id": machine_id}, token=token)


def check_device(token: str, machine_id: str) -> dict:
    """查询本机是否仍是账号当前活跃设备（被踢检测）。"""
    q = urllib.parse.urlencode({"machine_id": machine_id})
    return _get(f"/api/account/device/check?{q}", token)


def device_pair_request(machine_id: str) -> dict:
    """本机发起配对：拿 6 位配对码 + poll_token，用户在官网已登录状态下输码授权。

    适用于「不想在客户端输邮箱密码」的场景：官网已登录 -> 输码 -> 本机绑定完成。
    """
    return _post("/api/account/device/request", {"machine_id": (machine_id or "").strip()})


def device_pair_poll(poll_token: str) -> dict:
    """轮询配对结果；status=approved 时一次性返回 token / 昵称 / 头像。"""
    q = urllib.parse.urlencode({"poll_token": (poll_token or "").strip()})
    return _get(f"/api/account/device/poll?{q}", "")
