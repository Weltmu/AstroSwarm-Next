"""控制台密码登录 + 设备会话（防别人篡改数据，15 天免密码）。

老板 2026-10-07 原话："再给程序加一个密码登录的功能（防止其他人篡改数据，
有 15 天免密码登录验证）"。

为什么要有这一层（它和星群账号登录是**两件事**，都保留）：
  · 星群账号 token（auth.py）= "这台机器买了什么权益"；
  · 控制台密码会话（本模块）= "现在坐在浏览器前的这个人，有没有资格动这台服务器"。
  无头端的控制台跑在 http://IP:7860，同局域网谁都能打开 —— 只有账号 token 时，
  账号 token 本身又存在浏览器里，等于没有门槛。所以再加一道只属于这台服务器的密码。

安全底线（逐条对应实现）：
  · 密码只存 scrypt 哈希 + 每实例随机 salt，明文永不落盘、永不进日志、永不回显；
  · 会话 token 用 secrets.token_urlsafe(32) 生成，**服务端只存它的 sha256**——
    会话文件泄了也拿不到能用的 token；
  · 过期时间只由服务端判定（_now() 与 expires_at 比较），前端传什么都不信；
  · 密码/token 一律不进 URL（前端走 X-Console-Token 头；日志流/二维码这两个
    "浏览器不给带 header"的例外走 ?token=，与原有账号 token 同一处理）。
"""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path

from . import platform_info

log = logging.getLogger("astroswarm.console_session")

# 免密码登录时长（老板点名 15 天）与不勾选时的时长
REMEMBER_SECONDS = 15 * 24 * 3600
DEFAULT_SECONDS = 12 * 3600
# 密码规则：至少 8 位（与星群账号注册同口径）
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 256
# scrypt 参数：n=2**14 在树莓派这类机器上约 50~100ms，够用又不至于把登录拖成卡顿
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32

# 会话上限：防止登录文件无限长大（正常一台机器就几个浏览器）
MAX_SESSIONS = 50

# 登录限流：按来源 IP，沿用 api.py 那套口径（5 次/分钟）
LOGIN_WINDOW = 60
LOGIN_MAX_TRIES = 5
_login_hits: dict[str, list] = {}


def _paths() -> tuple[Path, Path]:
    """两个文件都放 config_home（与 config.json 同目录，备份/迁移一起带走）。

    每次现算而不是模块级常量：测试用 monkeypatch 换 config_home，写死路径会写到真机上去。
    """
    home = platform_info.config_home()
    return home / "console_auth.json", home / "console_sessions.json"


def _write_private(path: Path, payload: dict) -> None:
    """原子写 + 0600：密码哈希与会话 token 都属于"别人读到就等于拿到控制台"的东西。

    先写临时文件再 os.replace，避免写一半断电留下半个 JSON（那会让密码"凭空消失"，
    表现就是设过密码的机器又变回"首次部署"，任何人都能重新设一个进来）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    # os.open 时就把权限定成 0600：不能先按默认权限创建再 chmod，
    # 那中间有一小段别人可读的窗口。
    # ⚠ 0600 只在 Linux 上真的生效 —— Windows/NTFS 没有 POSIX 位，chmod 是空操作
    #   （本机开发环境就是如此）。真正的部署目标是 Linux，所以这里按 Linux 写。
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:  # noqa: BLE001
        try:
            os.unlink(str(tmp))
        except OSError:
            pass
        raise
    os.replace(str(tmp), str(path))
    try:
        os.chmod(str(path), 0o600)
    except OSError:  # Windows 上可能不支持，忽略
        pass


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        # 读坏了**不能**当成"没设过密码"：那等于把门敞开（谁都能重设一个密码进去）。
        # 如实报错，让运维去修文件。
        log.error("控制台鉴权文件读不出来（%s）：%s", path, exc)
        raise


# ─────────────────────────────────────────────────────────── 密码

def password_rules(password: str) -> str:
    """返回不合规的中文原因；合规返回空串。"""
    pw = str(password or "")
    if len(pw) < MIN_PASSWORD_LEN:
        return "密码至少 %d 位" % MIN_PASSWORD_LEN
    if len(pw) > MAX_PASSWORD_LEN:
        return "密码太长了（最多 %d 位）" % MAX_PASSWORD_LEN
    if pw.strip() != pw:
        return "密码首尾不要有空格"
    low = pw.lower()
    if low in ("12345678", "123456789", "1234567890", "password", "passw0rd",
               "qwertyui", "admin123", "abc12345", "11111111", "00000000",
               "iloveyou", "astroswarm"):
        return "这个密码太常见了，换一个别人猜不到的"
    if len(set(pw)) == 1:
        return "密码不能是同一个字符重复"
    if _is_sequential(pw):
        return "不要用连续的数字/字母当密码（如 12345678、abcdefgh）"
    if low in ("qwertyuiop", "qwertyui", "asdfghjkl", "zxcvbnm", "1qaz2wsx",
               "qazwsxedc", "1q2w3e4r"):
        return "这是键盘上的顺排，太容易猜了"
    if re.fullmatch(r"\d+", pw):
        # 纯数字即使不连续也太弱（8 位纯数字 = 1 亿种，跑一会儿就穷举完了）
        return "密码别只用数字，建议字母 + 数字混着来"
    return ""


def _is_sequential(pw: str) -> bool:
    """12345678 / 98765432 / abcdefgh / hgfedcba 这类顺子判为弱密码。"""
    if len(pw) < 4:
        return False
    steps = {ord(b) - ord(a) for a, b in zip(pw, pw[1:])}
    return steps in ({1}, {-1})


def _hash_password(password: str, salt_hex: str = "") -> dict:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.scrypt(
        str(password).encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
    )
    return {
        "algo": "scrypt",
        "salt": salt.hex(),
        "n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P,
        "dklen": _SCRYPT_DKLEN,
        "hash": digest.hex(),
    }


def _verify_password(password: str, rec: dict) -> bool:
    """比对密码。用 hmac.compare_digest 做定时安全比较。"""
    try:
        salt = bytes.fromhex(str(rec.get("salt") or ""))
        want = str(rec.get("hash") or "")
        if not salt or not want:
            return False
        digest = hashlib.scrypt(
            str(password).encode("utf-8"), salt=salt,
            n=int(rec.get("n") or _SCRYPT_N), r=int(rec.get("r") or _SCRYPT_R),
            p=int(rec.get("p") or _SCRYPT_P), dklen=int(rec.get("dklen") or _SCRYPT_DKLEN),
        )
        return hmac.compare_digest(digest.hex(), want)
    except Exception as exc:  # noqa: BLE001
        log.warning("校验控制台密码时出错：%s", exc)
        return False


def verify_password_for_login(password: str) -> bool:
    """登录用：拿输入密码和存的哈希比。没设过密码一律 False（调用方会另行提示）。

    和 _verify_password 分开取个名字，是为了让"这里是唯一校验登录密码的入口"这件事
    在调用处一眼可见（密码进到这里就没了，不返回、不记录、不留存）。
    """
    auth_path, _ = _paths()
    rec = _read_json(auth_path)
    stored = rec.get("password") if isinstance(rec.get("password"), dict) else None
    if not stored:
        return False
    ok = _verify_password(password, stored)
    del password
    return ok


def is_password_set() -> bool:
    auth_path, _ = _paths()
    try:
        rec = _read_json(auth_path)
    except Exception:  # noqa: BLE001 —— 文件坏了：当作"已设置"，宁可进不去也别把门敞开
        return True
    return bool(isinstance(rec.get("password"), dict) and rec["password"].get("hash"))


def set_password(password: str, *, require_old: str = "") -> dict:
    """首次设置 / 改密码。改密码时 require_old 必须是当前密码。

    改密码会**作废除当前会话以外的所有会话**（老板要求），调用方把 keep_token 传进来。
    """
    reason = password_rules(password)
    if reason:
        raise ValueError(reason)
    auth_path, _ = _paths()
    rec = {}
    if auth_path.exists():
        try:
            rec = _read_json(auth_path)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("控制台鉴权文件读不出来，先修好 %s（%s）" % (auth_path, exc))
    existing = rec.get("password") if isinstance(rec.get("password"), dict) else None
    if existing:
        if not require_old:
            raise ValueError("已经设置过控制台密码了")
        if not _verify_password(require_old, existing):
            raise ValueError("当前密码不对")
    rec["password"] = _hash_password(password)
    rec["password_set_at"] = int(time.time())
    _write_private(auth_path, rec)
    # 明文用完即弃，别留在这个栈帧之外的任何地方
    del password
    return {"ok": True, "password_set_at": rec["password_set_at"]}


# ─────────────────────────────────────────────────────────── 会话

def _token_digest(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _now() -> float:
    return time.time()


def _load_sessions() -> dict:
    _, sess_path = _paths()
    try:
        data = _read_json(sess_path)
    except Exception:  # noqa: BLE001 —— 会话文件坏了：当作没有有效会话（重新登录即可）
        return {}
    items = data.get("sessions")
    return items if isinstance(items, dict) else {}


def _save_sessions(items: dict) -> None:
    _, sess_path = _paths()
    now = _now()
    # 顺手清掉过期的，别让文件一直长
    alive = {
        k: v for k, v in items.items()
        if isinstance(v, dict) and float(v.get("expires_at") or 0) > now
    }
    if len(alive) > MAX_SESSIONS:
        # 只留最近到期的那些（正常用不到这么多，多出来的是别人试出来的）
        ordered = sorted(alive.items(), key=lambda kv: float(kv[1].get("expires_at") or 0),
                         reverse=True)[:MAX_SESSIONS]
        alive = dict(ordered)
    _write_private(sess_path, {"sessions": alive})


def create_session(*, remember: bool = True, device_label: str = "",
                   client_ip: str = "") -> dict:
    """签发会话 token。**只在调用方已经验过密码之后调用**。"""
    token = secrets.token_urlsafe(32)
    now = _now()
    ttl = REMEMBER_SECONDS if remember else DEFAULT_SECONDS
    expires_at = now + ttl
    items = _load_sessions()
    items[_token_digest(token)] = {
        "created_at": now,
        "expires_at": expires_at,
        "remember": bool(remember),
        "device_label": str(device_label or "")[:80],
        # 只记 IP 便于运维看"谁开着"，不记 User-Agent 全文（没必要存一堆指纹）
        "client_ip": str(client_ip or "")[:64],
    }
    _save_sessions(items)
    return {"token": token, "expires_at": expires_at, "remember": bool(remember),
            "ttl": ttl}


def verify(token: str) -> dict | None:
    """校验会话 token；有效返回会话信息（含 expires_at），否则 None。

    过期判定只在这里做（服务端时间），前端说"我还没过期"不作数。
    """
    if not token:
        return None
    items = _load_sessions()
    rec = items.get(_token_digest(token))
    if not isinstance(rec, dict):
        return None
    if float(rec.get("expires_at") or 0) <= _now():
        return None
    return rec


def revoke(token: str) -> bool:
    """作废一个会话（退出控制台）。返回是否真的删掉了。"""
    if not token:
        return False
    items = _load_sessions()
    key = _token_digest(token)
    if key not in items:
        return False
    items.pop(key, None)
    _save_sessions(items)
    return True


def revoke_others(keep_token: str) -> int:
    """改密码时用：作废除 keep_token 之外的全部会话。"""
    items = _load_sessions()
    keep = _token_digest(keep_token) if keep_token else ""
    before = len(items)
    items = {k: v for k, v in items.items() if k == keep}
    _save_sessions(items)
    return before - len(items)


def revoke_all() -> int:
    """作废所有会话（忘记密码后重置时用）。返回作废了几个。"""
    items = _load_sessions()
    count = len(items)
    if count:
        _save_sessions({})
    return count


def clear_password() -> None:
    """清除控制台密码（救援用：只有能在服务器上执行命令的人做得到）。

    老板 2026-10-10 要的"忘记密码怎么办"：网页上**不提供**找回通道，
    只能在服务器上跑 `astroswarm console-reset-password`。
    """
    auth_file, _ = _paths()
    try:
        auth_file.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise RuntimeError(f"删不掉 {auth_file}：{exc}") from exc


def revoke_all() -> int:
    items = _load_sessions()
    n = len(items)
    _save_sessions({})
    return n


def session_info(token: str) -> dict:
    """ /api/console/session 的返回体（不含任何密码/token 信息）。"""
    rec = verify(token)
    authed = rec is not None
    return {
        "ok": True,
        "need_setup": not is_password_set(),
        "authed": authed,
        "expires_at": float(rec.get("expires_at") or 0) if rec else 0,
        "remember": bool(rec.get("remember")) if rec else False,
        "device_label": str(rec.get("device_label") or "") if rec else "",
    }


# ─────────────────────────────────────────────────────────── 登录限流

def rate_limit(ip: str) -> None:
    """登录/设密码的限流：同一个 IP 一分钟最多 5 次。超了抛 RuntimeError(中文)。"""
    now = _now()
    hits = [t for t in _login_hits.get(ip, []) if now - t < LOGIN_WINDOW]
    if len(hits) >= LOGIN_MAX_TRIES:
        log.warning("控制台密码尝试过多，已临时拒绝：%s", ip or "unknown")
        raise RuntimeError("尝试过多，请稍后再试")
    hits.append(now)
    _login_hits[ip] = hits


def clear_rate_limit() -> None:
    _login_hits.clear()
