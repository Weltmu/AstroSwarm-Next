"""无头配置：JSON 存取（config_home/config.json）。"""
import json
import logging
import os
import shutil
import time
from pathlib import Path

from . import platform_info

DEFAULTS = {
    "port": 7860,
    "bot_port": 12113,
    "qq_app_id": "",
    "qq_app_secret": "",
    "qq_app_token": "",
    # 第三方 OneBot 协议端（NapCat / LLOneBot 等）：默认反向 WS，协议端来连我们
    "qq_channel": "onebot",
    "qq_onebot_mode": "reverse",
    "qq_onebot_host": "127.0.0.1",
    "qq_onebot_port": 0,
    "qq_onebot_path": "/onebot/v11/ws",
    "qq_onebot_token": "",
    "qq_onebot_listen_host": "127.0.0.1",
    # 飞书通道（2026-10-04 加）：以前这几个键没进配置表，控制台填了也进不了机器人 .env，
    # 表现就是"界面上填好了、机器人那边当没有"。
    # 界面：小白模式（与桌面端 settings.beginner_mode 同义，默认开）
    # 2026-10-04 加：控制台原来把它存在浏览器 localStorage，不同浏览器/不同设备不一致，
    # 桌面端存在服务器配置里，两边永远可能对不上。
    "beginner_mode": True,
    "feishu_enabled": False,
    "feishu_app_id": "",
    "feishu_app_secret": "",
    "feishu_encrypt_key": "",
    "feishu_verification_token": "",
    # 智能体档案（李清菡等）
    "agent_profile_enabled": False,
    "agent_profile_id": "star_helper",
    # 李清菡档案接管 QQ 后，只回 owner_qq 的私聊 —— 不填就「机器人收到消息但一声不吭」
    "agent_owner_qq": "",
    "agent_groups": "",
    "agent_address_words": "",
    "agent_persona_mode": "",
    "ai_provider": "deepseek",
    "ai_api_key": "",
    "ai_base_url": "https://api.deepseek.com",
    "ai_model": "deepseek-v4-flash",
    # 服务商模型列表缓存 {接口地址: [模型名]}：填完密钥自动拉取后写入，
    # 下次拉不到时下拉里仍能选到上一批（与桌面端 ai_models_cache 同义）。
    "ai_models_cache": {},
    # 与桌面端对齐：平台开关 / 视觉模型 / 唤醒词。
    # 这几个键不在白名单里时，控制台改了会被静默丢弃，表现就是「机器人一声不吭」。
    "ai_platforms": {"qq": True, "wechat": True, "feishu": True, "telegram": True},
    "vision_api_url": "",
    "vision_api_key": "",
    "vision_model": "",
    "agent_wake_words": "",
    "agent_wake_auto": True,
    # 本地人设工坊：当前启用的人设 id（空 = 没启用）。无头端从控制台启用/停用时写这里，
    # deploy.build_settings 读它 → 机器人进程拿到 ASTROSWARM_PERSONAS_ALLOWED。
    "local_persona_id": "",
    # 星群账号服务地址（登录 / 认领 / 兑换 / 拉权益都走它）。
    # **留空 = 用官方托管服务**（https://astroswarm.cn/api/account，见 auth.account_base）。
    #
    # 必须是空串，不能写死官方地址：DEFAULTS 里的值会被当成「配置里有值」，
    #   auth.account_base() 就会压住环境变量 ASTROSWARM_ACCOUNT_BASE 和 systemd 的
    #   Environment=。留空才能让「配置 > 环境变量 > 官方默认」这条优先级成立。
    "account_base": "",
    # 档位表（plans.json）位置：留空 = 用包内自带的 astroswarm_linux/plans.json。
    # 自建账号服务时指向服务端那份档位定义文件，两侧的「全解锁」口径就一致。
    # 故意不放进 SAFE_KEYS：这是运维在配置文件/环境变量里设的，不给控制台 API 改。
    "plans_file": "",
    "account_email": "",
    "account_token": "",
    "plan": "none",
    "plan_expires_at": 0,
    "owned_plugins": [],
    "entitlement_sig": "",
    "entitlement_machine": "",
}

SAFE_KEYS = {
    "port": int,
    "bot_port": int,
    "beginner_mode": bool,
    "qq_app_id": str,
    "qq_app_secret": str,
    "qq_app_token": str,
    "qq_channel": str,
    "qq_onebot_mode": str,
    "qq_onebot_host": str,
    "qq_onebot_port": int,
    "qq_onebot_path": str,
    "qq_onebot_token": str,
    "qq_onebot_listen_host": str,
    "ai_provider": str,
    "ai_api_key": str,
    "ai_base_url": str,
    "ai_model": str,
    "ai_models_cache": dict,
    # 智能体档案：不在白名单里时控制台改了 owner QQ / 群号会被静默丢弃，
    # 表现就是「机器人收到消息却一声不吭」。
    "agent_profile_enabled": bool,
    "agent_profile_id": str,
    "agent_owner_qq": str,
    "agent_groups": str,
    "agent_address_words": str,
    "agent_persona_mode": str,
    "ai_platforms": dict,
    "vision_api_url": str,
    "vision_api_key": str,
    "vision_model": str,
    "agent_wake_words": str,
    "agent_wake_auto": bool,
    "local_persona_id": str,
    "account_base": str,
    # ── 界面偏好（ui_* 前缀）─────────────────────────────────────────────
    # 2026-10-07：控制台原来把「外观」整份存在浏览器 localStorage（as_appearance），
    # 换浏览器/换设备/清缓存就全丢，老板看到的是"配置归 0"。
    # 这里只收「配置 / 偏好」类，且**都带 ui_ 前缀**：
    #   · 前缀是为了绝不与桌面端 settings.json 的通用键（theme / accent / …）撞名 ——
    #     撞名会让无头端写下的控制台外观去改桌面端的界面；
    #   · 草稿（插件工坊正在编辑的内容、工坊 job_id）故意不收：草稿留在本地是对的。
    # 值域校验（类型 / 长度 / 数值范围 / 枚举）在 validate_ui_prefs() 里，
    # 读和写两条路径都过一遍，手改 config.json 写坏也不会把前端搞崩。
    "ui_bg_enabled": bool,
    "ui_bg_path": str,
    "ui_bg_mask": int,
    "ui_bg_opacity": int,
    "ui_bg_bright": int,
    "ui_bg_autoplay": bool,
    "ui_bg_loop": bool,
    "ui_animations": bool,
    "ui_theme": str,
    "ui_accent": str,
    "ui_glass": str,
}

# ── 界面偏好白名单：类型 + 值域（见上面 ui_* 的说明）──────────────────────
# 数值统一用「夹取」而不是拒绝：滑杆拖动时打到边界是正常操作，
# 回 400 会让用户以为设置坏了。字符串超长直接截断，枚举不认识就回落默认。
UI_PREF_SETS = {
    "ui_theme": {"极光玻璃", "瑞士极简", "深空终端"},
    "ui_accent": {"深邃蓝", "暗夜紫", "赛博青", "暖阳橙", "薄荷绿", "深空绿（新色板）"},
    "ui_glass": {"默认（原样）", "液态玻璃", "颗粒磨砂"},
}
UI_PREF_RANGES = {
    "ui_bg_mask": (0, 100),
    "ui_bg_opacity": (10, 95),
    "ui_bg_bright": (-100, 100),
}
UI_PREF_DEFAULTS = {
    "ui_bg_enabled": False,
    "ui_bg_path": "",
    "ui_bg_mask": 68,
    "ui_bg_opacity": 70,
    "ui_bg_bright": 0,
    "ui_bg_autoplay": False,
    "ui_bg_loop": False,
    "ui_animations": True,
    "ui_theme": "极光玻璃",
    "ui_accent": "深邃蓝",
    "ui_glass": "默认（原样）",
}
UI_PREF_MAXLEN = {"ui_bg_path": 1024}
# 允许写进配置的界面偏好键（= SAFE_KEYS 里 ui_ 开头那批，单独列出来给校验用）
UI_PREF_KEYS = tuple(UI_PREF_DEFAULTS)


def validate_ui_prefs(cfg: dict) -> dict:
    """就地校验/夹取配置里的 ui_* 键，返回改动过的键名列表。

    读（load）与写（PUT /api/config）两条路径都要过：
    写的时候挡住越界值，读的时候兜住被手工改坏的 config.json。
    """
    changed = []
    for key in UI_PREF_KEYS:
        if key not in cfg:
            continue                     # 没设过就不写默认值：留空 = 前端用自带默认
        raw = cfg[key]
        want = type(UI_PREF_DEFAULTS[key])
        if want is bool:
            if not isinstance(raw, bool):
                cfg[key] = UI_PREF_DEFAULTS[key]
                changed.append(key)
            continue
        if want is int:
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                cfg[key] = UI_PREF_DEFAULTS[key]
                changed.append(key)
                continue
            lo, hi = UI_PREF_RANGES[key]
            val = int(raw)
            clamped = min(hi, max(lo, val))
            if clamped != raw:
                cfg[key] = clamped
                changed.append(key)
            continue
        # str
        if not isinstance(raw, str):
            cfg[key] = UI_PREF_DEFAULTS[key]
            changed.append(key)
            continue
        allowed = UI_PREF_SETS.get(key)
        if allowed is not None and raw not in allowed:
            cfg[key] = UI_PREF_DEFAULTS[key]
            changed.append(key)
            continue
        limit = UI_PREF_MAXLEN.get(key)
        if limit and len(raw) > limit:
            cfg[key] = raw[:limit]
            changed.append(key)
    return changed


def ui_pref_value(key: str, value):
    """PUT /api/config 用：按 UI_PREF_* 规则把单个 ui_* 键的入参转成落库值。

    与 validate_ui_prefs() 共用同一套值域表，避免写入口和读入口两套规则。
    类型不对抛 ValueError（→ 接口回 400，前端能看见"格式不正确"）；
    数值越界/文案超长/枚举不认识都按规则**夹取或回落**，不算错 ——
    滑杆拖到边界、主题名以后改了，都不该让保存失败。
    """
    if key not in UI_PREF_DEFAULTS:
        raise ValueError("未知的界面偏好键：%s" % key)
    want = type(UI_PREF_DEFAULTS[key])
    if want is bool:
        if not isinstance(value, bool):
            raise ValueError("%s 需要 true/false" % key)
        return value
    if want is int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("%s 需要数字" % key)
        if value != value or value in (float("inf"), float("-inf")):   # NaN / inf
            raise ValueError("%s 数值不合法" % key)
        lo, hi = UI_PREF_RANGES[key]
        return min(hi, max(lo, int(value)))
    if not isinstance(value, str):
        raise ValueError("%s 需要文本" % key)
    allowed = UI_PREF_SETS.get(key)
    if allowed is not None and value not in allowed:
        raise ValueError("%s 不是可选的取值" % key)
    limit = UI_PREF_MAXLEN.get(key)
    if limit and len(value) > limit:
        raise ValueError("%s 太长了（最多 %d 字）" % (key, limit))
    return value


def config_path() -> Path:
    return platform_info.config_home() / "config.json"


def last_good_path() -> Path:
    return config_path().with_name(config_path().name + ".bak")


def _salvage(raw: str) -> dict:
    """从坏配置文本里逐字段抢救：对每个默认键找最后一个 `"键": <值>` 再 raw_decode。

    为什么要抢救：文件被截断/写到一半时前半段往往还是完好的，整份丢成默认值等于把
    用户登录态（account_token）一起删了 —— 而那是重新进控制台的**唯一凭据**，
    丢了就只能上服务器手改文件（见 load()）。类型对不上默认值的字段一律不要。
    """
    out = {}
    if not raw:
        return out
    decoder = json.JSONDecoder()
    for key, default in DEFAULTS.items():
        marker = '"%s"' % key
        idx = raw.rfind(marker)
        if idx < 0:
            continue
        pos = idx + len(marker)
        while pos < len(raw) and raw[pos] in " \t\r\n":
            pos += 1
        if pos >= len(raw) or raw[pos] != ":":
            continue
        pos += 1
        while pos < len(raw) and raw[pos] in " \t\r\n":
            pos += 1
        try:
            value, _ = decoder.raw_decode(raw, pos)
        except Exception:  # noqa: BLE001 —— 这个字段也坏了，跳过继续救下一个
            continue
        if isinstance(default, bool) and not isinstance(value, bool):
            continue
        if isinstance(default, int) and not isinstance(default, bool) and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            continue
        if isinstance(default, str) and not isinstance(value, str):
            continue
        if isinstance(default, (list, dict)) and not isinstance(value, type(default)):
            continue
        out[key] = value
    return out


def load() -> dict:
    """读配置；解析失败时**不再静默吞掉**。

    except 后直接返回默认值是不行的：紧接着一次 save() 就会把默认值写回文件，
    用户的登录态 / 通道权益 / 已登记插件会无声消失。
    所以坏文件另存一份 .corrupt.<时间>，指出上一份好配置（.bak）在哪，写 error 日志，
    并在返回值里带上 _config_error（save() 不会把下划线字段写回文件）。
    """
    data = dict(DEFAULTS)
    path = config_path()
    if not path.exists():
        return data
    raw = ""
    try:
        raw = path.read_text(encoding="utf-8")
        loaded = json.loads(raw)
        if not isinstance(loaded, dict):
            raise ValueError("config.json 顶层不是 JSON 对象")
        data.update(loaded)
        # 界面偏好单独过一遍值域：这份文件可能被人手工改过，
        # 越界/枚举不认识的值不该直接灌进前端。
        validate_ui_prefs(data)
        return data
    except Exception as exc:  # noqa: BLE001
        bak = path.with_name(path.name + ".corrupt." + time.strftime("%Y%m%d-%H%M%S"))
        try:
            shutil.copy(path, bak)
            bak_txt = str(bak)
        except Exception:  # noqa: BLE001
            bak_txt = ""
        good = last_good_path()
        good_ok = False
        if good.exists():
            try:
                good_ok = isinstance(json.loads(good.read_text(encoding="utf-8")), dict)
            except Exception:  # noqa: BLE001
                good_ok = False
        # 抢救坏文件里还能读出来的字段：整份退回默认值会让 account_token 变空，
        # 控制台连原来正确的 token 也 401。
        rescued = _salvage(raw)
        data.update(rescued)
        logging.getLogger("astroswarm").error(
            "config.json 解析失败（%s）；坏文件已备份到 %s。已从坏文件里抢救回 %d 个字段%s；"
            "上一份好配置：%s。其余字段按默认值运行，请尽快在控制台重新保存一次配置。",
            exc, bak_txt or "<备份失败>", len(rescued),
            ("（含 account_token，登录态保住了）" if rescued.get("account_token") else ""),
            good if good_ok else "<无>",
        )
        data["_config_error"] = "%s: %s" % (type(exc).__name__, exc)
        data["_config_backup"] = bak_txt
        data["_config_last_good"] = str(good) if good_ok else ""
        data["_config_rescued"] = sorted(rescued)
        return data


def restore_last_good() -> dict:
    """把上一份好配置（config.json.bak）恢复回来；没有就返回 {ok: False}。"""
    good = last_good_path()
    path = config_path()
    if not good.exists():
        return {"ok": False, "error": "没有可回滚的备份"}
    try:
        data = json.loads(good.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("备份不是 JSON 对象")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "备份也坏了：%s" % exc}
    if path.exists():
        shutil.copy(path, path.with_name(path.name + ".broken."
                                        + time.strftime("%Y%m%d-%H%M%S")))
    save(data)
    return {"ok": True, "restored": True, "plan": data.get("plan")}


def save(cfg: dict) -> dict:
    """原子写：临时文件 + fsync + os.replace，避免半个文件把配置写坏。

    不能直接 write_text 覆盖：写到一半断电/进程被杀就是一个截断的 JSON，再 load 一次
    就成了默认值。每次覆盖前把当前这份能正常解析的配置留一份到 config.json.bak，供回滚。
    """
    merged = {
        **DEFAULTS,
        **{k: v for k, v in cfg.items() if not str(k).startswith("_")},
    }
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if isinstance(json.loads(path.read_text(encoding="utf-8")), dict):
                shutil.copy(path, last_good_path())
        except Exception:  # noqa: BLE001
            pass                     # 旧文件本来就坏，不用留
    elif not last_good_path().exists():
        # 第一次保存（新装机器）也先落一份默认值当回滚目标，否则这次写坏就没有副本可退。
        try:
            last_good_path().write_text(
                json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:  # noqa: BLE001 —— 备份写不出来不该挡住本次保存
            pass
    tmp = path.with_name(path.name + ".tmp")
    payload = json.dumps(merged, ensure_ascii=False, indent=2)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    try:                       # 目录项也落盘，掉电才不会丢
        dfd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except Exception:  # noqa: BLE001
        pass
    return merged
