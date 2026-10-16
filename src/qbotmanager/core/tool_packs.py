# -*- coding: utf-8 -*-
"""桌面端能力包管理：下载安装/卸载工具包、人设包、行为包。

与 Linux 无头版 tools.py 对齐：包安装到 <根>/tool_packs/<id>，
manifest.json 声明 kind（tool-pack / persona-pack / behavior-pack）；
机器人启动时由 bot.start_bot 注入 ASTROSWARM_TOOL_PACKS 等环境变量，
AgentRuntime 加载全部已装能力包（2026-09 起插件全部免费，不再按权益冻结）。
"""
import json
import shutil
import zipfile
from pathlib import Path

from . import ai_config
from .agent.tool import compile_persona
from . import license as lic_mod
from . import plugin_market

PACK_KINDS = ("tool-pack", "persona-pack", "behavior-pack")


def packs_dir(settings) -> Path:
    return Path(settings.root) / "tool_packs"


def _find_manifest(pack_dir: Path):
    """返回包目录下的 manifest.json（支持 zip 顶层目录容错）。"""
    m = pack_dir / "manifest.json"
    if m.exists():
        return m
    for sub in pack_dir.iterdir():
        if sub.is_dir() and (sub / "manifest.json").exists():
            return sub / "manifest.json"
    return None


# 2026-09 起：能力包随主仓库开源，插件商店一律免费 —— 不再有付费包，
# 也没有「会员到期冻结」。FREE_PACK_IDS 保留为「全部官方包 id」，是为了让
# 无头端 deploy._allowed_packs 等旧调用方直接沿用（语义 = 全部放行）。
FREE_PACK_IDS = {
    "eva", "liqinghan", "qweather",
    "proactive", "memory", "knowledge", "web-search",
    "timer", "group-manager", "reply-rhythm", "session-chat", "music",
}
# 已无付费包；保留这个空集合，给外部脚本/旧代码一个稳定的判空入口
PAID_PACK_IDS: set = set()


def _entitled(settings, plugin_id: str) -> bool:
    """能不能装/加载该能力包 —— 插件已全部免费，恒为 True。"""
    return True


def installed(settings) -> list:
    """列出已装能力包（冻结标记为兼容保留，现在恒不冻结）。"""
    out = []
    root = packs_dir(settings)
    if not root.exists():
        return out
    for pack in sorted(root.iterdir()):
        manifest = _find_manifest(pack)
        if manifest is None:
            continue
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
            pid = m.get("id") or ""
            out.append({
                "id": pid,
                "name": m.get("name"),
                "version": m.get("version"),
                "kind": m.get("kind"),
                "permissions": m.get("permissions") or [],
                "tools": [t.get("name") for t in (m.get("tools") or [])],
                "persona": m.get("persona") or "",
                "frozen": not _entitled(settings, pid) if pid else True,
            })
        except Exception:  # noqa: BLE001
            continue
    return out


def allowed_pack_ids(settings) -> list:
    """当前账号可加载的已装能力包 id（能力包已全部免费，装了即加载）。"""
    return [
        p["id"] for p in installed(settings)
        if p.get("id") and not p.get("frozen")
    ]


def install_pack(settings, entry: dict, zip_path=None, log=None) -> dict:
    """下载并安装能力包（插件已全部免费，不再按权益拦截）。"""
    pid = str((entry or {}).get("id") or "").strip()
    if not pid:
        raise RuntimeError("能力包缺少 id")
    access = plugin_market.plugin_access(entry, lic_mod.entitlements())
    if not access.get("allowed"):
        raise PermissionError(access.get("reason") or "当前账号无权安装该能力包")
    ver = str(entry.get("version") or "1.0.0")
    dest = Path(zip_path) if zip_path else None
    if dest is None:
        dest_dir = Path(settings.downloads_dir) / "plugins"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{pid}-{ver}.zip"
        plugin_market.download_plugin(
            str(entry.get("url")), dest, entry.get("sha256"))
    if not dest.exists():
        raise RuntimeError("能力包压缩包不存在")
    root = packs_dir(settings)
    root.mkdir(parents=True, exist_ok=True)
    target = root / pid
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    try:
        with zipfile.ZipFile(dest) as archive:
            for name in archive.namelist():
                if name.startswith("/") or ".." in name.split("/"):
                    raise RuntimeError("能力包包含非法路径: " + name)
            archive.extractall(target)
        # 顶层目录容错：zip 内是 <pid>/manifest.json 时把内容上提
        manifest = _find_manifest(target)
        if manifest is not None and manifest.parent != target:
            sub = manifest.parent
            for f in list(sub.rglob("*")):
                rel = f.relative_to(sub)
                if f.is_dir():
                    (target / rel).mkdir(parents=True, exist_ok=True)
                else:
                    (target / rel).parent.mkdir(parents=True, exist_ok=True)
                    f.replace(target / rel)
            shutil.rmtree(sub)
        manifest = _find_manifest(target)
        if manifest is None:
            raise RuntimeError("能力包缺少 manifest.json 清单")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("kind") in ("persona-pack", "agent-pack"):
            persona_text = compile_persona(data)
            if persona_text:
                ai_config.save_personality(settings, persona_text)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    finally:
        if zip_path is None:
            dest.unlink(missing_ok=True)
    if log:
        log(f"能力包已安装: {pid} → {target}")
    return {
        "ok": True,
        "id": pid,
        "name": data.get("name") or pid,
        "kind": data.get("kind") or "",
        "dir": str(target),
    }


def uninstall_pack(settings, pack_id: str) -> dict:
    target = packs_dir(settings) / pack_id
    if target.exists():
        shutil.rmtree(target)
    return {"ok": True, "id": pack_id}


def pack_env(settings) -> dict:
    """机器人进程环境变量：能力包目录 + 允许加载列表 + 回复节奏行为文件。"""
    root = packs_dir(settings)
    allowed = allowed_pack_ids(settings)
    env = {
        "ASTROSWARM_TOOL_PACKS": str(root),
        "ASTROSWARM_TOOL_PACKS_ALLOWED": ",".join(allowed),
    }
    rhythm = root / "reply-rhythm" / "behavior.json"
    if rhythm.exists():
        env["ASTROSWARM_REPLY_RHYTHM"] = str(rhythm)
    # 主动聊天能力包：装了并允许加载后让 ai 插件导入它的实现
    if "proactive" in allowed:
        env["ASTROSWARM_PACK_PROACTIVE"] = "1"
    # 连续对话能力包：装了就让 ai 插件去读它的参数（免@连续对话的开关/模式/有效期）
    if "session-chat" in allowed:
        env["ASTROSWARM_PACK_SESSION_CHAT"] = "1"
    return env


# ---- 能力包详情与参数 ----
# schema 与无头端 console_ext.pack_detail / save_pack_config 完全一致：
#   manifest.config.fields = [{key, label, type(bool|number|enum/text), default, min, max, step, options, help}]
#   没有 fields 时按 manifest.behavior 的键自动造一份
# 用户改的值写在 <tool_packs>/<pid>/config.json，运行时缺项用默认值。
def _flatten(d) -> dict:
    """包里的 behavior/config 可能是 {"proactive": {...}} 这种单键包装，摊平一层。"""
    if isinstance(d, dict) and len(d) == 1:
        v = next(iter(d.values()))
        if isinstance(v, dict):
            return v
    return d if isinstance(d, dict) else {}


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


# ---- 参数中文名 / 说明 / 取值范围 ----
# 用户看到的是这一张表：英文 key 只当内部标识（排查时用）。
# 表里没有的 key 就原样显示原名（不影响功能，加一行就能补上）。
# 字段含义：label=中文名, help=一句人话, unit=单位, type=控件类型(range=区间),
#           min/max/step=允许范围（号码类）, hint=额外提示
PACK_PARAM_LABELS: dict = {
    # 回复节奏能力包（manifest 里只有 behavior，没有中文名，界面上原来是一串英文）
    "busy_long_probability": {
        "label": "「忙很久」的出现概率", "help": "0~1 之间的小数，越大越常出现隔几分钟才回的情况。",
        "type": "number", "min": 0, "max": 1, "step": 0.01,
    },
    "busy_long_seconds": {
        "label": "「忙很久」的回复间隔", "unit": "秒",
        "help": "填一个区间（最小~最大），例如 180~900：这类回复会在 3 到 15 分钟之间随机挑一个时间回。",
        "type": "range", "min": 0, "max": 3600, "step": 1,
    },
    "busy_short_seconds": {
        "label": "「忙一小会儿」的回复间隔", "unit": "秒",
        "help": "区间，例如 8~40：偶尔慢半拍再回，别显得秒回很机械。",
        "type": "range", "min": 0, "max": 3600, "step": 1,
    },
    "idle_probability": {
        "label": "「空闲慢半拍」的出现概率", "help": "0~1 之间的小数，越大越常出现下面那个空闲间隔。",
        "type": "number", "min": 0, "max": 1, "step": 0.01,
    },
    "idle_seconds": {
        "label": "空闲时的回复间隔", "unit": "秒",
        "help": "区间，例如 60~180：闲着的时候隔 1 到 3 分钟再回。",
        "type": "range", "min": 0, "max": 3600, "step": 1,
    },
}


# 行为块的中文名（详情页「功能」列表用；没有的块回落显示原名）
PACK_BEHAVIOR_LABELS = {
    "reply-rhythm": "回复节奏",
    "session-chat": "连续对话（免 @ 也能接着聊）",
}

# 连续对话能力包的参数中文名（manifest 里已经写了中文 label，这里做兜底，
# 万一哪天 manifest 只留了英文 key，界面也不会变成一串英文）
PACK_PARAM_LABELS.update({
    "enabled": {"label": "开启免 @ 连续对话", "type": "bool",
                "help": "关掉后：群里只有 @ 它才回，其余消息一律不理。"},
    "mode": {"label": "怎么判断该不该接话", "type": "enum",
             "options": [
                 {"value": "timeout", "label": "超时断开（推荐：说一会儿自动停）"},
                 {"value": "api", "label": "读群聊天记录判断（每条都问一次 AI，费 token）"},
             ]},
    "expire_seconds": {"label": "连续对话有效期", "unit": "秒", "type": "number",
                       "min": 30, "max": 300, "step": 10},
    "unrelated_limit": {"label": "连着几句无关就停", "unit": "句", "type": "number",
                        "min": 1, "max": 20, "step": 1},
})


def param_label(key: str) -> dict:
    """取一个参数的中文名/说明/范围（没有就返回空 dict，界面兜底显示原名）。"""
    return dict(PACK_PARAM_LABELS.get(str(key or "")) or {})


def _looks_like_range(value) -> bool:
    """默认值是 [最小, 最大] 这种两元列表 → 区间参数。"""
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value))


def decorate_field(field: dict) -> dict:
    """给一个参数字段补上中文名/说明/范围；已经写好的（例如包里自带中文 label）不动。

    规则：
      · label 缺失、或者 label 就是英文 key → 用映射表里的中文名；
      · help/unit/min/max/step 缺就补，绝不覆盖包里显式写好的值；
      · type 缺失时：默认值是两元列表 → range，否则按 number/text。
    """
    out = dict(field or {})
    key = str(out.get("key") or "")
    meta = param_label(key)
    label = str(out.get("label") or "").strip()
    if not label or label == key:
        out["label"] = str(meta.get("label") or label or key)
    for name in ("help", "unit", "min", "max", "step"):
        if out.get(name) in (None, "") and meta.get(name) not in (None, ""):
            out[name] = meta[name]
    ftype = str(out.get("type") or "").strip()
    if not ftype:
        if _looks_like_range(out.get("default")):
            ftype = "range"
        elif isinstance(out.get("default"), bool):
            ftype = "bool"
        elif isinstance(out.get("default"), (int, float)):
            ftype = "number"
        else:
            ftype = "text"
    if ftype == "number" and _looks_like_range(out.get("default")):
        ftype = "range"                      # 值本身是区间 → 按区间编辑
    if ftype == "number" and str(meta.get("type") or "") == "range":
        ftype = "range"
    out["type"] = ftype
    return out


def split_range(raw, field: dict | None = None) -> tuple:
    """把区间值拆成 (最小, 最大)。

    认这几种写法：[80, 900] / "80~900" / "80-900" / "80,900" / (80, 900)；
    认不出来时回落到字段的 min/max（再不行给 0/0），保证界面不会崩。
    """
    f = field or {}
    lo_fallback = f.get("min")
    hi_fallback = f.get("max")
    nums = []
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        nums = list(raw[:2])
    elif isinstance(raw, str):
        text = raw.replace("，", ",").replace("—", "-").replace("－", "-")
        for sep in ("~", "-", ",", " "):
            if sep in text:
                nums = [p for p in text.replace("-", "~").split("~") if p.strip()] if sep == "-" else \
                       [p for p in text.split(sep) if p.strip()]
                break
    out = []
    for idx in range(2):
        value = None
        if idx < len(nums):
            try:
                value = float(str(nums[idx]).strip())
            except (TypeError, ValueError):
                value = None
        if value is None:
            fallback = lo_fallback if idx == 0 else hi_fallback
            value = float(fallback) if isinstance(fallback, (int, float)) else 0.0
        out.append(value)
    return (out[0], out[1])


def range_hint(field: dict) -> str:
    """"可填范围 60–900" 这种提示；没有范围就返回空串。"""
    f = field or {}
    lo, hi = f.get("min"), f.get("max")
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
        return ""
    def fmt(v):
        return str(int(v)) if float(v) == int(v) else str(v)
    return f"可填范围 {fmt(lo)}–{fmt(hi)}"



def pack_fields(manifest: dict) -> list:
    """参数 schema：manifest.config.fields（没有就按 behavior 的键造一份）。

    两种情况都会过一遍 `decorate_field`：包里写了中文名就尊重它，
    只写了英文 key（回复节奏那个包就是）就用映射表翻成中文 + 说明 + 范围。
    默认值是 [最小, 最大] 的自动认成区间参数（range）。
    """
    fields = ((manifest or {}).get("config") or {}).get("fields") or []
    if fields:
        return [decorate_field(f) for f in fields if isinstance(f, dict)]
    out = []
    for _, block in ((manifest or {}).get("behavior") or {}).items():
        if isinstance(block, dict):
            for key, value in block.items():
                if isinstance(value, bool):
                    kind = "bool"
                elif isinstance(value, (int, float)):
                    kind = "number"
                elif _looks_like_range(value):
                    kind = "range"
                else:
                    kind = "text"
                out.append(decorate_field(
                    {"key": key, "label": key, "type": kind, "default": value}))
    return out


def pack_features(manifest: dict) -> list:
    """详情页的「功能」列表：从 tools / behavior / schedule 派生。"""
    manifest = manifest or {}
    feats = []
    for tool in manifest.get("tools") or []:
        if isinstance(tool, dict):
            feats.append({"title": str(tool.get("name") or ""),
                          "desc": str(tool.get("description") or "")})
    for name, block in (manifest.get("behavior") or {}).items():
        if isinstance(block, dict):
            # 「功能」这一行人也要说中文：以前直接列英文参数名，等于换个地方再糊一次脸
            names = [str(param_label(k).get("label") or k) for k in block.keys()]
            feats.append({"title": PACK_BEHAVIOR_LABELS.get(str(name), str(name)),
                          "desc": "可调参数：" + "、".join(names)})
    for name, block in (manifest.get("schedule") or {}).items():
        if isinstance(block, dict):
            feats.append({"title": f"计划任务 · {name}",
                          "desc": "、".join(f"{k}={v}" for k, v in block.items())})
    return [f for f in feats if f["title"]]


def pack_config_path(settings, pack_id: str) -> Path:
    return packs_dir(settings) / str(pack_id) / "config.json"


def pack_detail(settings, pack_id: str) -> dict:
    """某个能力包的详情（已装就读 manifest + config 与 behavior 的默认值）。"""
    pid = str(pack_id or "").strip()
    if not pid or "/" in pid or "\\" in pid or ".." in pid:
        raise ValueError("插件标识不合法")
    d = packs_dir(settings) / pid
    manifest = _read_json(d / "manifest.json")
    installed = bool(manifest)
    cfg = _flatten(_read_json(d / "config.json"))
    behavior = _flatten(_read_json(d / "behavior.json"))
    fields = pack_fields(manifest)
    defaults = {str(f["key"]): f["default"] for f in fields if "default" in f}
    for key, value in (behavior or {}).items():
        defaults.setdefault(key, value)
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
        "features": pack_features(manifest) if installed else [],
        "fields": fields,
        "defaults": defaults,
        "values": {**defaults, **cfg},
        "has_user_config": bool(cfg),
        "dir": str(d),
    }


def coerce_field(field: dict, raw):
    """按 schema 校验/转换一个值；不合法抛 ValueError（与无头端 _coerce 同规则）。"""
    key = str((field or {}).get("key") or "")
    ftype = str((field or {}).get("type") or "text")
    label = str((field or {}).get("label") or key)
    if ftype == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return bool(raw)
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "on", "yes", "是")
        raise ValueError(f"{label}：要是开关值")
    if ftype == "number":
        try:
            val = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{label}：要是数字") from None
        lo, hi = field.get("min"), field.get("max")
        if lo is not None and val < float(lo):
            raise ValueError(f"{label}：不能小于 {lo}")
        if hi is not None and val > float(hi):
            raise ValueError(f"{label}：不能大于 {hi}")
        if val == int(val) and float(field.get("step") or 1) >= 1:
            return int(val)
        return val
    if ftype in ("range", "range_pair"):
        lo, hi = split_range(raw, field)
        lo_bound, hi_bound = field.get("min"), field.get("max")
        for name, val in (("最小", lo), ("最大", hi)):
            if lo_bound is not None and val < float(lo_bound):
                raise ValueError(f"{label}：{name}不能小于 {lo_bound}")
            if hi_bound is not None and val > float(hi_bound):
                raise ValueError(f"{label}：{name}不能大于 {hi_bound}")
        if lo > hi:
            raise ValueError(f"{label}：最小不能大于最大（现在填的是 {lo:g} 和 {hi:g}）")
        integral = float(field.get("step") or 1) >= 1
        return [int(lo), int(hi)] if integral else [lo, hi]
    if ftype in ("enum", "select"):
        options = field.get("options") or []
        vals = [o.get("value") if isinstance(o, dict) else o for o in options]
        if vals and raw not in vals:
            raise ValueError(f"{label}：只能是 {'/'.join(str(v) for v in vals)}")
        return raw
    return str(raw if raw is not None else "")


def save_pack_config(settings, pack_id: str, patch: dict) -> dict:
    """把用户改的参数写进 <tool_packs>/<pid>/config.json（只认 schema 里声明过的键）。"""
    detail = pack_detail(settings, pack_id)
    if not detail["installed"]:
        raise RuntimeError("这个能力包还没安装，装完才能改参数")
    if not isinstance(patch, dict) or not patch:
        raise ValueError("没有要改的参数")
    by_key = {str(f.get("key")): f for f in detail["fields"]}
    path = pack_config_path(settings, detail["id"])
    cur = _flatten(_read_json(path))
    changed = {}
    for key, raw in patch.items():
        field = by_key.get(str(key))
        if field is None:
            continue                      # 没在 schema 里的键直接忽略
        val = coerce_field(field, raw)
        if cur.get(key) != val:
            changed[str(key)] = val
        cur[str(key)] = val
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return {"ok": True, "id": detail["id"], "changed": changed,
            "values": cur, "path": str(path)}


# ---- 和风天气工具包配置（QWEATHER_* 环境变量注入）----
QW_CONFIG_NAME = "qweather.json"


def qweather_config_path(settings) -> Path:
    return Path(settings.root) / QW_CONFIG_NAME


def load_qweather_config(settings) -> dict:
    """读取 <根>/qweather.json；不存在/损坏返回空 dict。"""
    p = qweather_config_path(settings)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return {
        k: str(data.get(k) or "")
        for k in ("project_id", "credential_id", "private_key_path", "api_host")
    }


def save_qweather_config(settings, project_id="", credential_id="",
                         private_key_path="", api_host="") -> dict:
    """保存和风天气配置（项目ID/凭据ID/私钥路径/API Host）。"""
    data = {
        "project_id": str(project_id or "").strip(),
        "credential_id": str(credential_id or "").strip(),
        "private_key_path": str(private_key_path or "").strip(),
        "api_host": str(api_host or "").strip(),
    }
    qweather_config_path(settings).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


def qweather_env(settings) -> dict:
    """把 qweather.json 配置映射成机器人进程的 QWEATHER_* 环境变量。"""
    data = load_qweather_config(settings)
    env = {}
    if data.get("project_id"):
        env["QWEATHER_PROJECT_ID"] = data["project_id"]
    if data.get("credential_id"):
        env["QWEATHER_CREDENTIAL_ID"] = data["credential_id"]
    if data.get("private_key_path"):
        env["QWEATHER_PRIVATE_KEY_PATH"] = data["private_key_path"]
    if data.get("api_host"):
        env["QWEATHER_API_HOST"] = data["api_host"]
    return env
