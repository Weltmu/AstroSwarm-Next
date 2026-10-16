"""无头端控制台补充接口（控制台 1:1 之后补的后端能力）。

控制台的「插件管理 / 依赖管理 / 日志 / 设置」几页原来有按钮只能弹「无头端暂不支持」，
这里把能做的补上：

    GET  /api/plugins/installed     已装插件（plugin.json）+ 本地插件目录 + pyproject 记录的商店插件
    POST /api/plugins/install-pypi  开发者模式：从 PyPI 包名安装插件（pip + 写进 bot 的 pyproject）
    GET  /api/console/dev-mode      开发者模式开关（控制台自己的状态，存 config_home/console.json）
    POST /api/console/dev-mode      切换开发者模式（要鉴权）
    GET  /api/deps/status           依赖扫描/安装的当前状态（控制台轮询这个，插件安装也共用）
    POST /api/deps/scan             后台扫描插件声明的依赖，找出缺失项
    POST /api/deps/install          后台用机器人 venv 的 pip 安装缺失依赖
    GET  /api/logs/download         下载日志文件（?name=headless|nonebot，见 logs.LOG_FILES）
    GET  /api/persona/template      下载人格卡模板（桌面端「下载模板」的同一份 JSON）
    GET  /api/persona/guide         人格卡模板说明全文（控制台弹层展示）
    GET  /api/persona/list          本地人设工坊的人设列表
    POST /api/persona/import        导入人设（JSON / zip / Markdown 文本）
    POST /api/persona/activate      启用本地人设（写机器人人格 + 关硬编码档案）
    POST /api/persona/deactivate    停用本地人设（人格回内置默认）
    POST /api/persona/uninstall     卸载本地人设（必须 confirm=true）

2026-10-04 补齐的通道与日志接口（与桌面端飞书页 / QQ 页 / 服务页对齐）：

    GET  /api/feishu/status         飞书通道状态（只有 *_set 布尔，凭证永不回显）
    PUT  /api/feishu/config         写飞书凭证（空串 = 不改，__clear__ = 清空）
    POST /api/feishu/install-adapter 装飞书适配器（nonebot-adapter-feishu）
    GET  /api/telegram/status       纸飞机通道（桌面端只有占位页，这里如实回 supported:false）
    GET  /api/qq/official           QQ 官方凭证 / 收消息模式 / 群名单与回话白名单
    PUT  /api/qq/official           写官方凭证与通道（secret 规则同飞书）
    GET  /api/logs/retention        日志保留天数 + 占用 + 已归档文件名
    PUT  /api/logs/retention        改保留天数（1~365，默认 10 天）
    POST /api/logs/cleanup-now      立刻归档 + 清理过期日志

安装方式（api.py 末尾、StaticFiles 挂载之前）：

    from . import console_ext
    console_ext.register(app)

为什么不把路由直接写进 api.py：api.py 在服务器上已经和仓库里的两份副本都不完全一致，
改动越少越好；这个模块自带状态与线程，删掉它只是少几个接口，不影响原有功能。

为什么开发者模式开关单独存一个文件：桌面端的 developer_mode 在 settings 里、由 api.py 的
/api/config 读写，而那份 api.py 是服务器上唯一在跑、且与仓库不一致的文件；把开关放在
config_home/console.json 里，这个模块就能自洽，不用再动 api.py。
"""
import base64
import time
import urllib.request
import functools
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response

from . import deploy, headless_config, logs, platform_info
from . import plugins as hl_plugins

# 扫描/安装是后台线程 + 轮询，避免一个请求挂几十秒被 nginx 掐掉
_state_lock = threading.Lock()
_state = {
    "running": False,
    "kind": "",          # scan | install | plugin
    "stage": "",
    "started_at": 0.0,
    "finished_at": 0.0,
    "checked": 0,        # 扫到的依赖条数
    "missing": [],       # 缺失依赖（原始写法）
    "installed": [],     # 本次安装成功的包
    "package": "",       # 本次从 PyPI 装的插件包名
    "error": "",
    "log": [],           # 最近若干行输出，控制台直接展示
}
_INSTALL_TIMEOUT = 900
LOG_TAIL = 40


def _now() -> float:
    return time.time()


def _set(**kw) -> None:
    with _state_lock:
        _state.update(kw)


def _log(line: str) -> None:
    with _state_lock:
        _state["log"] = (_state["log"] + [str(line).rstrip()])[-LOG_TAIL:]
        _state["stage"] = str(line)


def _require_auth(authorization: str) -> None:
    """复用 api.py 的鉴权（延迟导入：api.py 是在末尾 import 本模块的）。"""
    from .api import _require_auth as check

    check(authorization)


def _settings():
    return deploy.build_settings()


def _bot_python(settings) -> Path | None:
    for cand in (
        Path(settings.bot_dir) / ".venv" / "bin" / "python",       # Linux/macOS
        Path(settings.bot_dir) / ".venv" / "bin" / "python3",
        Path(settings.bot_dir) / ".venv" / "Scripts" / "python.exe",  # Windows（本机自测用）
    ):
        if cand.exists():
            return cand
    return None


# ------------------------------------------------------------ 开发者模式开关
_DEV_LOCK = threading.Lock()
_PKG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*(\[[A-Za-z0-9,._+-]*\])?([<>=!~]=?[A-Za-z0-9._*+-]+)*$")


def _console_cfg_path() -> Path:
    return platform_info.config_home() / "console.json"


def _read_console_cfg() -> dict:
    try:
        data = json.loads(_console_cfg_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def dev_mode() -> bool:
    return bool(_read_console_cfg().get("developer_mode", False))


def set_dev_mode(on: bool) -> bool:
    with _DEV_LOCK:
        cfg = _read_console_cfg()
        cfg["developer_mode"] = bool(on)
        path = _console_cfg_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"写不进 {path}：{exc}") from exc
    return bool(on)


def valid_package(name: str) -> bool:
    """包名白名单：字母数字 . _ + -，可带 [extra]，可带版本比较（==、>= 等）。

    参数是当成 argv 单独一项传给 pip 的，不存在命令注入；这里挡的是手滑和怪字符。
    """
    name = str(name or "").strip()
    return bool(name) and len(name) <= 120 and bool(_PKG_RE.match(name))


def default_module(package: str) -> str:
    """按 NoneBot 惯例从包名推模块名：nonebot-plugin-foo -> nonebot_plugin_foo。"""
    base = re.split(r"[\[<>=!~;]", str(package).strip())[0].strip()
    return base.replace("-", "_")


def _plugin_dirs(settings) -> list:
    root = Path(settings.plugins_dir)
    if not root.exists():
        return []
    return [p for p in sorted(root.iterdir()) if p.is_dir() and p.name != "__pycache__"]


# ------------------------------------------------------------------ 扫描
def _scan_deps() -> None:
    from qbotmanager.core import plugins as plug_mod

    settings = _settings()
    dirs = _plugin_dirs(settings)
    deps, seen = [], set()
    for d in dirs:
        try:
            for dep in plug_mod.detect_dependencies_from_dir(d):
                key = str(dep).strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    deps.append(str(dep).strip())
        except Exception as exc:  # noqa: BLE001
            _log(f"跳过 {d.name}：{exc}")
    _set(checked=len(deps))
    _log(f"共扫描 {len(dirs)} 个插件，声明依赖 {len(deps)} 项")

    py = _bot_python(settings)
    if py is None:
        _set(missing=[], error="机器人运行时还没部署（缺 .venv），先启动一次机器人")
        _log("机器人运行时未部署，无法检查依赖")
        return

    missing = []
    for i, dep in enumerate(deps, 1):
        name = _dep_name(dep)
        if not name:
            continue
        ok = _pip_has(py, name)
        if not ok:
            missing.append(dep)
        _log(f"[{i}/{len(deps)}] {'有' if ok else '缺'} {name}")
    _set(missing=missing)
    _log(f"依赖检查完成：缺 {len(missing)} 项")


def _dep_name(dep: str) -> str:
    import re

    return re.split(r"[<>=!~;\[(]", str(dep).strip())[0].strip()


def _read_tail_bytes(path: Path, limit: int) -> bytes:
    """读文件尾部若干字节（日志可能很大，别整个读进内存）。"""
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > limit:
            fh.seek(size - limit)
        return fh.read()


def _pip_has(py: Path, name: str) -> bool:
    try:
        proc = subprocess.run(
            [str(py), "-m", "pip", "show", name],
            capture_output=True, timeout=60, check=False,
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ 安装
def _install_deps() -> None:
    with _state_lock:
        missing = list(_state.get("missing") or [])
    if not missing:
        _set(error="没有待安装的缺失依赖，先扫描一次")
        return
    settings = _settings()
    py = _bot_python(settings)
    if py is None:
        _set(error="机器人运行时还没部署（缺 .venv）")
        return
    _log(f"开始安装 {len(missing)} 个依赖（清华镜像，失败自动切官方源）")
    installed = []
    for i, dep in enumerate(missing, 1):
        name = _dep_name(dep) or dep
        _log(f"[{i}/{len(missing)}] 安装 {dep}")
        ok = _pip_install(py, dep)
        if not ok:
            _log(f"{name} 清华镜像失败，改用官方源重试")
            ok = _pip_install(py, dep, official=True)
        if ok:
            installed.append(name)
        else:
            _log(f"{name} 安装失败")
    _set(installed=installed)
    _log(f"安装结束：成功 {len(installed)} / {len(missing)} 个，建议重新扫描验证")


def _pip_install(py: Path, dep: str, official: bool = False) -> bool:
    env_index = ("https://pypi.org/simple" if official
                 else "https://pypi.tuna.tsinghua.edu.cn/simple")
    try:
        proc = subprocess.run(
            [str(py), "-m", "pip", "install", "-q", dep],
            capture_output=True, text=True, timeout=_INSTALL_TIMEOUT, check=False,
            env={**_base_env(), "PIP_INDEX_URL": env_index},
        )
    except Exception as exc:  # noqa: BLE001
        _log(f"pip 执行异常：{exc}")
        return False
    for line in (proc.stdout or "").splitlines()[-4:]:
        if line.strip():
            _log(line.strip())
    if proc.returncode != 0:
        for line in (proc.stderr or "").splitlines()[-3:]:
            if line.strip():
                _log(line.strip())
    return proc.returncode == 0


def _base_env() -> dict:
    import os

    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


# ------------------------------------------------------------------ 从 PyPI 装插件
def _install_pypi_plugin(package: str, module: str = "") -> None:
    """开发者模式：用机器人 venv 的 pip 装包，并把模块名写进 bot 的 pyproject。

    为什么**不**复用桌面端的 `install_store_plugin`：它会走 `python_runtime.ensure_runtime()`，
    而服务器上那份 ensure_runtime 在这台 Linux 上会去下载**Windows 版 embeddable Python**
    （实测往 data_home/python 里灌了 22MB 的 .pyd，最后以 Permission denied 失败）。
    无头端本来就有现成的机器人 venv（`bot_dir/.venv`，就是机器人自己跑的那个），
    直接用它的 pip 最稳，也和依赖安装走同一条路。
    """
    settings = _settings()
    py = _bot_python(settings)
    if py is None:
        _set(error="机器人运行时还没部署（缺 .venv）：先启动一次机器人，再回来装插件")
        _log("机器人运行时未部署，放弃安装")
        return

    mod = (module or "").strip() or default_module(package)
    _log(f"用机器人 venv 的 pip 安装 {package}（模块名记为 {mod}）")
    ok = _pip_install(py, package)
    if not ok:
        _log("默认镜像源失败，改用官方源重试")
        ok = _pip_install(py, package, official=True)
    if not ok:
        _set(error="安装失败：pip 返回非 0，看上面的输出")
        return

    try:
        from qbotmanager.core.bot import read_plugins, write_plugins

        cur = list(read_plugins(settings))
        if mod not in cur:
            cur.append(mod)
            write_plugins(settings, cur)
            _log(f"{mod} 已写进机器人 pyproject 的 [tool.nonebot].plugins")
        else:
            _log(f"{mod} 本来就在 pyproject 里")
    except Exception as exc:  # noqa: BLE001
        _set(error=f"包装好了，但写 pyproject 失败：{exc}")
        _log(f"写 pyproject 失败：{exc}")
        return

    _set(installed=[package])
    _log("装好了：重启机器人后生效")


def _start(kind: str, target, **extra) -> dict:
    with _state_lock:
        if _state["running"]:
            raise HTTPException(409, "已有扫描/安装任务在跑，请等它结束")
        _state.update({
            "running": True, "kind": kind, "stage": "启动中", "started_at": _now(),
            "finished_at": 0.0, "error": "", "log": [],
            **({"installed": [], "package": extra.get("package", "")} if kind == "plugin"
               else {"installed": []} if kind == "install"
               else {"checked": 0, "missing": []}),
        })

    def runner():
        try:
            target()
        except Exception as exc:  # noqa: BLE001
            _set(error=str(exc))
            _log(f"任务失败：{exc}")
        finally:
            _set(running=False, finished_at=_now(), stage="已结束")

    threading.Thread(target=runner, daemon=True, name=f"deps-{kind}").start()
    return {"ok": True, "started": True, "kind": kind}


# ------------------------------------------------------------------ 微信 iLink
def _ilink_dir() -> Path:
    """适配器把状态/二维码写在这里（它自己用的是 Path.cwd()/data/nonebot_adapter_ilink）。"""
    return Path(_settings().bot_dir) / "data" / "nonebot_adapter_ilink"


def _qq_events_module():
    """拿 core/qq_events（拿不到就返回 None，调用方自己降级）。"""
    try:
        from qbotmanager.core import qq_events
    except Exception:  # noqa: BLE001
        return None
    return qq_events


def _humanize_lines(lines) -> list:
    """把日志里的英文事件名翻成人话（和桌面端日志页同一套 core/qq_events）。

    控制台前端不用改：后端翻好，前端照样一行一行显示。
    """
    qq_events = _qq_events_module()
    if qq_events is None:
        return list(lines)
    return [qq_events.humanize_line(line) for line in lines]


def _read_json(path: Path) -> dict:
    """读一个 JSON 文件（带缓存）。

    无头端是按 HTTP 请求触发的，本来没有桌面端那种 2 秒心跳；但控制台一次会拉好几个
    状态（服务/依赖/权限/账号），每次直读盘在慢盘上一样拖响应。
    统一走共用的 core/json_cache（按 修改时间+大小 命中），文件被改过照样立刻重读；
    缓存模块拿不到就退回直读，功能不受影响。
    """
    try:
        from qbotmanager.core import json_cache
    except Exception:  # noqa: BLE001
        json_cache = None
    if json_cache is not None:
        return json_cache.load_json(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def wechat_state() -> dict:
    """微信通道现状：闸门有没有开、适配器在不在、登录没登录、二维码是哪一张。"""
    from . import auth as auth_mod

    settings = _settings()
    d = _ilink_dir()
    try:
        # 微信通道看 member（已开通的档位），不是 full（历史的全解锁字段）：
        # 两个判定分开，前端不要拿 full 当通道标志。
        _gate = auth_mod.feature_gate()
        full = bool(_gate.get("member", _gate.get("full")))
    except Exception:  # noqa: BLE001
        full = False
    state = _read_json(d / "ilink_state.json")
    status = _read_json(d / "ilink_login_status.json")
    qr = _read_json(d / "ilink_qrcode.json")
    qr_ts = float(qr.get("ts") or 0)
    return {
        "ok": True,
        "gate": full,
        "adapter": (Path(settings.plugins_dir) / "nonebot_adapter_ilink").exists(),
        "logged_in": bool(str(state.get("token") or "")),
        "bot_id": state.get("bot_id") or "",
        "user_id": state.get("user_id") or "",
        "login_status": status.get("login_status") or "",
        "login_extra": status.get("extra") or "",
        "qrcode_url": (qr.get("content") or "") if qr else "",
        "qrcode_ts": qr_ts,
        "qrcode_age": round(time.time() - qr_ts, 1) if qr_ts else None,
        "dir": str(d),
    }


# ------------------------------------------------------------------ 能力包详情/参数
def _tools_dir() -> Path:
    return Path(platform_info.data_home()) / "tool_packs"


def _flatten(d: dict) -> dict:
    """包里的 behavior/config 可能是 {"proactive": {...}} 这种单键包装，摊平一层。"""
    if isinstance(d, dict) and len(d) == 1:
        v = next(iter(d.values()))
        if isinstance(v, dict):
            return v
    return d if isinstance(d, dict) else {}


def _pack_fields(manifest: dict) -> list:
    """参数 schema：manifest.config.fields（没有就按 behavior 的键造一份）。"""
    fields = ((manifest.get("config") or {}).get("fields")) or []
    if fields:
        return fields
    out = []
    for _, block in (manifest.get("behavior") or {}).items():
        if isinstance(block, dict):
            for k, v in block.items():
                if isinstance(v, bool):
                    t = "bool"
                elif isinstance(v, (int, float)):
                    t = "number"
                else:
                    t = "text"
                out.append({"key": k, "label": k, "type": t, "default": v})
    return out


def _pack_features(manifest: dict) -> list:
    """详情页的「功能」列表：从 tools + behavior 派生。"""
    feats = []
    for tool in manifest.get("tools") or []:
        feats.append({"title": str(tool.get("name") or ""), "desc": str(tool.get("description") or "")})
    for name, block in (manifest.get("behavior") or {}).items():
        if isinstance(block, dict):
            feats.append({"title": name, "desc": "行为参数：" + "、".join(block.keys())})
    for name, block in (manifest.get("schedule") or {}).items():
        if isinstance(block, dict):
            feats.append({"title": f"计划任务 · {name}", "desc": "、".join(f"{k}={v}" for k, v in block.items())})
    return [f for f in feats if f["title"]]


def pack_detail(pid: str) -> dict:
    """某个能力包的详情（已安装就读它的 manifest + config；没装就只回基本信息）。"""
    pid = str(pid or "").strip()
    if not pid or "/" in pid or ".." in pid:
        raise HTTPException(400, "插件标识不合法")
    d = _tools_dir() / pid
    manifest = _read_json(d / "manifest.json")
    installed = bool(manifest)
    cfg = _flatten(_read_json(d / "config.json"))
    behavior = _flatten(_read_json(d / "behavior.json"))
    fields = _pack_fields(manifest)
    defaults = {}
    for f in fields:
        if "default" in f:
            defaults[str(f["key"])] = f["default"]
    for k, v in (behavior or {}).items():
        defaults.setdefault(k, v)
    values = {**defaults, **cfg}
    return {
        "ok": True,
        "id": pid,
        "installed": installed,
        "name": manifest.get("name") or pid,
        "version": manifest.get("version") or "",
        "kind": manifest.get("kind") or "",
        "tier": manifest.get("tier") or "",
        "description": manifest.get("description") or "",
        "adapters": manifest.get("adapters") or [],
        "permissions": manifest.get("permissions") or [],
        "features": _pack_features(manifest) if installed else [],
        "fields": fields,
        "defaults": defaults,
        "values": values,
        "has_user_config": bool(cfg),
        "dir": str(d),
    }


def _coerce(field: dict, raw):
    """按 schema 校验/转换一个值；不合法抛 400。"""
    key = str(field.get("key") or "")
    ftype = str(field.get("type") or "text")
    label = str(field.get("label") or key)
    if ftype == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return bool(raw)
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "on", "yes", "是")
        raise HTTPException(400, f"{label}：要是开关值")
    if ftype == "number":
        try:
            val = float(raw)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{label}：要是数字")
        lo, hi = field.get("min"), field.get("max")
        if lo is not None and val < float(lo):
            raise HTTPException(400, f"{label}：不能小于 {lo}")
        if hi is not None and val > float(hi):
            raise HTTPException(400, f"{label}：不能大于 {hi}")
        if val == int(val) and float(field.get("step") or 1) >= 1:
            return int(val)
        return val
    if ftype in ("enum", "select"):
        options = field.get("options") or []
        vals = [o.get("value") if isinstance(o, dict) else o for o in options]
        if vals and raw not in vals:
            raise HTTPException(400, f"{label}：只能是 {vals}")
        return raw
    return str(raw if raw is not None else "")


def save_pack_config(pid: str, patch: dict) -> dict:
    """把用户改的参数写进 <tool_packs>/<pid>/config.json（只认 schema 里声明过的键）。"""
    detail = pack_detail(pid)
    if not detail["installed"]:
        raise HTTPException(404, "这个能力包还没安装，装完才能改参数")
    if not isinstance(patch, dict) or not patch:
        raise HTTPException(400, "没有要改的参数")
    by_key = {str(f.get("key")): f for f in detail["fields"]}
    cur = _flatten(_read_json(_tools_dir() / pid / "config.json"))
    changed = {}
    for key, raw in patch.items():
        f = by_key.get(str(key))
        if f is None:
            continue                       # 没在 schema 里的键直接忽略
        val = _coerce(f, raw)
        if cur.get(key) != val:
            changed[str(key)] = val
        cur[str(key)] = val
    path = _tools_dir() / pid / "config.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"写参数失败：{exc}") from exc
    _log(f"能力包 {pid} 参数已保存：{changed or '无变化'}")
    return {"ok": True, "id": pid, "changed": changed, "values": cur, "path": str(path)}


# ---------------------------------------------------------------- 权益与兑换
def entitlement_state() -> dict:
    """本机当前的权益（账号服务下发、带签名；无头端只做镜像与展示）。"""
    from . import auth as auth_mod

    cfg = headless_config.load()
    try:
        gate = auth_mod.feature_gate()
    except Exception:  # noqa: BLE001
        gate = {"full": False, "member": False, "all_plugins": False}
    return {
        "ok": True,
        "email": cfg.get("account_email") or "",
        "plan": cfg.get("plan") or "none",
        "plan_expires_at": float(cfg.get("plan_expires_at") or 0),
        "owned_plugins": list(cfg.get("owned_plugins") or []),
        # full = 历史字段（plans.json 的 all_plugins，能力包已全部免费）
        # member = 已开通的档位（微信通道）。两者分开，前端不要拿 full 当通道标志。
        "full": bool(gate.get("full")),
        "member": bool(gate.get("member")),
        "all_plugins": bool(gate.get("all_plugins", gate.get("full"))),
        "logged_in": bool(cfg.get("account_token")),
    }


def _account_get(path: str, token: str) -> dict:
    """GET 账号服务（auth._post 只能 POST；/api/account/me 是 GET）。"""
    import json as _json
    import urllib.request

    from .auth import account_url

    req = urllib.request.Request(
        account_url(path),
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return _json.loads(resp.read().decode("utf-8"))


def _save_entitlement(data: dict) -> dict:
    """把账号服务返回的权益写进本机配置（login/redeem/me 三个来源字段名略有差异）。"""
    cfg = headless_config.load()
    if data.get("email"):
        cfg["account_email"] = str(data["email"])
    if data.get("plan") is not None:
        cfg["plan"] = str(data.get("plan") or "none")
    if data.get("plan_expires_at") is not None:
        cfg["plan_expires_at"] = float(data.get("plan_expires_at") or 0)
    if isinstance(data.get("owned_plugins"), list):
        cfg["owned_plugins"] = [str(x) for x in data["owned_plugins"]]
    for key in ("sig", "entitlement_sig"):
        if data.get(key):
            cfg["entitlement_sig"] = str(data[key])
            break
    for key in ("machine_id", "entitlement_machine"):
        if data.get(key):
            cfg["entitlement_machine"] = str(data[key])
            break
    headless_config.save(cfg)
    return cfg


# ---------------------------------------------------------------- 本地人设工坊
# 行为基准是桌面端 `src/qbotmanager/core/persona_workshop.py`：无头端**不重写**导入/校验/落盘，
# 只做「鉴权 + 参数白名单 + 调它 + 把结果转成 JSON」——两端因此用同一个目录、同一套人格卡格式。
# 人设目录 = persona_workshop.personas_dir(settings) = <安装根>/personas；
# 无头端的安装根就是 platform_info.data_home()，与桌面端 settings.root 同义（deploy.build_settings 建的就是它）。
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\.]{1,64}$")
_UPLOAD_SUFFIXES = (".json", ".zip", ".md", ".markdown", ".txt")
_MAX_UPLOAD = 2 * 1024 * 1024        # 人设包（含工具代码）2MB 足够，再大基本是误传


def valid_persona_id(value) -> bool:
    """人设 id 白名单：字母/数字/._-，1~64 位，且不含 `..`。

    为什么必须自己在进 persona_workshop 之前卡死：它拿 manifest 里的 `id` 直接当目录名
    （`root / pid`，见 install_persona / uninstall_persona），本身**不校验路径穿越**，
    所以 `../../xxx` 这种 id 能把 rmtree/copytree 打到人设目录外面去。无头端是网络入口，先挡。
    """
    pid = str(value or "").strip()
    return bool(_NAME_RE.match(pid)) and pid not in (".", "..") and ".." not in pid


def _clean_id(value) -> str:
    pid = str(value or "").strip()
    if not valid_persona_id(pid):
        raise HTTPException(400, "人设 id 不合法：只允许 字母/数字/._- 且 1~64 位")
    return pid


def _persona_settings():
    """桌面端 Settings 的等价物（root = 无头端数据根目录），并补上启用中的人设 id。

    为什么要把 local_persona_id 从无头配置补进来：deploy.build_settings() 建的是全新 Settings，
    不读 <root>/settings.json，否则每次请求都以为「没启用任何人设」——列表里的「启用中」标记、
    以及机器人进程的 ASTROSWARM_PERSONAS_ALLOWED（人设自带工具能不能加载）都会是空的。
    """
    s = _settings()
    s.local_persona_id = str(headless_config.load().get("local_persona_id") or "")
    return s


def _save_persona_state(pid: str, agent_profile_enabled=None) -> None:
    """把「当前启用的人设」写进无头配置（重启/下次请求都还在）。"""
    cfg = headless_config.load()
    cfg["local_persona_id"] = str(pid or "")
    if agent_profile_enabled is not None:
        cfg["agent_profile_enabled"] = bool(agent_profile_enabled)
    headless_config.save(cfg)


def _personas_root() -> Path:
    from qbotmanager.core import persona_workshop

    return persona_workshop.personas_dir(_persona_settings())


def _persona_module():
    """延迟导入桌面端人设模块：它只依赖 core（ai_config / agent.tool），不带 PySide6。

    仍然包一层：万一以后它被牵进 UI 依赖，无头端这里要给一句人话，而不是让整个 app 起不来。
    """
    try:
        from qbotmanager.core import persona_workshop
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            500, f"服务端缺少人设工坊模块（qbotmanager.core.persona_workshop）：{exc}"
        ) from exc
    return persona_workshop


def _persona_target(pid: str) -> Path:
    """人设目录下这一项的绝对路径；`..` / 绝对路径 / 符号链接一律 400。

    `pid` 已经过 _clean_id，这里再做一次 resolve 复核：`root/<pid>` 若是符号链接，
    resolve() 会落到人设目录之外，比对父目录即可拦下（软链接指向目录时 rmtree 也会误删别处）。
    """
    root = _personas_root()
    raw = root / pid
    if raw.is_symlink():
        raise HTTPException(400, "人设目录是符号链接，已拒绝操作")
    target = raw.resolve()
    if target == root.resolve() or target.parent != root.resolve():
        raise HTTPException(400, "人设路径越界，已拒绝操作")
    return target


def persona_list() -> dict:
    """本地人设列表（id / 名称 / 来源 / 更新时间 / 是否启用中）。"""
    pw = _persona_module()
    s = _persona_settings()
    root = pw.personas_dir(s)
    items = []
    for p in pw.list_personas(s):
        pid = str(p.get("id") or "")
        stamp = 0.0
        try:
            stamp = float((root / pid).stat().st_mtime)
        except OSError:
            pass
        items.append({
            **p,
            # 桌面端列表只有「工具：xxx」；控制台要显示来源与更新时间，这里补上（目录 mtime
            # 就是最近一次导入/覆盖该人设的时间）
            "source": "本地导入（含工具）" if p.get("tools") else "本地导入",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp)) if stamp else "",
            "updated_ts": stamp,
        })
    return {
        "ok": True,
        "items": items,
        "active": str(getattr(s, "local_persona_id", "") or ""),
        "dir": str(root),
    }


def _decode_upload(payload: dict):
    """把请求体里的内容落成字节 + 后缀；不接受用户文件名当路径。"""
    name = str(payload.get("filename") or "").strip()
    low = name.lower()
    suffix = next((ext for ext in _UPLOAD_SUFFIXES if low.endswith(ext)), "")
    b64 = payload.get("content_b64")
    text = payload.get("content")
    if b64:
        try:
            data = base64.b64decode(str(b64), validate=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"上传内容不是合法 base64：{exc}") from exc
    elif text is not None:
        data = str(text).encode("utf-8")
        if suffix == ".zip":        # 文本内容却自称 zip：按 JSON 处理
            suffix = ".json"
    else:
        raise HTTPException(400, "没有收到人设内容（content 或 content_b64 至少给一个）")
    if not data.strip():
        raise HTTPException(400, "人设内容是空的")
    if len(data) > _MAX_UPLOAD:
        raise HTTPException(400, f"人设包太大（上限 {_MAX_UPLOAD // 1024 // 1024}MB）")
    if not suffix:
        suffix = ".zip" if data[:2] == b"PK" else ".json"
    return data, suffix


def _parse_card(text: str) -> dict:
    try:
        card = json.loads(text)
    except ValueError as exc:
        raise HTTPException(400, f"人设 JSON 解析失败：{exc}") from exc
    if not isinstance(card, dict):
        raise HTTPException(400, "人格卡必须是 JSON 对象")
    _clean_id(card.get("id"))      # 见 valid_persona_id 的说明：id 会被当目录名用
    return card


def _check_zip_persona_id(path: Path) -> str:
    """zip 人设包：挡掉穿越路径，并校验 manifest.json 里的 id。

    persona_workshop 解压时已经挡了 `..` 和绝对路径，但它不校验 manifest 的 id，
    而 id 会被 `copytree` 到 `root/<id>` 且先 `rmtree` 同名目标 —— 恶意 id 能删/写到人设目录外。
    """
    import zipfile

    try:
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            for n in names:
                parts = n.replace("\\", "/").split("/")
                if n.startswith(("/", "\\")) or ".." in parts:
                    raise HTTPException(400, f"压缩包包含非法路径：{n}")
            cands = [n for n in names if n.replace("\\", "/").endswith("manifest.json")]
            if not cands:
                raise HTTPException(400, "压缩包里缺少 manifest.json")
            cands.sort(key=lambda n: n.count("/"))     # 顶层目录里的优先
            raw = zf.read(cands[0])
    except zipfile.BadZipFile as exc:
        raise HTTPException(400, f"不是有效的 zip 压缩包：{exc}") from exc
    try:
        card = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, f"manifest.json 解析失败：{exc}") from exc
    if not isinstance(card, dict):
        raise HTTPException(400, "manifest.json 必须是 JSON 对象")
    return _clean_id(card.get("id"))


def _wrap_text_card(text: str, payload: dict, filename: str) -> dict:
    """Markdown / 纯文本人设：整段当 system_prompt 包成一张人格卡。

    桌面端工具只认 JSON 与 zip，而模板说明里写的「两种写法」之一就是直接给一整段人设文字；
    控制台是粘贴框，所以这里适配一层，落盘后仍是标准人格卡（校验/编译都走同一套）。
    """
    body = str(text or "").strip()
    if not body:
        raise HTTPException(400, "人设文本是空的")
    want = str(payload.get("id") or payload.get("name") or "").strip() or Path(filename).stem
    slug = re.sub(r"[^A-Za-z0-9_\-\.]", "-", want).strip("-.")
    if not valid_persona_id(slug):
        slug = "persona-" + time.strftime("%Y%m%d-%H%M%S")   # 中文名等没法当目录名，退回时间戳 id
    return {
        "format": "astroswarm-persona",
        "version": "1.0",
        "id": slug,
        "name": str(payload.get("name") or "").strip() or want or slug,
        "kind": "persona-pack",
        "summary": "无头端控制台导入的文本人设",
        "system_prompt": body,
        "tools": [],
    }


def import_persona(payload: dict) -> dict:
    """导入人设：JSON 人格卡 / zip 人设包 / 一段人设文本（Markdown）。

    落盘到临时目录再交给桌面端的 install_persona（它收的是**本地文件路径**），
    校验交给它，临时文件在 finally 里清掉，不留垃圾在磁盘上。
    """
    pw = _persona_module()
    data, suffix = _decode_upload(payload or {})
    name = str((payload or {}).get("filename") or "").strip()

    tmpdir = Path(tempfile.mkdtemp(prefix="astroswarm_persona_"))
    try:
        if suffix == ".zip":
            src = tmpdir / "upload.zip"
            src.write_bytes(data)
            _check_zip_persona_id(src)
        elif suffix == ".json":
            src = tmpdir / "upload.json"
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise HTTPException(400, "人设 JSON 必须是 UTF-8 文本") from exc
            _parse_card(text)
            src.write_text(text, encoding="utf-8")
        else:
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise HTTPException(400, "人设文本必须是 UTF-8 编码") from exc
            src = tmpdir / "upload.json"
            src.write_text(
                json.dumps(_wrap_text_card(text, payload or {}, name), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        try:
            res = pw.install_persona(_persona_settings(), src, log=_log)
        except RuntimeError as exc:       # 校验不过算参数错误，别报 500
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, f"写入人设目录失败：{exc}") from exc
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    _log(f"人设已导入：{res.get('name')}（{res.get('id')}）")
    return {"ok": True, **res}


def activate_persona(pid: str) -> dict:
    """启用本地人设：写机器人人格 + 标记启用 + 关掉硬编码智能体档案（与桌面端同序）。"""
    pw = _persona_module()
    from qbotmanager.core import ai_config

    pid = _clean_id(pid)
    s = _persona_settings()
    if not _persona_target(pid).exists():
        raise HTTPException(404, "人设不存在，先刷新一下列表")
    try:
        res = pw.activate_persona(s, pid)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"启用人设失败：{exc}") from exc
    # activate_persona 内部把 agent_profile_enabled 写进了 <root>/settings.json，
    # 但机器人启动读的是无头配置（deploy.build_settings），所以这里再同步一份，否则双人格
    _save_persona_state(pid, agent_profile_enabled=False)
    _log(f"已启用本地人设：{res.get('name')}（{pid}）")
    try:
        personality = ai_config.read_personality(s)
    except Exception:  # noqa: BLE001
        personality = ""
    return {"ok": True, "id": pid, "name": res.get("name"),
            "personality": personality, "needs_restart": True}


def deactivate_persona() -> dict:
    """停用本地人设：清启用标记并把人格恢复内置默认。"""
    pw = _persona_module()
    from qbotmanager.core import ai_config

    s = _persona_settings()
    try:
        pw.deactivate_persona(s)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc
    # 桌面端「停用」只清标记、人格文本留在输入框里；无头端没有那个输入框的即时回填，
    # 按功能约定恢复成内置默认人格，否则机器人还在用上一个人设的话术。
    default = str(getattr(ai_config, "_DEFAULT_PERSONALITY", "") or "")
    if default:
        ai_config.save_personality(s, default)
    _save_persona_state("")
    _log("已停用本地人设，人格恢复内置默认")
    try:
        personality = ai_config.read_personality(s)
    except Exception:  # noqa: BLE001
        personality = ""
    return {"ok": True, "personality": personality, "needs_restart": True}


def uninstall_persona(pid: str, confirm: bool) -> dict:
    """卸载人设：不可撤销，必须带 confirm=true（前端 window.confirm 之外的后端兜底）。"""
    pw = _persona_module()

    pid = _clean_id(pid)
    if not confirm:
        raise HTTPException(400, "卸载人设需要二次确认：请带 confirm=true")
    target = _persona_target(pid)          # 越界（.. / 绝对路径 / 符号链接）先拦
    if not target.exists():
        raise HTTPException(404, "人设不存在，可能已经被删掉了")
    if not target.is_dir():
        raise HTTPException(400, "人设路径不是目录，已拒绝删除")
    try:
        pw.uninstall_persona(_persona_settings(), pid)
    except OSError as exc:
        raise HTTPException(500, f"卸载失败：{exc}") from exc
    if str(headless_config.load().get("local_persona_id") or "") == pid:
        _save_persona_state("")
    _log(f"已卸载本地人设：{pid}")
    return {"ok": True, "id": pid, "needs_restart": True}


# ------------------------------------------------------------------ 注册
def register(app: FastAPI) -> None:
    """把这些路由挂到 api.py 的 app 上（必须在 StaticFiles 之前调用）。"""

    # 飞书凭证 / 日志保留天数存在无头配置里，而 deploy.build_settings() 不认识这几个键：
    # 补一层（幂等），否则控制台存得进去、机器人启动时读不到（详见函数注释）。
    install_settings_bridge()

    @app.get("/api/plugins/entitlement")
    def plugins_entitlement(authorization: str = Header("", alias="Authorization")):
        # 这里回的是邮箱 / 套餐 / 已购插件，正好是 /api/auth/status 刚被加固要藏的那份数据，
        # 未登录不能再给（控制台本来就是带 token 调它）。
        _require_auth(authorization)
        return entitlement_state()

    @app.get("/api/plugins/market")
    def plugins_market(authorization: str = Header("", alias="Authorization")):
        """官网插件市场：和桌面端「插件」页同一份数据（core.plugin_market）。

        老板 2026-10-04 要求控制台与桌面端 100% 对齐：桌面端能在应用里逛市场、一键装，
        控制台以前只能传 zip。返回字段与桌面端 _refresh_market_list 用的一致。
        市场拉不到/签名不过不算崩溃：回 ok:false + 中文原因，前端显示"暂不可用"。
        """
        _require_auth(authorization)
        from qbotmanager.core import plugin_market

        try:
            raw, sig = plugin_market.fetch_market()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "market_unreachable", "entries": [],
                    "why": f"插件市场连不上：{str(exc)[:120]}"}
        if not raw:
            return {"ok": False, "error": "market_unreachable", "entries": [],
                    "why": "插件市场没有返回内容（检查服务器网络）"}
        try:
            if not plugin_market.verify_manifest(raw, sig or ""):
                return {"ok": False, "error": "bad_signature", "entries": [],
                        "why": "市场签名校验没过，为安全起见不显示列表"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "verify_failed", "entries": [],
                    "why": f"签名校验出错：{str(exc)[:120]}"}
        try:
            text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
            entries = plugin_market.parse_market(text)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "bad_market", "entries": [],
                    "why": f"市场清单格式不对：{str(exc)[:120]}"}
        try:
            ent = entitlement_state()
        except Exception:  # noqa: BLE001
            ent = {}
        out = []
        for item in entries or []:
            if not isinstance(item, dict):
                continue
            try:
                access = plugin_market.plugin_access(item, ent)
            except Exception:  # noqa: BLE001
                access = {}
            out.append({
                # id 必须带上：控制台「官网市场」的「安装」按钮要靠它调
                # POST /api/plugins/install（那个接口也认 name 作兜底，见 api.py）。
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or item.get("id") or ""),
                "desc": str(item.get("desc") or item.get("description") or ""),
                "version": str(item.get("version") or ""),
                "category": str(item.get("category") or "其它"),
                "access": access,
            })
        return {"ok": True, "entries": out, "count": len(out)}

    @app.post("/api/plugins/redeem")
    def plugins_redeem(body: dict, authorization: str = Header("", alias="Authorization")):
        """兑换码：转发给账号服务（它才知道码是真的），成功就把新权益存本机。"""
        _require_auth(authorization)
        code = str((body or {}).get("code") or "").strip()
        if not code:
            raise HTTPException(400, "兑换码不能为空")
        from . import auth as auth_mod

        cfg = headless_config.load()
        token = str(cfg.get("account_token") or "")
        if not token:
            raise HTTPException(401, "本机还没登录星群账号：先去「设置 → 星群账号」登录")
        try:
            data = auth_mod._post("/api/account/redeem", {"code": code}, token=token)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            raise HTTPException(400, f"兑换失败：{msg}") from exc
        _save_entitlement(data)
        # 兑换接口不回签名，补拉一次 /me 拿最新权益（含签名），否则本地验签还是旧的
        try:
            me = _account_get("/api/account/me", token)
            _save_entitlement(me)
        except Exception as exc:  # noqa: BLE001
            _log(f"兑换后补拉权益失败（不影响已购记录）：{exc}")
        _log(f"兑换码已使用：{code[:4]}****")
        return {"ok": True, **entitlement_state(), "detail": data}

    @app.post("/api/plugins/refresh-entitlement")
    def plugins_refresh_entitlement(authorization: str = Header("", alias="Authorization")):
        """去账号服务重新拉一次权益（买了插件之后点这个）。"""
        _require_auth(authorization)
        from . import auth as auth_mod

        cfg = headless_config.load()
        token = str(cfg.get("account_token") or "")
        if not token:
            raise HTTPException(401, "本机还没登录星群账号：先去「设置 → 星群账号」登录")
        try:
            data = _account_get("/api/account/me", token)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"拉取权益失败：{exc}") from exc
        _save_entitlement(data)
        return {"ok": True, **entitlement_state()}

    @app.get("/api/tools/{pid}/detail")
    def tools_detail(pid: str, authorization: str = Header("", alias="Authorization")):
        # 详情里带包目录绝对路径 + 该包 config.json 的当前参数值，未登录不给
        _require_auth(authorization)
        return pack_detail(pid)

    @app.put("/api/tools/{pid}/config")
    def tools_config(pid: str, body: dict, authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        return save_pack_config(pid, body or {})

    @app.get("/api/wechat/status")
    def wechat_status(authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        return wechat_state()

    @app.post("/api/wechat/verify-code")
    def wechat_verify_code(body: dict, authorization: str = Header("", alias="Authorization")):
        """写配对码文件，适配器读到就删（跟桌面端 UI 一个套路）。"""
        _require_auth(authorization)
        code = re.sub(r"\s+", "", str((body or {}).get("code") or ""))
        if not code:
            raise HTTPException(400, "配对码不能为空")
        d = _ilink_dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / "ilink_verify_code.txt").write_text(code, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"写配对码失败：{exc}") from exc
        _log(f"已收到配对码（{len(code)} 位），等微信适配器读取")
        return {"ok": True, "code_len": len(code)}

    @app.post("/api/wechat/relogin")
    def wechat_relogin(authorization: str = Header("", alias="Authorization")):
        """重新扫码：清掉 token 和旧二维码，重启机器人让它重新走登录流程。"""
        _require_auth(authorization)
        d = _ilink_dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
            st = _read_json(d / "ilink_state.json")
            st["token"] = ""
            st["get_updates_buf"] = ""
            (d / "ilink_state.json").write_text(
                json.dumps(st, ensure_ascii=False), encoding="utf-8")
            for name in ("ilink_qrcode.json", "ilink_login_status.json"):
                (d / name).unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"清理微信登录状态失败：{exc}") from exc
        from . import services

        try:
            services.restart("bot")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"重启机器人失败：{exc}") from exc
        _log("微信：已清登录态并重启机器人，稍后会出新的二维码")
        return {"ok": True, "restarted": True}

    @app.get("/api/wechat/qrcode.png")
    def wechat_qrcode(token: str = "", authorization: str = Header("", alias="Authorization")):
        """把二维码链接渲染成图片给控制台显示（需要 segno；没装就返回 503，前端退回显示链接）。

        鉴权：二维码就是微信登录凭证，谁扫到谁就能接管这个微信通道，所以必须登录才能取。
        <img> 不能自定义 header，控制台会用 ?token=<登录 token>（见 src/api.js 的 authUrl）。
        """
        _require_auth(token or authorization)
        url = str(wechat_state().get("qrcode_url") or "")
        if not url:
            raise HTTPException(404, "还没有二维码，点一下「重新扫码登录」")
        try:
            import io as _io

            import segno
        except ImportError as exc:
            raise HTTPException(503, "服务器没装 segno（pip install segno），可先用二维码链接") from exc
        try:
            buf = _io.BytesIO()
            segno.make(url, error="m").save(buf, kind="png", scale=6, border=2)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"生成二维码失败：{exc}") from exc
        return Response(content=buf.getvalue(), media_type="image/png",
                        headers={"Cache-Control": "no-store"})


    @app.get("/api/plugins/installed")
    def plugins_installed(authorization: str = Header("", alias="Authorization")):
        # 回的是插件目录绝对路径 + 本机装了哪些东西（踩点信息），要登录
        _require_auth(authorization)
        from qbotmanager.core import plugins as plug_mod

        settings = _settings()
        try:
            installed = [
                {"id": p.get("id"), "name": p.get("name"), "version": p.get("version")}
                for p in hl_plugins.installed()
            ]
        except Exception as exc:  # noqa: BLE001
            installed = []
            _log(f"读取已装插件失败：{exc}")
        try:
            local = plug_mod.list_local_plugins(settings)
        except Exception:  # noqa: BLE001
            local = []
        try:
            store = plug_mod.list_store_plugins(settings)
        except Exception:  # noqa: BLE001
            store = []
        return {
            "ok": True,
            "installed": installed,
            "local": local,
            "store": store,
            "dir": str(settings.plugins_dir),
        }

    @app.get("/api/console/dev-mode")
    def console_dev_mode(authorization: str = Header("", alias="Authorization")):
        # 开发者模式是否开着属于本机状态（开了就能装第三方 zip / pip 包），未登录不给
        _require_auth(authorization)
        return {"ok": True, "developer_mode": dev_mode()}

    @app.post("/api/console/dev-mode")
    def console_set_dev_mode(body: dict, authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        return {"ok": True, "developer_mode": set_dev_mode(bool((body or {}).get("on")))}

    @app.post("/api/plugins/install-pypi")
    def plugins_install_pypi(body: dict, authorization: str = Header("", alias="Authorization")):
        """开发者模式：从 PyPI 包名安装 NoneBot 插件（前端那行输入框对应这里）。"""
        _require_auth(authorization)
        if not dev_mode():
            raise HTTPException(403, "开发者模式没开：先去「设置 → 启动行为」把它打开（后果自负）")
        pkg = str((body or {}).get("package") or "").strip()
        if not valid_package(pkg):
            raise HTTPException(400, "包名不合法：只允许 字母数字 . _ + - 和 [] 里的 extra，可带 ==/>= 版本")
        module = str((body or {}).get("module") or "").strip()
        return _start("plugin", lambda: _install_pypi_plugin(pkg, module), package=pkg)

    @app.get("/api/deps/status")
    def deps_status(authorization: str = Header("", alias="Authorization")):
        # 状态里带 pip 输出 / 错误栈 / 文件绝对路径，未登录不给（控制台轮询本来就带 token）
        _require_auth(authorization)
        with _state_lock:
            return {"ok": True, **{k: v for k, v in _state.items()}}

    @app.post("/api/deps/scan")
    def deps_scan(authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        return _start("scan", _scan_deps)

    @app.post("/api/deps/install")
    def deps_install(authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        return _start("install", _install_deps)

    @app.get("/api/logs/download")
    def logs_download(name: str = logs.LOG_FILE_DEFAULT,
                      authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)   # 日志里有 AI key / 微信 token / 用户对话
        # 日志文件由名字白名单决定（logs.LOG_FILES），调用方给不了路径
        try:
            src, path = logs.resolve_log(name)
        except ValueError:
            raise HTTPException(400, f"未知的日志名，只能是：{logs.LOG_NAME_LIST}") from None
        if not path.exists():
            raise HTTPException(404, "暂无日志文件")
        # 不能用 FileResponse：日志正被服务持续追加，starlette 先按 stat 算 Content-Length
        # 再读文件，中间长出来的内容会撞上 "Too much data for declared Content-Length"
        # （实测必现，响应 200 但传输中断）。这里一次性读进内存再返回，长度天然一致。
        data = _read_tail_bytes(path, 4 * 1024 * 1024)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return Response(
            content=data,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{src}-{stamp}.log"'},
        )

    @app.get("/api/logs/tail")
    def logs_tail(n: int = 200, name: str = logs.LOG_FILE_DEFAULT,
                  authorization: str = Header("", alias="Authorization")):
        """给控制台冷启动用的一次性拉取（SSE 之外的兜底）。"""
        _require_auth(authorization)
        try:
            src, path = logs.resolve_log(name)
        except ValueError:
            raise HTTPException(400, f"未知的日志名，只能是：{logs.LOG_NAME_LIST}") from None
        if not path.exists():
            return {"ok": True, "name": src, "lines": []}
        text = logs.tail(path, max(1, min(int(n), 2000)))
        logs.prune_old()   # 顺手按保留天数清旧日志（默认 10 天；失败不影响返回）
        return {"ok": True, "name": src, "lines": _humanize_lines(text.splitlines())}

    @app.get("/api/logs/official")
    def logs_official(n: int = 500, name: str = logs.LOG_FILE_DEFAULT,
                      authorization: str = Header("", alias="Authorization")):
        """只挑官方通道那些行（与桌面端日志页的「官方通道」筛选项同一套判断）。

        排查"群里 @ 它不回"时最有用：一眼看到机器人到底收到了什么事件、模式是全量还是仅 @。
        """
        _require_auth(authorization)
        try:
            src, path = logs.resolve_log(name)
        except ValueError:
            raise HTTPException(400, f"未知的日志名，只能是：{logs.LOG_NAME_LIST}") from None
        if not path.exists():
            return {"ok": True, "name": src, "lines": [], "total": 0}
        lines = logs.tail(path, max(1, min(int(n), 5000))).splitlines()
        qq_events = _qq_events_module()
        if qq_events is None:
            picked = []
        else:
            picked = [line for line in lines if qq_events.is_official_line(line)]
        return {"ok": True, "name": src, "total": len(picked),
                "lines": _humanize_lines(picked)}

    # ---------------------------------------------------------- 人设工坊（本地）
    @app.get("/api/persona/template")
    def persona_template(
        fmt: str = "json",
        token: str = "",
        authorization: str = Header("", alias="Authorization"),
    ):
        """下载人格卡模板（桌面端「下载模板」给的就是这份 JSON）。

        `?fmt=md` 给 Markdown 说明（和「模板说明」同一份内容，方便另存）。
        鉴权：统一要求登录；`<a download>` 带不了 header，所以同时接受 `?token=`（同二维码接口）。
        """
        _require_auth(token or authorization)
        pw = _persona_module()
        want_md = str(fmt or "").strip().lower() in ("md", "markdown", "guide")
        text = pw.guide_text() if want_md else pw.template_text()
        filename = "persona-card-guide.md" if want_md else "persona-card-template.json"
        media = "text/markdown; charset=utf-8" if want_md else "application/json; charset=utf-8"
        return Response(
            content=text.encode("utf-8"),
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/persona/guide")
    def persona_guide(authorization: str = Header("", alias="Authorization")):
        """模板说明全文：控制台拿它在界面上就地展开/弹层显示。"""
        _require_auth(authorization)
        text = _persona_module().guide_text()
        return {"ok": True, "text": text, "chars": len(text)}

    @app.get("/api/persona/list")
    def persona_list_route(authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        return persona_list()

    @app.post("/api/persona/import")
    def persona_import_route(body: dict, authorization: str = Header("", alias="Authorization")):
        """导入人设：body 给 content（文本，JSON/Markdown）或 content_b64（zip）。

        为什么不用 multipart UploadFile：无头端依赖里没有 python-multipart，
        FastAPI 一旦看到 File/Form 参数就会在**注册路由时**直接抛错，整个 app 起不来。
        前端本来就能读文件内容（文本或 base64），走 JSON 更稳。
        """
        _require_auth(authorization)
        return import_persona(body or {})

    @app.post("/api/persona/activate")
    def persona_activate_route(body: dict, authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        return activate_persona(str((body or {}).get("id") or ""))

    @app.post("/api/persona/deactivate")
    def persona_deactivate_route(authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        return deactivate_persona()

    @app.post("/api/persona/uninstall")
    def persona_uninstall_route(body: dict, authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        data = body or {}
        return uninstall_persona(str(data.get("id") or ""), bool(data.get("confirm")))

    # ---- 记忆时间线（与桌面端 BrainPage 的「记忆时间线（本地）」对应）----
    @app.get("/api/memory/list")
    def memory_list_route(authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        return memory_list()

    @app.get("/api/memory/export")
    def memory_export_route(
        fmt: str = "json",
        token: str = "",
        authorization: str = Header("", alias="Authorization"),
    ):
        # <a download> 带不了 header，所以同时收 ?token=（与 persona/template 同一套做法）
        _require_auth(token or authorization)
        name, media, body = memory_export(fmt)
        return Response(
            content=body,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.post("/api/memory/delete")
    def memory_delete_route(body: dict, authorization: str = Header("", alias="Authorization")):
        """删一条记忆。

        参数必须显式：index 缺省 -1、scope 不做白名单校验时，`{}` 或
        `{"scope":"global"}` 这类请求会静默删掉最后一条。删除不可逆，缺字段一律 400。
        """
        _require_auth(authorization)
        data = body or {}
        scope = str(data.get("scope") or "").strip()
        if scope not in ("global", "user"):
            raise HTTPException(400, "scope 只能是 global 或 user")
        if "index" not in data or data.get("index") is None:
            raise HTTPException(400, "必须显式提供 index（整数，0 基；负数从末尾数）")
        if isinstance(data.get("index"), bool):
            raise HTTPException(400, "index 必须是整数")
        try:
            index = int(data["index"])
        except (TypeError, ValueError):
            raise HTTPException(400, "index 必须是整数") from None
        user = str(data.get("user") or "")
        if scope == "user" and not user:
            raise HTTPException(400, "scope=user 时必须提供 user")
        return memory_delete(scope, user, index)

    @app.post("/api/memory/clear")
    def memory_clear_route(body: dict, authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        return memory_clear(bool((body or {}).get("confirm")))

    # ---- 内置记忆（程序侧三档那套）：现在记住了什么 / 忘掉一条 ----
    # 与上面 /api/memory/* 的区别：上面读的是 AI 插件自己的 aichat_memory.json，
    # 这里读的是内置记忆库（qbotmanager.core.memory），也就是「无/基础/智能」那一档的库。
    @app.get("/api/memory/builtin")
    def memory_builtin_list_route(qq: str = "",
                                  authorization: str = Header("", alias="Authorization")):
        """现在记住了什么：档位 + 条数 + 明细（按人分组，最多 200 条）。"""
        _require_auth(authorization)
        return builtin_memory_overview(str(qq or ""))

    @app.post("/api/memory/builtin/forget")
    def memory_builtin_forget_route(body: dict,
                                    authorization: str = Header("", alias="Authorization")):
        """「忘掉这件事」：**真删**。qq 必填；fact 留空 = 清掉这个人的全部记忆。"""
        _require_auth(authorization)
        data = body or {}
        target = str(data.get("qq") or "").strip()
        if not target:
            raise HTTPException(400, "必须给 qq（要忘掉谁的事）")
        return builtin_memory_forget(target, str(data.get("fact") or ""))

    # ---- 本地知识库（与桌面端 BrainPage 的「本地知识库」对应）----
    @app.get("/api/knowledge/list")
    def knowledge_list_route(authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        return knowledge_list()

    @app.post("/api/knowledge/add")
    def knowledge_add_route(body: dict, authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        data = body or {}
        return knowledge_add(str(data.get("title") or ""), str(data.get("text") or ""))

    @app.post("/api/knowledge/delete")
    def knowledge_delete_route(body: dict, authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        data = body or {}
        return knowledge_delete(str(data.get("id") or ""), bool(data.get("confirm")))

    # ---- 消息中心与首页统计 ----
    @app.get("/api/messages/list")
    def messages_list_route(limit: int = 50,
                            authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        return messages_list(limit)

    @app.get("/api/stats/summary")
    def stats_summary_route(authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        return stats_summary()

    # ---- MCP 配置 ----
    @app.get("/api/mcp/config")
    def mcp_config_route(authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        return mcp_config()

    @app.post("/api/mcp/save")
    def mcp_save_route(body: dict, authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        data = body or {}
        servers = data.get("servers")
        if servers is not None and not isinstance(servers, dict):
            raise HTTPException(400, "servers 必须是对象（名称 -> {type,url/command,...}）")
        return mcp_save(bool(data.get("enabled")), servers if servers is not None else {})

    # ---- 唤醒词生成（对应桌面端 BrainPage 的「按人格生成」按钮）----
    # 前端 AiBrain 一直在 POST 这个地址，但无头端从来没实现过 —— 点一次必弹
    # 「唤醒词生成失败：Not Found」。这里补上，语义与桌面端一致（按人格文本提称呼）。
    @app.post("/api/ai/wake-words")
    def ai_wake_words_route(body: dict, authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        data = body or {}
        return wake_words(str(data.get("personality") or ""),
                          str(data.get("persona_mode") or ""))

    # ---- 本地 zip 安装（仅开发者模式，且只接受上传内容）----
    @app.post("/api/tools/install-zip")
    def tools_install_zip_route(body: dict, authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        data = body or {}
        return install_tool_pack_zip(
            str(data.get("filename") or ""),
            str(data.get("content_b64") or ""),
            bool(data.get("confirm")),
        )

    # ===== 机器人侧 AI 设置（与桌面端「AI 大脑」页同源）=====
    # 这些值都存在**机器人侧** aichat_manager.json（不是控制台自己的 config.json），
    # 所以统一走 qbotmanager.core.ai_config 的读写函数，别写进 headless_config.SAFE_KEYS。

    @app.get("/api/ai/settings")
    def ai_settings(authorization: str = Header("", alias="Authorization")):
        """群聊回话总开关 / 引用解析 / 群名单 / 主动搭话 / 免@连续对话。"""
        _require_auth(authorization)
        ai_config = _ai_config_module()
        s = _settings()
        return {
            "group_chat_enabled": ai_config.read_group_chat_enabled(s),
            "rich_parse_enabled": ai_config.read_rich_parse_enabled(s),
            "allowed_groups": ai_config.allowed_groups(s),
            "known_groups": ai_config.known_groups(s),
            "interject": ai_config.read_interject(s),
            "session": ai_config.read_session(s),
            # 2026-10-02 新增：入群欢迎开关 + 插件主动发送白名单（与桌面端同一份配置）
            "group_welcome_enabled": ai_config.read_group_welcome(s),
            "allowed_send_targets": ai_config.read_send_targets(s),
            # 2026-10-04 新增：识图 / 聊天里自学 / 跨平台互通（与桌面端同一份配置）
            "image_recognition_enabled": ai_config.read_image_recognition(s),
            "self_forge_enabled": ai_config.read_self_forge(s),
            "cross_platform_enabled": ai_config.read_cross_platform(s),
            "identity_bindings": ai_config.read_identity_bindings(s),
            # 2026-10-11 新增：内置记忆三档（无/基础/智能）+ 那一句中文说明
            "memory_enabled": ai_config.read_memory_enabled(s),
            "memory_level": ai_config.read_memory_level(s),
            "memory_level_note": ai_config.MEMORY_LEVEL_NOTE,
            "memory_level_options": ai_config.memory_level_info()["options"],
            # 2026-10-11 新增：空 @ 行为 + 上文回溯上限（老板报的"空@答了一小时前的内容"）
            "empty_at_action": ai_config.read_empty_at(s),
            "empty_at_info": ai_config.empty_at_info(),
            "context_window_minutes": ai_config.read_context_window_minutes(s),
            "context_window_note": ai_config.CONTEXT_WINDOW_NOTE,
            "context_window_default": ai_config.CONTEXT_WINDOW_DEFAULT,
            "context_window_min": ai_config.CONTEXT_WINDOW_MIN,
            "context_window_max": ai_config.CONTEXT_WINDOW_MAX,
            # 2026-10-12 新增：单条正文上限（H-13 落地时只有后端、界面上一直没有输入框）
            "max_incoming_chars": ai_config.read_max_incoming_chars(s),
            "max_incoming_chars_info": ai_config.max_incoming_chars_info(),
            # 2026-10-12 新增：分条发送（像人一样分几条发；改完立即生效）
            "multi_message": ai_config.read_multi_message(s),
            "multi_message_info": ai_config.multi_message_info(),
            # 2026-10-12 新增（与桌面端 brain_page.py:673-695 同源）：
            # 自动通过好友申请 + Agent 模式。**能力不在就不显示开关** ——
            # 桌面端的判据是 ai_config.has_capability，这里回两个布尔给前端用同一个口径。
            "auto_approve_friend": ai_config.read_auto_approve_friend(s),
            "agent_mode_enabled": ai_config.read_agent_mode(s),
            "has_approve_friend": ai_config.has_capability(s, "approve_friend"),
            "has_agent_tools": ai_config.has_capability(s, "send_to"),
            # 「这次改动要不要重启」的唯一口径（前端标注按钮/提示都用它）
            "restart_policy": _restart_policy().summary(),
        }

    @app.put("/api/ai/settings")
    def ai_settings_save(body: dict, authorization: str = Header("", alias="Authorization")):
        """只写传进来的字段；一个都不传就当没改过（避免误清空）。"""
        _require_auth(authorization)
        ai_config = _ai_config_module()
        s = _settings()
        data = body or {}
        done = []
        if "group_chat_enabled" in data:
            ai_config.save_group_chat_enabled(s, bool(data["group_chat_enabled"]))
            done.append("group_chat_enabled")
        if "rich_parse_enabled" in data:
            ai_config.save_rich_parse_enabled(s, bool(data["rich_parse_enabled"]))
            done.append("rich_parse_enabled")
        if "allowed_groups" in data:
            groups = data["allowed_groups"]
            if not isinstance(groups, list):
                raise HTTPException(400, "allowed_groups 要是数组（空数组 = 不限群）")
            ai_config.save_allowed_groups(s, [str(g) for g in groups])
            done.append("allowed_groups")
        if "interject" in data:
            payload = data["interject"]
            if not isinstance(payload, dict):
                raise HTTPException(400, "interject 要是对象")
            current = ai_config.read_interject(s)
            current.update(payload)
            ai_config.save_interject(s, current)
            done.append("interject")
        if "session" in data:
            payload = data["session"]
            if not isinstance(payload, dict):
                raise HTTPException(400, "session 要是对象")
            current = ai_config.read_session(s)
            current.update(payload)
            ai_config.save_session(s, current)
            done.append("session")
        if "group_welcome_enabled" in data:
            ai_config.save_group_welcome(s, bool(data["group_welcome_enabled"]))
            done.append("group_welcome_enabled")
        if "allowed_send_targets" in data:
            targets = data["allowed_send_targets"]
            if not isinstance(targets, list):
                raise HTTPException(400, "allowed_send_targets 要是数组（空数组 = 一个都不许发）")
            ai_config.save_send_targets(s, targets)
            done.append("allowed_send_targets")
        # 2026-10-04：识图 / 自学 / 跨平台互通（和桌面端同一份读写）
        if "image_recognition_enabled" in data:
            ai_config.save_image_recognition(s, bool(data["image_recognition_enabled"]))
            done.append("image_recognition_enabled")
        if "self_forge_enabled" in data:
            ai_config.save_self_forge(s, bool(data["self_forge_enabled"]))
            done.append("self_forge_enabled")
        if "cross_platform_enabled" in data:
            ai_config.save_cross_platform(s, bool(data["cross_platform_enabled"]))
            done.append("cross_platform_enabled")
        # 2026-10-11：内置记忆三档（无/基础/智能）。写的是机器人侧同一份
        # aichat_manager.json；**改完立即生效，不用重启机器人** ——
        # 机器人进程每次调用都热读这份配置（core/memory/policy.py::level()），
        # 再加上这里刷新的热配置快照（bot.write_live_gates）。
        if "memory_level" in data:
            raw = str(data["memory_level"] or "").strip().lower()
            allowed = set(ai_config.MEMORY_LEVELS) | set(ai_config.MEMORY_LEVEL_LABELS.values())
            if raw not in allowed:
                raise HTTPException(400, "记忆档位只能是 off / basic / smart（无 / 基础 / 智能）")
            ai_config.save_memory_level(s, raw)
            done.append("memory_level")
            _refresh_bot_hot_config()
        # 2026-10-11：空 @ 行为 + 上文回溯上限 T（都是热读，改完立即生效）
        if "empty_at_action" in data:
            value = ai_config.normalize_empty_at(data["empty_at_action"])
            ai_config.save_empty_at(s, value)
            done.append("empty_at_action")
        if "context_window_minutes" in data:
            try:
                minutes = int(data["context_window_minutes"])
            except (TypeError, ValueError):
                raise HTTPException(400, "上文回溯上限要是整数（分钟）")
            if not (ai_config.CONTEXT_WINDOW_MIN - 1 <= minutes
                    <= ai_config.CONTEXT_WINDOW_MAX + 1):
                raise HTTPException(
                    400, "上文回溯上限要在 %d~%d 分钟之间"
                         % (ai_config.CONTEXT_WINDOW_MIN, ai_config.CONTEXT_WINDOW_MAX))
            ai_config.save_context_window_minutes(s, minutes)
            done.append("context_window_minutes")
        # 2026-10-12：单条正文上限（H-13 的后端一直都在，界面上一直没有输入框）
        if "max_incoming_chars" in data:
            try:
                chars = int(data["max_incoming_chars"])
            except (TypeError, ValueError):
                raise HTTPException(400, "单条正文上限要是整数（字；0 = 不限制）")
            if chars != 0 and not (ai_config.MAX_INCOMING_CHARS_MIN - 1 <= chars
                                   <= ai_config.MAX_INCOMING_CHARS_MAX + 1):
                raise HTTPException(
                    400, "单条正文上限要在 %d~%d 字之间，或填 0 表示不限制"
                         % (ai_config.MAX_INCOMING_CHARS_MIN,
                            ai_config.MAX_INCOMING_CHARS_MAX))
            ai_config.save_max_incoming_chars(s, chars)
            done.append("max_incoming_chars")
        # 2026-10-12：分条发送（模式 + 最多几条 + 每条字数 + 每字延迟 + 单条间上限）。
        # 五个值机器人侧都是**每条消息现读**，所以改完立即生效、不用重启机器人。
        if "multi_message" in data:
            payload = data["multi_message"]
            if not isinstance(payload, dict):
                raise HTTPException(400, "multi_message 要是对象（只传要改的键）")
            unknown = [k for k in payload
                       if k not in ("mode", "max_parts", "max_part_chars",
                                    "part_delay_ms", "part_delay_max_ms")]
            if unknown:
                raise HTTPException(400, "multi_message 里不认识的键：%s" % "、".join(unknown))
            if "mode" in payload:
                raw = str(payload["mode"] or "").strip().lower()
                if raw not in ("", "off", "auto", "always", "关", "关闭", "单条",
                               "一条", "自动", "总是", "总是分条", "强制",
                               "single", "none", "force", "all", "on", "smart"):
                    raise HTTPException(
                        400, "分条模式只能是 off / auto / always（关 / 自动 / 总是）")
            ai_config.save_multi_message(s, payload)
            done.append("multi_message")
        # 2026-10-12（桌面端 brain_page.py:677-695 同源）：自动通过好友申请 + Agent 模式。
        # 两个都是机器人侧每条消息/每个事件现读 aichat_manager.json 的键 →
        # 改完立即生效，不用重启机器人（机器人收消息时 chat_manager.maybe_reload()）。
        if "auto_approve_friend" in data:
            ai_config.save_auto_approve_friend(s, bool(data["auto_approve_friend"]))
            done.append("auto_approve_friend")
        if "agent_mode_enabled" in data:
            ai_config.save_agent_mode(s, bool(data["agent_mode_enabled"]))
            done.append("agent_mode_enabled")
        # 这些键（群聊开关 / 白名单 / 搭话 / 免@会话 / 各类开关）**全都是热读**：
        # 机器人侧每条消息现读 aichat_manager.json，所以都不需要重启。
        return {"ok": True, "changed": done, "needs_restart": False,
                "hot_reload": True,
                "note": _restart_policy().HOT_LINE}

    # ===== 本机能力清单（与桌面端「插件 → 本机能力（可搜索 · 可开关）」同源）=====
    # 2026-10-12：老板反馈「插件页面看不见好友申请那个功能」，桌面端因此加了这一节
    # （plugins_page.py:104-186）。无头端这边**一直没有对应接口**，控制台也就没有这一节
    # —— 本轮补齐，读写的还是机器人侧 aichat_manager.json 的 disabled_tools
    # （机器人侧 chat.py 每条消息现读，改完立即生效、不用重启）。

    @app.get("/api/ai/capabilities")
    def ai_capabilities(authorization: str = Header("", alias="Authorization")):
        try:
            return capabilities_list()
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"本机能力清单读取失败：{exc}") from exc

    @app.put("/api/ai/capabilities")
    def ai_capabilities_save(body: dict, authorization: str = Header("", alias="Authorization")):
        """开/关一项能力。只写传进来的 name（未知名字照样先列出来核对，不硬拒）。"""
        _require_auth(authorization)
        data = body or {}
        name = str(data.get("name") or "").strip()
        if not name or len(name) > 120:
            raise HTTPException(400, "要给出能力名字（name）")
        if "enabled" not in data or not isinstance(data["enabled"], bool):
            raise HTTPException(400, "enabled 要是 true/false")
        return capabilities_toggle(name, bool(data["enabled"]))

    # ===== 好友申请（2026-10-12 桌面端新增；控制台只读展示）=====

    @app.get("/api/ai/friend-requests")
    def ai_friend_requests(authorization: str = Header("", alias="Authorization")):
        """机器人进程记的待处理好友申请（落盘桥，只读；控制台从不让机器人做事）。"""
        _require_auth(authorization)
        return friend_requests_state()

    # ===== QQ 官方通道：收消息模式 + 一键体检（桌面端 QQ 页同源）=====

    @app.get("/api/qq/group-state")
    def qq_group_state(authorization: str = Header("", alias="Authorization")):
        """机器人到底在收「全量群消息」还是「只收 @」——"群里 @ 它不回"的第一现场证据。"""
        _require_auth(authorization)
        ai_config = _ai_config_module()
        state = ai_config.read_qq_group_state(_settings())
        return {
            "mode": str(state.get("mode") or "none"),
            "text": ai_config.qq_group_state_text(state),
            "hint": ai_config.qq_group_state_hint(state),
            "last_ts": float(state.get("last_ts") or 0),
            "events": state.get("events") or {},
        }

    @app.get("/api/qq/diagnose")
    def qq_diagnose(authorization: str = Header("", alias="Authorization")):
        """官方通道一键体检：凭证、环境、进程、连接、群消息模式、AI 开关逐条给结论。"""
        _require_auth(authorization)
        try:
            from qbotmanager.core import qq_doctor
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"服务端缺少体检模块（qq_doctor）：{exc}") from exc
        rows = qq_doctor.run_checks(_settings(), _LinuxRunState())
        return {
            "summary": qq_doctor.summary(rows),
            "rows": [{"name": r.get("name"), "level": r.get("level"),
                      "text": r.get("text"), "hint": r.get("hint") or ""} for r in rows],
        }

    # ===== 飞书 / 纸飞机 / QQ 官方凭证 / 日志保留（2026-10-04 控制台接口补齐）=====
    # 前端按固定字段名调（契约见模块顶部）；密钥一律只回 *_set 布尔，写操作回 needs_restart。

    @app.get("/api/feishu/status")
    def feishu_status(authorization: str = Header("", alias="Authorization")):
        # 回的是"填没填"这些布尔量，但整机通道状态不该给未登录的人看（与 /api/wechat/status 一致）
        _require_auth(authorization)
        return feishu_state()

    @app.put("/api/feishu/config")
    def feishu_config_save(body: dict, authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        return feishu_save(body or {})

    @app.post("/api/feishu/install-adapter")
    def feishu_install_adapter(authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        return feishu_install_adapter_now()

    @app.get("/api/telegram/status")
    def telegram_status(authorization: str = Header("", alias="Authorization")):
        """纸飞机通道如实回"还没做"。

        桌面端的 ui/pages/telegram_page.py 也只有一句占位（EmptyState），
        所以这里**不假装能配**：控制台照这个开关显示"规划中"就行。
        """
        _require_auth(authorization)
        return {"ok": True, "supported": False, "planned": True,
                "why": "纸飞机通道还在规划中"}

    @app.get("/api/qq/official")
    def qq_official_state_route(authorization: str = Header("", alias="Authorization")):
        # 凭证填没填 + 群名单（openid）都是本机接入信息，要登录
        _require_auth(authorization)
        return qq_official()

    @app.put("/api/qq/official")
    def qq_official_save_route(body: dict, authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        return qq_official_save(body or {})

    @app.get("/api/logs/retention")
    def logs_retention(authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        return logs_retention_state()

    @app.put("/api/logs/retention")
    def logs_retention_save_route(body: dict, authorization: str = Header("", alias="Authorization")):
        _require_auth(authorization)
        return logs_retention_save(body or {})

    @app.post("/api/logs/cleanup-now")
    def logs_cleanup_now_route(authorization: str = Header("", alias="Authorization")):
        """立刻按保留天数清理旧日志（桌面端服务页「立刻清理旧日志」同款）。"""
        _require_auth(authorization)
        return logs_cleanup_now()

    # ===== 星群账号：头像与昵称（与桌面端共用 qbotmanager.core.account）=====
    # 桌面端早就有（core/account.py：头像落盘 + 昵称 + 老官网个人中心同步），
    # 无头端一直缺这块（2026-10-01 老板问"头像昵称做好了是吗"时发现）。

    def _account_module():
        try:
            from qbotmanager.core import account
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"服务端缺少账号模块（account）：{exc}") from exc
        return account

    # 资料同步防抖：上次去官网拉取的时间 / 最小间隔（秒）
    _PROFILE_SYNC_AT = {"t": 0.0}
    _PROFILE_SYNC_MIN_INTERVAL = 10.0

    def _profile_payload(account_mod, acc: dict) -> dict:
        avatar = ""
        try:
            path = account_mod.avatar_file()
            if path.exists():
                avatar = "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()
        except OSError:
            avatar = ""
        return {
            "email": str(acc.get("email") or ""),
            "nickname": str(acc.get("nickname") or ""),
            "signature": str(acc.get("signature") or ""),
            "display_name": account_mod.display_name(acc),
            "plan": str(acc.get("plan") or ""),
            "avatar": avatar,
        }

    @app.get("/api/account/profile")
    def account_profile(authorization: str = Header("", alias="Authorization")):
        """当前登录账号的昵称与头像（头像给 data URL，前端直接塞 <img src>）。

        2026-10-06 修（老板反馈"控制台昵称头像没跟官网同步"）：本机这份账号文件里
        没有昵称/头像时，**顺手去账号服务拉一次**（`/me`），拉不到就照旧返回空的，
        不报错 —— 这样用户登录后一打开就看到了，不用先点「从官网同步」。
        """
        _require_auth(authorization)
        account_mod = _account_module()
        acc = account_mod.load_account()
        if not acc:
            raise HTTPException(401, "本机还没登录星群账号")
        # 每次都试着去官网拉一次，但 10 秒内只拉一次 —— 老板在官网换了头像，
        # 控制台轮询或点「刷新」都能在 10 秒内看到新的。
        # （2026-10-07 修：以前只在"本机完全没有资料"时才拉，本地存着旧头像就永远不更新，
        #   老板实测"官网换了头像，控制台一直显示旧的、点刷新也不好使"。）
        now = time.time()
        if now - _PROFILE_SYNC_AT["t"] >= _PROFILE_SYNC_MIN_INTERVAL:
            _PROFILE_SYNC_AT["t"] = now
            try:
                refreshed = account_mod.refresh_profile()
                if refreshed:
                    acc = refreshed
            except Exception as exc:  # noqa: BLE001 —— 网络不通不该让账号卡片打不开
                _log(f"同步官网昵称/头像失败（先用本机那份）：{exc}")
        return _profile_payload(account_mod, acc)

    @app.post("/api/account/refresh")
    def account_refresh(authorization: str = Header("", alias="Authorization")):
        """从账号服务拉一次 /me，把官网个人中心改过的昵称/头像同步回本机。"""
        _require_auth(authorization)
        account_mod = _account_module()
        acc = account_mod.refresh_profile()
        if not acc:
            raise HTTPException(400, "同步失败：可能未登录或网络不通")
        return _profile_payload(account_mod, acc)

    @app.post("/api/account/profile")
    def account_profile_save(body: dict, authorization: str = Header("", alias="Authorization")):
        """改昵称 / 换头像（与官网个人中心同一套）。

        2026-10-06 补 avatar 分支：前端按契约发 `{avatar: "data:image/…"}` 时，
        老实现只读 nickname，会把昵称**清空**还回 200（等于悄悄改坏数据）。
        现在：带 avatar 就走 upload_avatar；带 nickname 才改昵称；两个都带就都做。
        """
        _require_auth(authorization)
        account_mod = _account_module()
        acc = account_mod.load_account()
        if not acc:
            raise HTTPException(401, "本机还没登录星群账号")
        payload = body or {}
        token = str(acc.get("token") or "")
        did = []

        data_url = str(payload.get("avatar") or "")
        if data_url:
            if not data_url.startswith("data:image/"):
                raise HTTPException(400, "头像格式不对（要 data:image/... 的 data URL）")
            if len(data_url) > 4 * 1024 * 1024:
                raise HTTPException(400, "头像太大了，换一张小一点的")
            res = account_mod.upload_avatar(token, data_url)
            if not res.get("ok"):
                raise HTTPException(400, res.get("detail") or "换头像失败")
            account_mod._apply_profile(res)
            did.append("avatar")

        if "nickname" in payload:
            nickname = str(payload.get("nickname") or "")
            if len(nickname) > 32:
                raise HTTPException(400, "昵称最多 32 个字")
            res = account_mod._post("/api/account/profile", {"nickname": nickname}, token=token)
            if not res.get("ok"):
                raise HTTPException(400, res.get("detail") or "改昵称失败")
            account_mod._apply_profile(res)
            did.append("nickname")

        if not did:
            raise HTTPException(400, "没说要改什么（nickname 或 avatar 至少给一个）")
        out = _profile_payload(account_mod, account_mod.load_account() or acc)
        out["changed"] = did
        return out

    @app.post("/api/account/avatar")
    def account_avatar_save(body: dict, authorization: str = Header("", alias="Authorization")):
        """换头像：前端压成 data URL 传进来，转发给账号服务（它会再裁一次）并落盘。"""
        _require_auth(authorization)
        account_mod = _account_module()
        acc = account_mod.load_account()
        if not acc:
            raise HTTPException(401, "本机还没登录星群账号")
        data_url = str((body or {}).get("avatar") or "")
        if not data_url.startswith("data:image/"):
            raise HTTPException(400, "头像格式不对（要 data:image/... 的 data URL）")
        if len(data_url) > 4 * 1024 * 1024:
            raise HTTPException(400, "头像太大了，换一张小一点的")
        res = account_mod.upload_avatar(str(acc.get("token") or ""), data_url)
        if not res.get("ok"):
            raise HTTPException(400, res.get("detail") or "换头像失败")
        account_mod._apply_profile(res)
        return _profile_payload(account_mod, account_mod.load_account() or acc)


# ===== 本地知识库读写（供上面的路由与自检使用）=====

_KNOWLEDGE_ID_RE = re.compile(r"^[A-Za-z0-9_\-\.]{1,64}$")
_KNOWLEDGE_MAX_TEXT = 2 * 1024 * 1024      # 单篇正文上限 2MB（超过就分几篇放）
_KNOWLEDGE_MAX_TITLE = 120


def _knowledge_module():
    """延迟导入桌面端知识库模块（只依赖 core，不带 PySide6），失败给人话而不是让 app 起不来。"""
    try:
        from qbotmanager.core import knowledge_base
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            500, f"服务端缺少知识库模块（qbotmanager.core.knowledge_base）：{exc}"
        ) from exc
    return knowledge_base


def knowledge_list() -> dict:
    """知识库文档列表（接口层按 id 白名单过滤，避免把乱七八糟的文件名透给前端）。"""
    kb = _knowledge_module()
    s = _settings()
    items = []
    for d in kb.list_documents(s) or []:
        did = str(d.get("id") or "")
        if not _KNOWLEDGE_ID_RE.match(did):
            continue
        items.append({
            "id": did,
            "title": str(d.get("title") or did),
            "chunks": int(d.get("chunks") or 0),
        })
    return {"ok": True, "total": len(items), "items": items,
            "dir": str(kb.docs_dir(s))}


def knowledge_add(title: str, text: str) -> dict:
    """新增一篇文档。正文与标题都必填；正文过长直接拒绝，不要静默截断。"""
    kb = _knowledge_module()
    title = title.strip()
    text = text.strip()
    if not text:
        raise HTTPException(400, "文档内容不能为空")
    if not title:
        title = text.splitlines()[0][:40] or "未命名文档"   # 与桌面端一样：不填就取首行
    if len(title) > _KNOWLEDGE_MAX_TITLE:
        raise HTTPException(400, f"标题最长 {_KNOWLEDGE_MAX_TITLE} 字")
    if len(text.encode("utf-8")) > _KNOWLEDGE_MAX_TEXT:
        raise HTTPException(400, "文档太大（上限 2MB），请拆分后再上传")
    try:
        res = kb.add_document(_settings(), title, text)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"写入知识库失败：{exc}") from exc
    return {"ok": True, "doc": {"id": res.get("id"), "title": res.get("title") or title,
                               "chunks": res.get("chunks")},
            "needs_restart": True}


def knowledge_delete(doc_id: str, confirm: bool) -> dict:
    """删除一篇文档。id 走白名单，且必须确实存在；删除不可撤销，所以要 confirm。"""
    if not confirm:
        raise HTTPException(400, "删除文档需要二次确认（confirm=true）")
    if not _KNOWLEDGE_ID_RE.match(doc_id) or ".." in doc_id:
        raise HTTPException(400, "文档 id 不合法")
    existing = {i["id"] for i in knowledge_list()["items"]}
    if doc_id not in existing:
        raise HTTPException(404, f"没有这篇文档：{doc_id}")
    kb = _knowledge_module()
    try:
        res = kb.delete_document(_settings(), doc_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"删除文档失败：{exc}") from exc
    return {"ok": True, "id": doc_id, "result": res, "needs_restart": True}


# ===== MCP 配置与本地 zip 安装 =====

_ZIP_MAX_BYTES = 20 * 1024 * 1024      # 上传 zip 上限 20MB
_ZIP_MAX_UNPACKED = 100 * 1024 * 1024  # 解压后总量上限 100MB（防解压炸弹）
_ZIP_MAX_MEMBERS = 5000                # 成员数上限
_ZIP_MAX_RATIO = 200                   # 单成员压缩比上限（file_size / compress_size）


def _ai_config_module():
    try:
        from qbotmanager.core import ai_config
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"服务端缺少 ai_config 模块：{exc}") from exc
    return ai_config


def _restart_policy():
    """「这次改动要不要重启」的唯一口径（core.restart_policy）。

    拿不到就用一份最小兜底（不能让接口因为这个辅助模块 500）。
    """
    try:
        from qbotmanager.core import restart_policy
        return restart_policy
    except Exception:  # noqa: BLE001
        class _Fallback:
            HOT_LINE = "设置改了立即生效；代码改了才需要重启，且点一下就能重启。"

            @staticmethod
            def summary() -> dict:
                return {"line": _Fallback.HOT_LINE, "hot_keys": [], "cold_keys": []}

        return _Fallback


def _refresh_bot_hot_config() -> None:
    """把机器人侧的热配置刷一遍（`.env` + 有效开关快照）。

    控制台所有"设置类"改动存完都该调它一次：机器人进程下次读就是新值，
    **不用重启**。失败只记日志（用户还能点重启兜底），绝不把保存弄失败。
    """
    try:
        s = _settings()
        from qbotmanager.core import bot as bot_mod

        bot_mod.write_env(s)
        bot_mod.write_live_gates(s)
    except Exception as exc:  # noqa: BLE001
        _log(f"刷新机器人热配置失败（这次改动可能要重启才生效）：{exc}")


def _agent_profile_module():
    """延迟导入桌面端智能体档案模块（唤醒词提取用它；它只依赖 core，不带 PySide6）。

    仍然包一层：万一以后它被牵进 UI 依赖，无头端这里要给一句人话，而不是让整个 app 起不来。
    """
    try:
        from qbotmanager.core import agent_profile
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            500, f"服务端缺少智能体档案模块（qbotmanager.core.agent_profile）：{exc}"
        ) from exc
    return agent_profile


def _tool_packs_module():
    """延迟导入桌面端能力包模块（本地 zip 安装用它；它只依赖 core）。

    install_tool_pack_zip 必须用这里返回的模块对象：本模块没有模块级 import
    tool_packs_mod，直接引用那个名字会让 /api/tools/install-zip 每次调用都 500。
    """
    try:
        from qbotmanager.core import tool_packs
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            500, f"服务端缺少能力包模块（qbotmanager.core.tool_packs）：{exc}"
        ) from exc
    return tool_packs


# ------------------------------------------------------------ 本机能力清单
# 对应桌面端 `ui/pages/plugins_page.py::_build_capability_panel`（2026-10-12 新增）
# 与 `core/ai_config.py::list_capabilities`（同一份实现，**不另写第二套**）。

def _agent_runtime_schemas() -> list:
    """主程序内置工具清单（拿不到就返回空，不影响界面）。"""
    try:
        from qbotmanager.core.agent.runtime import get_runtime

        return list(get_runtime().schemas("") or [])
    except Exception:  # noqa: BLE001 —— 运行时缺依赖时这一节照旧显示插件那份
        return []


def _plugin_tool_meta() -> tuple:
    """静态解析 ai 插件里的工具声明 + 权限表（与 ai_config._plugin_actions 同一口径）。

    为什么不直接 import 插件：那个包要 nonebot，控制台进程不该为了"看一眼清单"
    把机器人依赖拖进来（桌面端也是这么做的，见 ai_config._plugin_actions 的注释）。
    """
    base = (Path(__file__).resolve().parent.parent / "qbotmanager" / "assets"
            / "plugins" / "ai" / "tools_bridge.py")
    actions = []
    perms = {}
    try:
        text = base.read_text(encoding="utf-8")
    except OSError:
        return actions, perms
    for m in re.finditer(r'_tool\(\s*"([a-z_]+)"\s*,\s*"([^"]{0,200})"', text):
        actions.append({"name": m.group(1), "desc": m.group(2)})
    try:
        perms = dict(re.findall(r'^\s{4}"([a-z_]+)":\s*"([^"]*)",\s*$', text, re.M))
    except Exception:  # noqa: BLE001
        perms = {}
    return actions, perms


def capabilities_list() -> dict:
    """本机能调用的全部能力：名字 / 说明 / 来源 / 需要权限 / 现在开没开。

    与桌面端 `ai_config.list_capabilities` 一一对应（同一份 disabled_tools）。
    `source` 用中文短标签：主程序 / 内置 ai 插件 / 能力包 / 智能体档案。
    返回值里额外带 `hot_reload` 与 `note`，前端照它显示"改完立即生效"。
    """
    cfg = _ai_config_module()
    s = _settings()
    disabled = set(cfg.read_disabled_tools(s))
    rows = []
    for tool in _agent_runtime_schemas():
        name = str(((tool or {}).get("function") or {}).get("name") or "")
        if not name:
            continue
        rows.append({
            "name": name,
            "desc": str(((tool or {}).get("function") or {}).get("description") or "")[:160],
            "source": "主程序",
            "permission": "",
            "enabled": name not in disabled,
        })
    actions, perms = _plugin_tool_meta()
    for item in actions:
        rows.append({
            "name": item["name"],
            "desc": item["desc"][:160],
            "source": "内置 ai 插件",
            "permission": perms.get(item["name"], ""),
            "enabled": item["name"] not in disabled,
        })
    # 已装能力包（装上即用，没有单独开关；列出来是为了让"本机能调什么"完整）
    try:
        packs = _tool_packs_module().list_packs(s) or []
    except Exception:  # noqa: BLE001 —— 能力包读不到不影响插件那份清单
        packs = []
    for pack in packs:
        pid = str(pack.get("id") or pack.get("name") or "")
        if not pid:
            continue
        rows.append({
            "name": "包:" + pid,
            "desc": str(pack.get("desc") or pack.get("title") or "")[:160],
            "source": "能力包",
            "permission": "",
            "enabled": True,
        })
    # 名字去重（同名只留第一条：主程序优先）
    seen = set()
    uniq = []
    for row in rows:
        if row["name"] in seen:
            continue
        seen.add(row["name"])
        uniq.append(row)
    return {
        "ok": True,
        "items": uniq,
        "total": len(uniq),
        "enabled": sum(1 for r in uniq if r["enabled"]),
        "disabled_tools": sorted(disabled),
        "has_approve_friend": any(r["name"] == "approve_friend" for r in uniq),
        "has_agent_tools": any(r["name"] == "send_to" for r in uniq),
        "hot_reload": True,
        "note": "关掉某个开关，机器人就不再用它 —— 模型那边直接看不见。改完立即生效，不用重启机器人。",
    }


def capabilities_toggle(name: str, enabled: bool) -> dict:
    """开/关一项能力（写机器人侧同一份 disabled_tools；机器人每条消息现读）。"""
    cfg = _ai_config_module()
    s = _settings()
    before = set(cfg.read_disabled_tools(s))
    cfg.set_tool_enabled(s, name, bool(enabled))
    after = set(cfg.read_disabled_tools(s))
    return {
        "ok": True,
        "name": name,
        "enabled": bool(enabled),
        "changed": sorted(before ^ after),
        "needs_restart": False,
        "hot_reload": True,
        "note": _restart_policy().HOT_LINE,
    }


# ------------------------------------------------------------ 好友申请（只读桥）
# 机器人进程把待处理申请写一份 JSON（assets/plugins/ai/friend_requests.py），
# 控制台进程只读它 —— 两个进程不共享内存，控制台**绝不**替机器人做动作。
# 落盘路径由 deploy.build_settings().bot_dir 决定，两边用同一个环境变量对齐：
#   ASTROSWARM_BOT_DIR（deploy.build_bot_env 注入；老的没注入时回退 bot/ 相对路径）。

def friend_requests_path() -> Path:
    try:
        bot_dir = Path(str(_settings().bot_dir))
    except Exception:  # noqa: BLE001
        bot_dir = Path.cwd() / "bot"
    return bot_dir / "data" / "friend_requests.json"


def friend_requests_state() -> dict:
    """待处理好友申请（只读）。文件不在 = 机器人还没收到过申请，不是错误。"""
    path = friend_requests_path()
    rows = []
    updated = 0.0
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        if isinstance(data, dict):
            rows = [r for r in (data.get("pending") or []) if isinstance(r, dict)]
            updated = float(data.get("updated_at") or 0)
    except FileNotFoundError:
        rows = []
    except Exception as exc:  # noqa: BLE001 —— 坏文件不该让整页 500
        raise HTTPException(500, f"好友申请文件读不出来：{exc}") from exc
    cfg = _ai_config_module()
    s = _settings()
    try:
        auto = cfg.read_auto_approve_friend(s)
    except Exception:  # noqa: BLE001
        auto = False
    return {
        "ok": True,
        "pending": rows,
        "total": len(rows),
        "auto_approve_friend": auto,
        "updated_at": updated,
        "path": str(path),
        "read_only": True,
        "note": ("控制台只读这份清单：机器人进程才是处理好友申请的那一方。"
                 "要自动通过就打开「自动通过好友申请」开关。"),
    }


def mcp_config() -> dict:
    """读 MCP 配置（存 ai_config 的 manager 文件里，机器人侧读同一份）。"""
    cfg = _ai_config_module()
    try:
        return {"ok": True, "mcp": cfg.read_mcp(_settings())}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"读取 MCP 配置失败：{exc}") from exc


def mcp_save(enabled: bool, servers: dict) -> dict:
    """写 MCP 配置。只做结构校验：每个服务器必须有 type，sse/ws 要有 url，stdio 要有 command。"""
    cfg = _ai_config_module()
    clean = {}
    for name, spec in (servers or {}).items():
        key = str(name).strip()
        if not key or len(key) > 64:
            raise HTTPException(400, f"MCP 名称不合法：{name!r}")
        if not isinstance(spec, dict):
            raise HTTPException(400, f"「{key}」的配置必须是对象")
        typ = str(spec.get("type") or "sse").strip().lower()
        if typ not in ("sse", "stdio", "ws", "http", "streamable_http"):
            raise HTTPException(400, f"「{key}」的 type 不支持：{typ}")
        if typ == "stdio":
            if not str(spec.get("command") or "").strip():
                raise HTTPException(400, f"「{key}」是 stdio，必须填 command")
        elif not str(spec.get("url") or "").strip():
            raise HTTPException(400, f"「{key}」必须填 url")
        item = {"type": typ, "enabled": bool(spec.get("enabled", True))}
        for k in ("url", "command", "args", "env", "headers"):
            if spec.get(k) not in (None, "", [], {}):
                item[k] = spec[k]
        clean[key] = item
    try:
        ok = cfg.save_mcp(_settings(), bool(enabled), clean)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"保存 MCP 配置失败：{exc}") from exc
    if not ok:
        raise HTTPException(500, "保存 MCP 配置失败（写文件返回失败）")
    return {"ok": True, "enabled": bool(enabled), "servers": len(clean),
            "needs_restart": True}


# 唤醒词兜底：与桌面端 agent_profile.DEFAULT_WAKE_WORDS / 内置档案默认词对齐
_PERSONA_WAKE_DEFAULTS = {
    "eva": ["EVA", "eva", "艾娃"],
    "liqinghan": ["李清菡", "学姐", "姐姐", "菡姐", "菡菡"],
}


def wake_words(persona_text: str = "", persona_mode: str = "") -> dict:
    """按人格文本生成唤醒词。

    与桌面端 BrainPage「按人格生成」同语义，但**不调用外部模型**：
    桌面端走 agent_profile.generate_wake_words(..., call_llm)，
    无头端不接 LLM —— 按钮点一下就要花钱、还可能因为没有 key 而失败，
    代价远大于收益。这里复用同一套确定性正则（agent_profile.extract_wake_words，
    也就是桌面端 LLM 失败时的兜底路径），提不到称呼再退回档案默认词。

    人格文本来源：请求体里的 personality（界面上正在编辑的那份）优先；
    没有就回落到 ai_config 里已保存的人格。两条路都空 → 返回默认词。
    所以这个接口永远返回 200 + 非空 words，前端不会再有「Not Found」。
    """
    prof = _agent_profile_module()
    mode = str(persona_mode or "").strip().lower()
    default = list(_PERSONA_WAKE_DEFAULTS.get(mode) or prof.DEFAULT_WAKE_WORDS)

    text = str(persona_text or "").strip()
    if not text:
        try:
            text = str(_ai_config_module().read_personality(_settings()) or "").strip()
        except Exception:  # noqa: BLE001 —— 读不到人格不是错误，走默认词
            text = ""

    words = []
    if text:
        try:
            words = list(prof.extract_wake_words(text) or [])
        except Exception:  # noqa: BLE001
            words = []
    source = "persona" if words else "default"
    if not words:
        words = default
    return {"ok": True, "words": words, "source": source}


def _zip_reject_reason(names) -> str:
    """zip slip 检查：绝对路径 / `..` / 符号链接一律拒绝。"""
    for n in names:
        raw = str(n or "").replace("\\", "/")
        if not raw:
            continue
        if raw.startswith("/") or raw.startswith("../") or "/../" in raw or raw == "..":
            return f"压缩包里有越界路径：{raw}"
        if raw.startswith("~"):
            return f"压缩包里有可疑路径：{raw}"
    return ""


def _zip_bomb_reason(infos) -> str:
    """解压炸弹检查：只看压缩包里的声明值，安装前就拦掉。

    除了 zip 本身的大小，还要看解压后的总量：0.19MB 的包实测能解出 200MB（1027:1），
    只看上传体积，最坏能往 tool_packs 目录写出约 20GB。
    """
    infos = list(infos or [])
    if len(infos) > _ZIP_MAX_MEMBERS:
        return f"压缩包成员太多（{len(infos)} 个，上限 {_ZIP_MAX_MEMBERS}）"
    total = 0
    for info in infos:
        size = int(getattr(info, "file_size", 0) or 0)
        packed = int(getattr(info, "compress_size", 0) or 0)
        total += size
        if total > _ZIP_MAX_UNPACKED:
            return (f"解压后太大（超过 {_ZIP_MAX_UNPACKED // (1024 * 1024)}MB），"
                    "拒绝安装")
        if size > 0 and packed > 0 and size / packed > _ZIP_MAX_RATIO:
            return (f"压缩比异常（{getattr(info, 'filename', '')}："
                    f"{size}/{packed}），疑似解压炸弹")
    return ""


def install_tool_pack_zip(filename: str, content_b64: str, confirm: bool) -> dict:
    """从上传的 zip 安装能力包。

    安全边界，一条都不能省：
    1. 必须开发者模式开启（与桌面端「开发者模式开启后可安装第三方 zip」一致）
    2. 必须 confirm=true（明确告知风险自负）
    3. 只接受**上传内容**，不接受服务器本地路径 —— 服务是 root 跑的
    4. 先做 zip slip 校验（绝对路径 / `..` / 符号链接），再校验大小
    5. 落临时目录、文件名自生成，装完删掉
    """
    import base64

    if not dev_mode():
        raise HTTPException(403, "本地 zip 安装仅在开发者模式下可用（设置 → 开发者模式）")
    if not confirm:
        raise HTTPException(400, "安装第三方 zip 需要二次确认（confirm=true）")
    name = str(filename or "").strip()
    if not name.lower().endswith(".zip"):
        raise HTTPException(400, "只支持 .zip 上传")
    if not content_b64:
        raise HTTPException(400, "没有收到文件内容")
    if len(content_b64) > _ZIP_MAX_BYTES * 4 // 3 + 1024:
        raise HTTPException(400, "文件太大（上限 20MB）")
    try:
        raw = base64.b64decode(content_b64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"base64 解码失败：{exc}") from exc
    if len(raw) > _ZIP_MAX_BYTES:
        raise HTTPException(400, "文件太大（上限 20MB）")

    import hashlib
    import io
    import tempfile
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            bad = _zip_reject_reason(zf.namelist())
            if bad:
                raise HTTPException(400, bad)
            bomb = _zip_bomb_reason(zf.infolist())
            if bomb:
                raise HTTPException(400, bomb)
            for info in zf.infolist():
                # 符号链接：外部属性高 16 位 == 0xA1FF
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise HTTPException(400, f"压缩包里含符号链接：{info.filename}")
            manifest_name = next(
                (n for n in zf.namelist() if n.endswith("manifest.json")), "")
            if not manifest_name:
                raise HTTPException(400, "压缩包里没有 manifest.json（能力包清单）")
            manifest = json.loads(zf.read(manifest_name).decode("utf-8"))
    except HTTPException:
        raise
    except zipfile.BadZipFile as exc:
        raise HTTPException(400, f"不是有效的 zip：{exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"读取压缩包失败：{exc}") from exc

    pid = str(manifest.get("id") or "").strip()
    if not re.match(r"^[A-Za-z0-9_\-\.]{1,64}$", pid) or ".." in pid:
        raise HTTPException(400, f"manifest 里的 id 不合法：{pid!r}")

    entry = {
        "id": pid,
        "name": str(manifest.get("name") or pid),
        "version": str(manifest.get("version") or "0.0.0"),
        "kind": str(manifest.get("kind") or "tool-pack"),
        "url": "local-upload",
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    tmpdir = tempfile.mkdtemp(prefix="sw-zip-")
    try:
        path = Path(tmpdir) / "pack.zip"
        path.write_bytes(raw)
        res = _tool_packs_module().install_pack(_settings(), entry, zip_path=path)
    except HTTPException:
        raise
    except PermissionError as exc:
        # 权益不足是「没买这个包」，不是服务端故障：兜成 500 会让前端以为要重试
        raise HTTPException(403, f"当前账号无权安装该能力包：{exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"安装失败：{exc}") from exc
    finally:
        import shutil as _shutil

        _shutil.rmtree(tmpdir, ignore_errors=True)
    return {"ok": True, "pack": {"id": pid, "name": entry["name"], "version": entry["version"]},
            "result": res, "needs_restart": True}


# ===== 消息与统计（消息中心 / 首页真实统计）=====

class _LinuxRunState:
    """把 Linux 的运行状态包成桌面端 qq_doctor 期望的那两个方法。

    桌面端那边传的是 core/manager.py；无头端没有这个类，运行状态在 deploy.status()
    里（state: running/installed/not_installed + qq: connected/stopped/unknown）。
    接口对不上就会误报"机器人没在跑"，所以这里显式转一层。
    """

    def bot_running(self) -> bool:
        try:
            return str(deploy.status().get("state") or "") == "running"
        except Exception:  # noqa: BLE001
            return False

    def qq_login_state(self) -> dict:
        try:
            state = str(deploy.status().get("qq") or "")
        except Exception:  # noqa: BLE001
            state = ""
        label = {
            "connected": "已连接（协议端在线）",
            "stopped": "没连上（协议端不在线）",
            "unknown": "读不到连接状态",
        }.get(state, "")
        return {"connected": state == "connected", "reason": label}


def _message_store():
    """延迟导入桌面端消息模块（只依赖 core）。"""
    try:
        from qbotmanager.core import message_store
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            500, f"服务端缺少消息模块（qbotmanager.core.message_store）：{exc}"
        ) from exc
    return message_store


def _conversation_key(platform, scene, room) -> str:
    """把 (platform, scene, room) 还原成会话 key（与 message_store._parse_key 互逆）。

    `load_conversations()` 造出来的字典里**没有 key**，只有 platform/scene/room。
    前端要一个稳定的 key 做去重与「哪个群/哪个好友」的显示，所以在接口层补一个，
    不去改桌面端共用的 message_store（那会牵动桌面端界面）。
    """
    platform = str(platform or "qq").lower()
    scene = str(scene or "").strip().lower()
    room = str(room or "").strip()
    if not room:
        return ""
    if platform == "qq":
        return f"group_{room}" if scene == "group" else room
    return f"{platform}:{scene or 'private'}:{room}"


def messages_list(limit: int = 50) -> dict:
    """最近会话列表。

    只透出标量字段：一条会话里可能带着整段聊天记录，列表接口不能把正文全吐给前端
    （一来很慢，二来没必要）。正文留给以后真正需要的接口。

    `scene`/`room` 必须在白名单里：它们是「这个会话是哪个群 / 哪个好友」的唯一来源，
    丢了以后界面上多个 QQ 会话会都显示成「QQ 会话」，分不清谁是谁。
    """
    ms = _message_store()
    s = _settings()
    limit = max(1, min(int(limit or 50), 500))
    convs = []
    for c in (ms.load_conversations(s, limit=limit) or []):
        if not isinstance(c, dict):
            continue
        item = {"count": int(c.get("count") or 0)}
        for k in ("key", "title", "name", "platform", "scene", "room",
                  "ts", "last", "last_text"):
            v = c.get(k)
            if isinstance(v, (str, int, float)) and str(v) != "":
                item[k] = v[:200] if isinstance(v, str) else v
        if not item.get("key"):
            key = _conversation_key(item.get("platform"), item.get("scene"), item.get("room"))
            if key:
                item["key"] = key
        convs.append(item)
    dsh = []
    for c in (ms.load_dsh_conversations(s, limit=limit) or []):
        if isinstance(c, dict):
            dsh.append({"key": str(c.get("key") or "")[:120],
                        "count": int(c.get("count") or 0),
                        "ts": c.get("ts") or 0})
    try:
        ctx_file = str(ms.context_file(s))
    except Exception:  # noqa: BLE001
        ctx_file = ""          # 拿不到路径不影响列表本身（旧版本 core 可能没有这个函数）
    return {"ok": True, "total": len(convs), "conversations": convs,
            "dsh": dsh, "file": ctx_file}


def stats_summary() -> dict:
    """首页统计：全部来自机器人真实落盘数据。"""
    ms = _message_store()
    s = _settings()
    try:
        summary = dict(ms.brain_summary(s) or {})
    except Exception as exc:  # noqa: BLE001
        summary = {"error": str(exc)}
    try:
        summary["messages"] = int(ms.load_message_count(s) or 0)
    except Exception as exc:  # noqa: BLE001
        summary["messages"] = 0
        summary.setdefault("error", str(exc))
    summary["ok"] = True
    return summary


# ===== 记忆库读写（供上面的路由与自检使用）=====

# 记忆库的「读 → 改 → 原子写」必须整体串行：并发删不同 index 会互相覆盖，
# 10 条删除最后只落地 1 条。
_MEMORY_LOCK = threading.Lock()


def _memory_file() -> Path:
    """机器人记忆库文件。

    桌面端已把路径封装在 ai_config.memory_file()（<data>/bot/data/ai/aichat_memory.json），
    无头端不要自己拼路径，否则换版本就对不上了。
    """
    from qbotmanager.core import ai_config

    return ai_config.memory_file(_settings())


def _memory_read() -> dict:
    """读记忆库。文件不存在=空结构；**文件存在但解析失败要报错**，不能当成空。

    解析失败不能静默返回默认值：紧接着一次保存就会把用户攒下来的记忆覆盖掉。
    """
    import json as _json

    path = _memory_file()
    if not path.exists():
        return {"global": [], "users": {}}
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            500, f"记忆库文件解析失败（{exc}）：{path} 请先修好或改名，避免保存时覆盖")
    if not isinstance(data, dict):
        raise HTTPException(500, "记忆库格式异常（顶层不是 JSON 对象）")
    data.setdefault("global", [])
    data.setdefault("users", {})
    return data


def _memory_backup(path: Path) -> str:
    """改动前留一份带时间戳的备份（删错了能找回来）。

    名字里带一段随机后缀：只精确到秒的话，同一秒内连点几次「删除」会写到同一个备份路径。
    """
    import shutil as _shutil

    if not path.exists():
        return ""
    unique = "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:8])
    bak = path.with_name(path.name + ".bak." + unique)
    _shutil.copy(path, bak)
    return str(bak)


def _memory_write(data: dict) -> None:
    """原子写：唯一临时文件 + fsync + os.replace（同 headless_config.save 的做法）。

    临时文件名必须唯一：固定 `<名字>.tmp` 时并发写同一个 fd，会得到互相穿插的非法 JSON，
    记忆时间线直接 500。
    """
    import json as _json
    import os as _os

    path = _memory_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.tmp.%d.%s" % (path.name, _os.getpid(), uuid.uuid4().hex[:8]))
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            _json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            _os.fsync(fh.fileno())
        _os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def memory_list() -> dict:
    """记忆时间线：全局记忆 + 按用户分组（每组带条数与最近若干条）。"""
    data = _memory_read()
    gl = list(data.get("global") or [])
    groups = []
    for uid, items in (data.get("users") or {}).items():
        lst = list(items or [])
        groups.append({"user": str(uid), "count": len(lst), "items": lst[-50:]})
    groups.sort(key=lambda g: g["count"], reverse=True)
    return {
        "ok": True,
        "total": len(gl) + sum(g["count"] for g in groups),
        "global_count": len(gl),
        "global": gl[-200:],
        "users": groups,
        "file": str(_memory_file()),
    }


def memory_export(fmt: str = "json"):
    """导出记忆库。json = 原始结构；md = 人可读的时间线。"""
    import json as _json

    data = _memory_read()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if str(fmt).lower() in ("md", "markdown"):
        lines = ["# 记忆时间线导出", "", f"导出时间：{stamp}", ""]
        gl = list(data.get("global") or [])
        lines.append(f"## 全局记忆（{len(gl)} 条）")
        lines += [f"- {_memory_text(x)}" for x in gl] or ["- （空）"]
        for uid, items in (data.get("users") or {}).items():
            lst = list(items or [])
            lines += ["", f"## 用户 {uid}（{len(lst)} 条）"]
            lines += [f"- {_memory_text(x)}" for x in lst] or ["- （空）"]
        body = ("\n".join(lines) + "\n").encode("utf-8")
        return f"memory-{stamp}.md", "text/markdown; charset=utf-8", body
    body = _json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    return f"memory-{stamp}.json", "application/json; charset=utf-8", body


def _memory_text(item) -> str:
    """把一条记忆压成一行文本（结构不固定：可能是字符串，也可能是 dict）。"""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for k in ("text", "content", "summary", "fact", "value"):
            if item.get(k):
                return str(item[k]).strip()
        return str(item)[:300]
    return str(item)


def memory_delete(scope: str, user: str, index: int) -> dict:
    """删一条：scope=global 删全局；scope=user 删某用户下的第 index 条（0 基，负数从末尾数）。

    scope 是白名单（global/user），index 由路由层保证「显式传了整数」——
    index 缺省 -1 时，前端漏字段或一次探测请求就会静默删掉最后一条用户记忆。
    """
    if scope not in ("global", "user"):
        raise HTTPException(400, "scope 只能是 global 或 user")
    if scope == "user" and not str(user or ""):
        raise HTTPException(400, "scope=user 时必须提供 user")
    with _MEMORY_LOCK:      # 读-改-写整体串行，避免并发丢更新/写出坏 JSON
        data = _memory_read()
        path = _memory_file()
        if scope == "user":
            users = data.get("users") or {}
            if user not in users:
                raise HTTPException(404, f"没有这个用户分组：{user}")
            lst = list(users.get(user) or [])
            if not lst:
                raise HTTPException(400, "该用户没有记忆可删")
            if index < 0:
                index += len(lst)
            if index < 0 or index >= len(lst):
                raise HTTPException(400, f"index 越界（0-{len(lst) - 1}）")
            removed = lst.pop(index)
            users[user] = lst
            data["users"] = users
        else:
            lst = list(data.get("global") or [])
            if not lst:
                raise HTTPException(400, "没有全局记忆可删")
            if index < 0:
                index += len(lst)
            if index < 0 or index >= len(lst):
                raise HTTPException(400, f"index 越界（0-{len(lst) - 1}）")
            removed = lst.pop(index)
            data["global"] = lst
        backup = _memory_backup(path)
        _memory_write(data)
    return {"ok": True, "removed": _memory_text(removed), "backup": backup,
            "needs_restart": True}


def memory_clear(confirm: bool) -> dict:
    """清空全部记忆（备份保留，confirm 不为真一律不执行）。"""
    if not confirm:
        raise HTTPException(400, "清空记忆需要二次确认（confirm=true）")
    with _MEMORY_LOCK:
        data = _memory_read()
        path = _memory_file()
        total = len(data.get("global") or []) + sum(
            len(v or []) for v in (data.get("users") or {}).values())
        backup = _memory_backup(path)
        _memory_write({"global": [], "users": {}})
    return {"ok": True, "cleared": total, "backup": backup, "needs_restart": True}


# ---- 内置记忆（程序侧三档那套）：与桌面端「AI 大脑 → 现在记住了什么」同一份数据 ----

def _builtin_memory_module():
    """延迟导入内置记忆模块。老装机环境没有它 → 503 并说清原因，而不是 500。"""
    try:
        from qbotmanager.core import memory as memory_mod
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"服务端缺少内置记忆模块（qbotmanager.core.memory）：{exc}") from exc
    return memory_mod


def builtin_memory_overview(qq: str = "") -> dict:
    """「现在记住了什么」：档位 + 条数 + 条目明细。

    **只读**，而且走的是内置模块自己的 list_memories——它会把 off 档处理成空，
    与机器人侧看到的一致（控制台不会显示机器人其实看不见的东西）。
    """
    mod = _builtin_memory_module()
    cfg = _ai_config_module()          # 档位说明的文案与桌面端共用同一份，别各写各的
    level = mod.level()
    info = dict(mod.stats() or {})
    rows: list = []
    if level != "off":
        try:
            conn = mod.store().connect()
            if qq:
                people = [str(qq)]
            else:
                # 按「最近写过记忆的人」排前面。必须 GROUP BY：SELECT DISTINCT 配
                # ORDER BY MAX(id) 在 SQLite 里是 misuse of aggregate（线上真踩过）。
                people = [str(r["qq"]) for r in conn.execute(
                    "SELECT qq, MAX(id) AS last_id FROM memories WHERE expired=0 "
                    "GROUP BY qq ORDER BY last_id DESC LIMIT 50")]
            for person in people:
                for r in conn.execute(
                        "SELECT id, qq, fact, kind, weight, ts, expired FROM memories "
                        "WHERE qq=? AND expired=0 ORDER BY id DESC LIMIT 50", (person,)):
                    rows.append({"id": int(r["id"]), "qq": str(r["qq"]),
                                 "fact": str(r["fact"]), "kind": str(r["kind"] or "event"),
                                 "weight": int(r["weight"] or 3),
                                 "ts": int(r["ts"] or 0)})
        except Exception as exc:  # noqa: BLE001 —— 读库失败要说出来，不能显示成「没有记忆」
            raise HTTPException(500, f"读内置记忆库失败：{exc}") from exc
    return {
        "ok": True,
        "level": level,
        "level_label": cfg.MEMORY_LEVEL_LABELS.get(level, level),
        "level_note": cfg.MEMORY_LEVEL_NOTE,
        "options": cfg.memory_level_info()["options"],
        "total": int(info.get("total") or 0),
        "promises": int(info.get("promises") or 0),
        "impressions": int(info.get("impressions") or 0),
        "expired": int(info.get("expired") or 0),
        "db": str(info.get("db") or ""),
        "rows": rows,
        "truncated": len(rows) >= 50 * 50,
    }


def builtin_memory_forget(qq: str, fact: str = "") -> dict:
    """「忘掉这件事」：真删（不是标记）。fact 留空 = 清掉这个人的全部记忆。"""
    mod = _builtin_memory_module()
    if mod.level() == "off":
        raise HTTPException(400, "当前档位是「无」，本来就没有在记")
    removed = list(mod.forget(str(qq), str(fact or "")) or [])
    return {"ok": True, "removed": removed, "count": len(removed),
            "scope": "all" if not str(fact or "").strip() else "one",
            "needs_restart": False}


# ===== 通道配置（飞书 / QQ 官方）与日志保留 =====
# 存哪：
#   飞书凭证、日志保留天数 → 无头配置 config_home/config.json，键名与桌面端 Settings 的
#   同名字段一模一样（feishu_enabled / feishu_app_id / ... / logs_keep_days）；
#   QQ 官方凭证 → 已有的 qq_app_id / qq_app_secret / qq_app_token / qq_channel
#   （deploy.build_settings() 本来就把它们映射进 Settings，机器人的 write_env 也读它）。
# 为什么不像桌面端那样写 <root>/settings.json：无头端的机器人启动读的是 config.json
#   （deploy.build_settings），写 settings.json 没人读 —— 控制台存了等于没存。

_CLEAR_VALUE = "__clear__"        # 前端用它表示"把这个字段清空"
_SECRET_MAX_LEN = 512             # 凭证字段长度上限（挡手滑粘进来一整页东西）
_LOGS_KEEP_DEFAULT = 10           # 老板定的默认：日志留最近 10 天
_LOGS_KEEP_MIN, _LOGS_KEEP_MAX = 1, 365
_ADAPTER_WAIT_SECONDS = 25        # 装适配器最多让请求等这么久（再长就会被 nginx 掐掉）
_ADAPTER_LOG_LINES = 120          # 安装日志最多留多少行（给前端展示）

# deploy.build_settings() 没映射、但机器人侧要用到的字段（键名与桌面端 Settings 字段一致）
# 少了这一步，控制台把飞书凭证存进 config.json 后机器人读不到：write_env() 拼不出
# FEISHU_BOTS、适配器也不会注册 —— 界面显示"就绪"而机器人根本不连，正是桌面端飞书页
# 明确拒绝的"假装已接入"。deploy.py 不在本次改动范围内，所以在下面包一层补上。
_CONSOLE_SETTINGS_FIELDS = (
    ("feishu_enabled", "bool"),
    ("feishu_app_id", "str"),
    ("feishu_app_secret", "str"),
    ("feishu_encrypt_key", "str"),
    ("feishu_verification_token", "str"),
)

_bridge_lock = threading.Lock()
_bridge_installed = False

# 装适配器是后台线程 + 轮询等待：pip 要联网，几十秒起步，同步等必被 nginx 掐
_adapter_lock = threading.Lock()
_adapter_state = {"running": False, "log": [], "ok": None, "error": "", "started_at": 0.0}

# 适配器装没装的缓存：bot.feishu_adapter_installed() 每次都要起一个 `pip show` 子进程
# （实测 0.5~1.5 秒），状态卡被控制台轮询会把 CPU 白烧掉。
_adapter_cache = {"key": "", "at": 0.0, "ok": False}
_ADAPTER_CACHE_TTL = 15.0


def _as_bool(value) -> bool:
    """配置里的布尔（手改过的 config.json 可能是 "true"/"1" 这类字符串）。"""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "是", "开")
    return bool(value)


def _clamp_keep_days(value) -> int:
    """日志保留天数收敛到 1~365，脏值回默认 10（与桌面端 QSpinBox 的范围一致）。"""
    try:
        days = int(value)
    except (TypeError, ValueError):
        return _LOGS_KEEP_DEFAULT
    return max(_LOGS_KEEP_MIN, min(days, _LOGS_KEEP_MAX))


def _overlay_console_fields(settings, cfg: dict):
    """把无头配置里那几个 deploy.build_settings() 不认识的字段补到 Settings 上。"""
    for key, kind in _CONSOLE_SETTINGS_FIELDS:
        if key not in cfg or cfg.get(key) is None:
            continue
        try:
            setattr(settings, key, _as_bool(cfg[key]) if kind == "bool" else str(cfg[key]))
        except (TypeError, ValueError):   # noqa: PERF203 —— 单字段脏了不该拖垮整份配置
            continue
    if "logs_keep_days" in cfg:
        settings.logs_keep_days = _clamp_keep_days(cfg.get("logs_keep_days"))
    return settings


def install_settings_bridge() -> bool:
    """给 deploy.build_settings() 包一层，让机器人的启动路径也拿到飞书凭证 / 日志保留天数。

    幂等：重复调用（register 被调多次、selftest 又建一个 app）只包一次。
    将来 deploy.build_settings() 自己映射了这几个键，这个包装可以整段删掉 ——
    它叠加的就是同一份 config.json，不会重复也不会打架。
    """
    global _bridge_installed
    with _bridge_lock:
        current = deploy.build_settings
        if _bridge_installed or getattr(current, "_console_bridge", False):
            return False
        original = current

        @functools.wraps(original)
        def build_settings():
            return _overlay_console_fields(original(), headless_config.load())

        build_settings._console_bridge = True
        deploy.build_settings = build_settings
        _bridge_installed = True
    return True


def _channel_settings():
    """_settings() 再补上控制台写在无头配置里的通道字段（飞书凭证 / 日志保留天数）。

    为什么不直接用 _settings()：deploy.build_settings() 建的是全新 Settings，
    飞书那 5 个键不在它的映射表里；不补的话控制台自己都读不回来 ——
    状态卡会永远显示"没填"。这里和 install_settings_bridge() 是同一份口径。
    """
    s = _overlay_console_fields(_settings(), headless_config.load())
    py = _bot_python(s)
    if py is not None:
        # build_settings() 给的 python_exe 是 python_dir/"python.exe"（Windows 名），
        # Linux 上不存在 —— 不换成机器人 venv 的那个，adapter_installed 永远是 False。
        s.python_exe = py
    return s


def _save_headless(cfg: dict) -> dict:
    """把整份无头配置写回去（headless_config.save 会自己合并默认值、原子落盘）。"""
    try:
        return headless_config.save(cfg)
    except Exception as exc:  # noqa: BLE001 —— 给人话，不让 OSError 裸奔成 500 堆栈
        raise HTTPException(
            500, f"保存配置失败（{headless_config.config_path()}）：{exc}") from exc


def _bot_module():
    """延迟导入机器人模块（装适配器 / 判适配器装没装都用它）。"""
    try:
        from qbotmanager.core import bot as bot_mod
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"服务端缺少机器人模块（qbotmanager.core.bot）：{exc}") from exc
    return bot_mod


def _apply_secret(data: dict, key: str, current: str):
    """算一个凭证字段的新值，返回 (新值, 是否变了)。

    写规则（前后端约定）：字段没传 / 传 null / 传空串 = **不改**（前端回填不了密钥，
    空串只表示"这栏没动过"）；传 `__clear__` = 清空；其它 = 覆盖。
    这样前端可以整表提交而不必先把密钥读出来（后端也永远不回显它）。
    """
    if key not in data or data.get(key) is None:
        return current, False
    value = str(data.get(key)).strip()
    if not value:
        return current, False
    if value == _CLEAR_VALUE:
        value = ""
    elif len(value) > _SECRET_MAX_LEN or "\n" in value or "\r" in value:
        raise HTTPException(
            400, f"{key} 不合法：最多 {_SECRET_MAX_LEN} 个字符，也不能带换行")
    return value, value != current


def _log_adapter(line) -> None:
    text = str(line or "").strip()
    if not text:
        return
    with _adapter_lock:
        _adapter_state["log"] = (_adapter_state["log"] + [text])[-_ADAPTER_LOG_LINES:]


def _feishu_adapter_installed(settings, refresh: bool = False) -> bool:
    """飞书适配器装没装（带 15 秒缓存，见 _adapter_cache 的说明）。"""
    key = str(getattr(settings, "python_exe", "") or "")
    now = _now()
    with _adapter_lock:
        if (not refresh and _adapter_cache["key"] == key
                and now - _adapter_cache["at"] < _ADAPTER_CACHE_TTL):
            return bool(_adapter_cache["ok"])
    try:
        ok = bool(_bot_module().feishu_adapter_installed(settings))
    except Exception as exc:  # noqa: BLE001 —— 查不出来按"没装"处理，别让状态接口 500
        _log(f"检查飞书适配器失败：{exc}")
        ok = False
    with _adapter_lock:
        _adapter_cache.update(key=key, at=now, ok=ok)
    return ok


def feishu_state(settings=None) -> dict:
    """飞书通道状态。**只回 *_set 布尔**，App Secret / Token 一律不回显。"""
    s = settings if settings is not None else _channel_settings()
    app_id = str(getattr(s, "feishu_app_id", "") or "").strip()
    secret = str(getattr(s, "feishu_app_secret", "") or "").strip()
    token = str(getattr(s, "feishu_verification_token", "") or "").strip()
    encrypt = str(getattr(s, "feishu_encrypt_key", "") or "").strip()
    enabled = bool(getattr(s, "feishu_enabled", False))
    installed = _feishu_adapter_installed(s)
    # 判据与机器人侧 bot._feishu_ready() 对齐（它还要求 verification_token），
    # 否则这里显示"就绪"、机器人却不会注册飞书适配器。
    if not enabled:
        why = "飞书通道没开：勾上「启用飞书通道」再保存"
    elif not app_id or not secret:
        why = "还没填 App ID / App Secret（飞书开放平台 → 开发者后台 → 凭证与基础信息）"
    elif not token:
        why = "还差 Verification Token（事件订阅页给的）：缺它机器人不会注册飞书适配器"
    elif not installed:
        why = "适配器（nonebot-adapter-feishu）还没装：点「装适配器」（要联网）"
    else:
        why = ""
    return {
        "ok": True,
        "enabled": enabled,
        "app_id_set": bool(app_id),
        "app_secret_set": bool(secret),
        "encrypt_key_set": bool(encrypt),
        "verification_token_set": bool(token),
        "adapter_installed": installed,
        "ready": not why,
        "why": why,
    }


def feishu_save(data: dict) -> dict:
    """写飞书配置：只有真的变了才落盘（免得每次保存都翻一次配置备份）。"""
    data = data or {}
    cfg = headless_config.load()
    changed = []
    if "enabled" in data and data.get("enabled") is not None:
        want = _as_bool(data.get("enabled"))
        if want != _as_bool(cfg.get("feishu_enabled", False)):
            cfg["feishu_enabled"] = want
            changed.append("enabled")
    for key, cfg_key in (("app_id", "feishu_app_id"),
                         ("app_secret", "feishu_app_secret"),
                         ("encrypt_key", "feishu_encrypt_key"),
                         ("verification_token", "feishu_verification_token")):
        value, differs = _apply_secret(data, key, str(cfg.get(cfg_key) or ""))
        if differs:
            cfg[cfg_key] = value
            changed.append(key)
    if changed:
        _save_headless(cfg)
    _log("飞书配置已保存：" + ("、".join(changed) if changed else "没有变化"))
    return {"ok": True, "changed": changed, "needs_restart": True}


def feishu_install_adapter_now() -> dict:
    """装飞书适配器（nonebot-adapter-feishu），最多等 _ADAPTER_WAIT_SECONDS 秒。

    bot.ensure_feishu_deps() 内部是 pip（几十秒起步），同步等会被 nginx 掐成 504，
    所以放后台线程：装完就回 ok:true；还没装完如实回 ok:false + running:true，
    安装继续跑（再点一次会接着看进度，不会又起一个 pip）。
    """
    s = _channel_settings()
    if _bot_python(s) is None:
        return {"ok": False, "adapter_installed": False, "running": False,
                "log": ["机器人运行时还没部署（缺 .venv）：先启动一次机器人再装适配器"],
                "error": "机器人运行时还没部署（缺 .venv），先启动一次机器人"}
    if _feishu_adapter_installed(s, refresh=True):
        return {"ok": True, "adapter_installed": True, "running": False,
                "log": ["飞书适配器已经装好了（重启机器人后生效）"],
                "needs_restart": True, "error": ""}

    with _adapter_lock:
        running = bool(_adapter_state["running"])
        if not running:
            _adapter_state.update(running=True, log=[], ok=None, error="", started_at=_now())
    if not running:
        threading.Thread(target=_feishu_install_worker, args=(s,), daemon=True,
                         name="feishu-adapter-install").start()

    deadline = _now() + _ADAPTER_WAIT_SECONDS
    while _now() < deadline:
        with _adapter_lock:
            if not _adapter_state["running"]:
                break
        time.sleep(0.4)

    with _adapter_lock:
        running = bool(_adapter_state["running"])
        lines = list(_adapter_state["log"])
        ok = _adapter_state["ok"]
        error = str(_adapter_state["error"] or "")
    if running:
        _log_adapter(f"还在装（已经等了 {_ADAPTER_WAIT_SECONDS} 秒），后台继续")
        with _adapter_lock:
            lines = list(_adapter_state["log"])
        return {"ok": False, "adapter_installed": False, "running": True, "log": lines,
                "error": "适配器还在装（pip 要联网，慢的时候一两分钟）：稍后再刷新状态看结果"}
    installed = _feishu_adapter_installed(s, refresh=True)
    if not ok and not error:
        error = "适配器没装上：多半是这台机器连不上 PyPI，或机器人 venv 里没有 pip"
    if not installed and ok:
        error = "pip 说装完了但校验没通过：看上面的日志，或在服务器上手跑一次 pip install nonebot-adapter-feishu"
    return {"ok": bool(ok and installed), "adapter_installed": installed, "running": False,
            "log": lines, "error": error, "needs_restart": True}


def _feishu_install_worker(settings) -> None:
    """后台装适配器：日志进 _adapter_state，结束写 ok/error（异常也不能把线程带崩）。"""
    ok, error = False, ""
    try:
        ok = bool(_bot_module().ensure_feishu_deps(settings, log=_log_adapter))
    except Exception as exc:  # noqa: BLE001
        error = f"装适配器失败：{exc}"
        _log_adapter(error)
    with _adapter_lock:
        _adapter_state.update(running=False, ok=bool(ok), error=error)
    try:
        _feishu_adapter_installed(settings, refresh=True)
    except Exception:  # noqa: BLE001 —— 只是刷缓存，失败无所谓
        pass


def qq_official() -> dict:
    """QQ 官方通道：凭证是否已填 + 收消息模式 + 见过的群 + 回话白名单。

    凭证读的是无头配置（控制台自己写的那份，deploy.build_settings 也读它）；
    模式/群名单是机器人侧插件写的状态与 AI 配置（ai_config），所以这里要 _settings()。
    """
    cfg = headless_config.load()
    ai_config = _ai_config_module()
    s = _channel_settings()
    try:
        state = ai_config.read_qq_group_state(s)
    except Exception as exc:  # noqa: BLE001 —— 读不到状态不该让凭证面板整个打不开
        _log(f"读取 QQ 群消息模式失败：{exc}")
        state = {}
    try:
        groups = list(ai_config.known_groups(s) or [])
    except Exception:  # noqa: BLE001
        groups = []
    try:
        allowed = list(ai_config.allowed_groups(s) or [])
    except Exception:  # noqa: BLE001
        allowed = []
    channel = str(cfg.get("qq_channel") or "onebot").strip() or "onebot"
    return {
        "ok": True,
        "enabled": channel != "onebot",      # 与 bot.build_env_text 的判据一致：不是 onebot 就走官方
        "channel": channel,
        "appid_set": bool(str(cfg.get("qq_app_id") or "").strip()),
        "secret_set": bool(str(cfg.get("qq_app_secret") or "").strip()),
        "token_set": bool(str(cfg.get("qq_app_token") or "").strip()),
        "mode": str(state.get("mode") or "none"),
        "mode_text": ai_config.qq_group_state_text(state),
        "mode_hint": ai_config.qq_group_state_hint(state),
        "last_ts": float(state.get("last_ts") or 0),
        "groups": groups,
        "allowed_groups": allowed,
    }


def qq_official_save(data: dict) -> dict:
    """写 QQ 官方凭证 / 通道 / 群白名单。

    - enabled：开关官方通道（false 就切回第三方 onebot，与桌面端通道下拉一个语义）；
    - mode：只接受 official / onebot（等价于 enabled）；收消息模式（全量 / 仅 @）是
      q.qq.com 那边的设置，控制台改不了 —— 传这种值直接给人话，不装成成功。
    - appid / secret / token：__clear__ 清空、空串不改（同飞书）。
    - allowed_groups：可选的群白名单（空数组 = 不限群），走 ai_config 那份机器人侧配置。
    """
    data = data or {}
    cfg = headless_config.load()
    changed = []
    # 收消息模式（全量 / 仅 @）改不动：它由 q.qq.com 的「接收所有消息」决定，是**只读状态**。
    # 传这种值不报错（前端整表提交时不会因此保存失败），但要如实回 mode_ignored + 怎么办。
    mode_ignored = False
    note = ""

    def set_channel(value: str, field: str) -> None:
        if str(cfg.get("qq_channel") or "") != value:
            cfg["qq_channel"] = value
            changed.append(field)

    if "enabled" in data and data.get("enabled") is not None:
        set_channel("official" if _as_bool(data.get("enabled")) else "onebot", "enabled")
    if "mode" in data and data.get("mode") is not None:
        mode = str(data.get("mode") or "").strip().lower()
        if mode in ("official", "onebot"):
            set_channel(mode, "mode")
        elif mode in ("full", "at_only", "none", "all", "at"):
            mode_ignored = True
            note = ("收消息模式（全量 / 仅 @）由 QQ 开放平台的「接收所有消息」决定，"
                    "控制台改不了：去 q.qq.com 打开它，再重启机器人。")
            _log("QQ 官方：有人在改收消息模式，已拒绝（要在 q.qq.com 改）")
        else:
            raise HTTPException(400, "mode 只能是 official（官方机器人）或 onebot（第三方协议端）")
    for key, cfg_key in (("appid", "qq_app_id"),
                         ("secret", "qq_app_secret"),
                         ("token", "qq_app_token")):
        value, differs = _apply_secret(data, key, str(cfg.get(cfg_key) or ""))
        if differs:
            cfg[cfg_key] = value
            changed.append(key)

    # 白名单先校验类型再落盘：不然后面 400 了，前面几个字段却已经写进去了（半套生效）
    groups = data.get("allowed_groups")
    if "allowed_groups" in data and groups is not None and not isinstance(groups, list):
        raise HTTPException(400, "allowed_groups 要是数组（空数组 = 不限群）")

    if changed:
        _save_headless(cfg)

    if isinstance(groups, list):
        try:
            _ai_config_module().save_allowed_groups(_channel_settings(), groups)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"保存群白名单失败：{exc}") from exc
        changed.append("allowed_groups")

    _log("QQ 官方配置已保存：" + ("、".join(changed) if changed else "没有变化"))
    return {"ok": True, "changed": changed, "needs_restart": True,
            "mode_ignored": mode_ignored, "note": note}


def _logs_keep_days() -> int:
    return _clamp_keep_days(headless_config.load().get("logs_keep_days"))


def _logs_usage(home: Path) -> dict:
    """日志目录占用（桌面端服务页显示的"现在占多少 MB / 几个文件"）。"""
    try:
        from qbotmanager.core import cleanup as cleanup_mod

        info = cleanup_mod.logs_usage(home) or {}
    except Exception as exc:  # noqa: BLE001 —— 统计不出来也不能让接口挂掉
        _log(f"统计日志占用失败：{exc}")
        info = {}
    return {"bytes": int(info.get("bytes") or 0),
            "files": int(info.get("files") or 0),
            "mb": float(info.get("mb") or 0.0)}


def _archived_logs(home: Path) -> list:
    """已归档的旧日志文件名（正在写的那两份不在里面；按名字排序方便前端直接列）。"""
    live = set(logs.LOG_FILES.values())
    try:
        from qbotmanager.core import cleanup as cleanup_mod

        is_archived = cleanup_mod._is_archived_log      # noqa: SLF001 —— 与桌面端同一套判据
    except Exception:  # noqa: BLE001
        is_archived = None
    names = []
    try:
        entries = sorted(Path(home).iterdir())
    except OSError:
        return []
    for path in entries:
        try:
            if not path.is_file() or path.name in live:
                continue
        except OSError:
            continue
        if is_archived is not None:
            try:
                if not is_archived(path.name):
                    continue
            except Exception:  # noqa: BLE001
                pass
        elif ".log" not in path.name.lower():
            continue
        names.append(path.name)
    return names


def logs_retention_state() -> dict:
    home = platform_info.log_home()
    return {"ok": True, "keep_days": _logs_keep_days(),
            "usage": _logs_usage(home), "archives": _archived_logs(home),
            "dir": str(home)}


def logs_retention_save(data: dict) -> dict:
    """改日志保留天数（1~365）。越界 / 非整数一律 400 说清楚，不静默改成别的值。"""
    data = data or {}
    if "keep_days" not in data or data.get("keep_days") is None:
        raise HTTPException(400, "必须提供 keep_days（1~365 的整数）")
    raw = data["keep_days"]
    if isinstance(raw, bool) or (isinstance(raw, float) and not float(raw).is_integer()):
        raise HTTPException(400, "keep_days 必须是 1~365 的整数")
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        raise HTTPException(400, "keep_days 必须是 1~365 的整数") from None
    if not _LOGS_KEEP_MIN <= days <= _LOGS_KEEP_MAX:
        raise HTTPException(
            400, f"keep_days 只能是 {_LOGS_KEEP_MIN}~{_LOGS_KEEP_MAX} 天（收到 {days}）")
    cfg = headless_config.load()
    cfg["logs_keep_days"] = days
    _save_headless(cfg)
    _log(f"日志保留天数已改为 {days} 天")
    # 机器人的清理线程读 settings.logs_keep_days，所以下次启动才用上新值（见 install_settings_bridge）
    return {"ok": True, "keep_days": days, "needs_restart": True}


def logs_cleanup_now() -> dict:
    """立刻归档跨天的日志 + 删掉超过保留天数的归档。

    与桌面端服务页的「立刻清理旧日志」同一个函数（cleanup.rotate_and_prune_logs）：
    正在写的那份永不删，被占用的（Windows 常见）进 skipped，下次再来。
    """
    home = platform_info.log_home()
    keep = _logs_keep_days()
    try:
        from qbotmanager.core import cleanup as cleanup_mod

        stats = cleanup_mod.rotate_and_prune_logs(home, keep) or {}
    except Exception as exc:  # noqa: BLE001 —— 回 ok:false + 原因，别 500
        _log(f"清理日志失败：{exc}")
        return {"ok": False, "error": f"清理日志失败：{exc}", "rotated": [], "deleted": [],
                "skipped": [], "bytes_freed": 0, "keep_days": keep, "needs_restart": False}
    rotated = [str(x) for x in (stats.get("rotated") or [])]
    deleted = [str(x) for x in (stats.get("deleted") or [])]
    freed = int(stats.get("bytes_freed") or 0)
    _log(f"日志清理：归档 {len(rotated)} 个、删除 {len(deleted)} 个，回收 {round(freed / 1048576, 2)} MB")
    # 这是立刻生效的动作、没改任何配置：不需要重启（桌面端同样不提示重启）
    return {"ok": True, "rotated": rotated, "deleted": deleted,
            "skipped": [str(x) for x in (stats.get("skipped") or [])],
            "bytes_freed": freed, "keep_days": keep, "needs_restart": False}


def selftest() -> dict:
    """不依赖 HTTP 的自检：路由能注册、依赖名提取、包名校验、开关读写都能跑。"""
    app = FastAPI()
    register(app)
    routes = sorted(r.path for r in app.routes if getattr(r, "path", "").startswith("/api/"))
    before = dev_mode()
    set_dev_mode(before)
    return {
        "routes": routes,
        "dep_name": [_dep_name(d) for d in ("nonebot2[fastapi]>=2.0.0", "PyJWT", "a; python_version<'3.11'")],
        "pkg_ok": [p for p in ("nonebot-plugin-status", "nonebot-plugin-x[fastapi]", "foo==1.2.3")
                   if valid_package(p)],
        "pkg_bad": [p for p in ("", "  ", "foo bar", "foo;rm -rf /", "a" * 200) if not valid_package(p)],
        "module": default_module("nonebot-plugin-status==1.0"),
        "dev_mode_roundtrip": dev_mode() == before,
        "plugins_dir": str(_settings().plugins_dir),
        # 人设工坊：id 白名单是**安全边界**（persona_workshop 拿 id 当目录名，自己不挡穿越）
        "persona_bad_ids": [p for p in ("../x", "..", ".", "/etc/passwd", "a/b", "a" * 80, "", "a..b")
                            if not valid_persona_id(p)],
        "persona_ok_ids": [p for p in ("com.demo.rose", "rose-1.0", "A_b.c") if valid_persona_id(p)],
        "personas_dir": str(_personas_root()),
        "json_dump_ok": bool(json.dumps(dict(_state), ensure_ascii=False)),
        # 凭证写规则：空串不改、__clear__ 清空（前端整表提交时靠它，谁也不回显密钥）
        "secret_rule": {
            "empty_keeps": _apply_secret({"k": ""}, "k", "old")[0],
            "missing_keeps": _apply_secret({}, "k", "old")[0],
            "clear_clears": _apply_secret({"k": _CLEAR_VALUE}, "k", "old")[0],
            "value_overwrites": _apply_secret({"k": " new "}, "k", "old")[0],
        },
        "settings_bridge": bool(getattr(deploy.build_settings, "_console_bridge", False)),
    }
