"""李清菡 Agent 配置加载（环境变量优先 + .env 回退，不引入额外依赖）。"""
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo


def _bot_id_from_env(raw):
    """从 .env 的 QQ_BOTS 里取机器人自己的 id（AppID）。

    应用写 .env 时会带 QQ_BOTS（官方通道），但没写 BOT_QQ —— 不兜这一下，
    bot_qq 会一直是默认值 "astrobot"，跟真实 id 永远对不上。
    """
    try:
        bots = json.loads(raw.get("QQ_BOTS") or "[]")
        if bots and isinstance(bots[0], dict):
            return str(bots[0].get("id") or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _load_env(path):
    cfg = {}
    if not path:
        return cfg
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return cfg


def _val(env_names, raw, key, default=""):
    for name in env_names:
        if os.environ.get(name):
            return os.environ[name]
    return raw.get(key, default)


def _val_prefixed(env_names, raw, key, default=""):
    """取值顺序：进程环境变量 → .env 里的**带前缀**键名 → .env 里的裸键名 → 默认值。

    为什么要认带前缀的键名（2026-10-11 修的真 bug）：
    程序侧 `core.bot.write_env()` 往机器人 `.env` 写的是
    ``QBM_AGENT_OWNER_QQ=…`` / ``QBM_AGENT_ADDRESS_WORDS=…``，
    而这里原来只查 ``raw.get("OWNER_QQ")`` —— **两个名字对不上**。
    结果是"只写 .env（不设进程环境变量）"时，主人 QQ / 唤醒词**从来没生效过**：
    界面里改了、.env 里也写了，机器人侧读到的还是空 / 内置默认值。
    现在两种键名都认，**带前缀的优先**（那是程序侧真正写的那个）。
    """
    for name in env_names:
        value = os.environ.get(name)
        if value:
            return value
    for name in env_names:
        value = raw.get(name)
        if value:
            return value
    return raw.get(key, default)


class Config:
    def __init__(self, path=None):
        self._env_path = path
        self._env_key = None
        # 运行期自动学习到的群（被 @ 即加入）：与"配置里写的"分开记，
        # 这样配置里删掉一个群是**立即生效**的，而学到的群不会被热重载抹掉。
        self._learned_groups = []
        raw = _load_env(path)
        self._apply_env(raw)

    # ------------------------------------------------------------ 热读（2026-10-11）
    # 主人 QQ / 群白名单 / 唤醒词（称呼）以前只在启动时读一次 .env，
    # 在界面上改完必须重启机器人。现在每条消息前 `maybe_reload()` 一次：
    # 文件指纹（mtime+size）变了才重新解析，代价就是一次 os.stat。
    HOT_KEYS = ("OWNER_QQ", "GROUPS", "ADDRESS_WORDS", "PERSONA_NAME",
                "PERSONA_MODE", "PERSONA_EXTRA", "BOT_QQ", "QBM_AGENT_OWNER_QQ",
                "QBM_AGENT_GROUPS", "QBM_AGENT_ADDRESS_WORDS",
                "QBM_AGENT_PERSONA_EXTRA")

    @staticmethod
    def _env_fingerprint(path):
        """`.env` 的指纹（mtime_ns + 大小）；读不到返回 None。

        注意别把实例属性也起名 `_env_key` —— 那会把这个方法**盖掉**
        （2026-10-11 真踩过：`self._env_key = key` 之后 `self._env_key(path)`
        变成 'NoneType' object is not callable，热重载直接炸）。
        """
        try:
            st = os.stat(path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def maybe_reload(self) -> bool:
        """主人 QQ / 群白名单 / 唤醒词改了就重新读（改完立即生效，不用重启）。

        返回是否真的重载了。注意：这里**只刷新设置类字段**，
        运行期状态（记忆库、会话、冷却、已学习群组）一个都不动。
        """
        if not self._env_path:
            return False
        key = self._env_fingerprint(self._env_path)
        if key is None or key == self._env_key:
            return False
        try:
            raw = _load_env(self._env_path)
        except Exception:  # noqa: BLE001 —— 文件被写到一半/读不动：保留旧值
            return False
        # 解析成功才更新指纹：坏文件时下次还会重试，不会"记住了坏指纹就再也不读"
        self._env_key = key
        self._apply_env(raw)
        return True

    def _apply_env(self, raw):
        """把 .env 里的**设置类**字段套到 self 上（构造与热重载共用一份）。"""
        self.ds_api_url = str(_val(
            ("DEEPSEEK_API_URL",), raw, "DEEPSEEK_API_URL",
            "https://api.deepseek.com")).rstrip("/")
        self.ds_api_key = str(_val(
            ("QBM_AGENT_API_KEY", "DEEPSEEK_API_KEY"), raw, "DEEPSEEK_API_KEY", ""))
        self.ds_model = str(_val(
            ("DEEPSEEK_MODEL",), raw, "DEEPSEEK_MODEL", "deepseek-v4-flash"))
        # 0 = 不限制：请求里不带 max_tokens，由接口按模型自身上限走
        # （老板 2026-10-11 明确取消额度限制；原来读不到就默认 300，反而把看图正文挤空过）
        self.ds_max_tokens = int(_val(
            ("DEEPSEEK_MAX_TOKENS",), raw, "DEEPSEEK_MAX_TOKENS", "") or "0")
        # ---- 视觉模型（OpenAI 兼容 /v1/chat/completions，给 DeepSeek 看图用）----
        self.sf_api_key = str(_val(
            ("QBM_AGENT_VISION_KEY", "SILICONFLOW_API_KEY"), raw, "SILICONFLOW_API_KEY", ""))
        # 主模型自己能不能看图（默认能：deepseek 新模型都支持；不吃图会自动退回纯文本）
        self.main_vision = str(_val(
            ("QBM_AGENT_MAIN_VISION",), raw, "QBM_AGENT_MAIN_VISION", "1")).strip() not in ("0", "false", "False")

        self.vision_model = str(_val(
            ("VISION_MODEL",), raw, "VISION_MODEL", "Qwen/Qwen3-Omni-30B-A3B-Instruct"))
        self.vision_api_url = str(_val(
            ("VISION_API_URL",), raw, "VISION_API_URL",
            "https://api.siliconflow.cn/v1")).rstrip("/")
        self.vision_timeout = int(_val(
            ("VISION_TIMEOUT",), raw, "VISION_TIMEOUT", "90"))
        self.vision_max_bytes = int(_val(
            ("VISION_MAX_BYTES",), raw, "VISION_MAX_BYTES", str(12 * 1024 * 1024)))
        # 主人 QQ / 机器人自己 / 唤醒词：都必须认 `.env` 里的**带前缀**键名
        # （write_env 写的就是 QBM_AGENT_*），否则"只写 .env"这条路上一直是死的。
        self.owner_qq = str(_val_prefixed(
            ("QBM_AGENT_OWNER_QQ",), raw, "OWNER_QQ", ""))
        self.bot_qq = str(_val_prefixed(
            ("QBM_AGENT_BOT_ID",), raw, "BOT_QQ", "") or "") or _bot_id_from_env(raw) or "astrobot"
        # 群白名单：**已经学到的群**（agent._learn_group 会写进 self._learned_groups
        # 并落盘 groups.json）不能被这里抹掉，也不能因为"配置里没写"就删掉 ——
        # 但**配置里删掉的群要立即不生效**，所以这里严格按
        # 「配置里的 + 运行期学到的」重建，而不是在旧列表上做并集。
        configured = [g.strip() for g in str(_val_prefixed(
            ("QBM_AGENT_GROUPS",), raw, "GROUPS", "")).split(",") if g.strip()]
        learned = [g for g in (getattr(self, "_learned_groups", None) or [])
                   if g not in configured]
        self.groups = configured + learned
        self.address_words = [w.strip() for w in str(_val_prefixed(
            ("QBM_AGENT_ADDRESS_WORDS",), raw, "ADDRESS_WORDS",
            "李清菡,学姐,姐姐,菡姐,菡菡")).split(",") if w.strip()]
        self.persona_mode = str(_val(
            ("QBM_AGENT_PERSONA_MODE", "PERSONA_MODE"), raw, "PERSONA_MODE", "")).strip().lower()
        self.persona_name = str(_val(
            ("QBM_AGENT_PERSONA_NAME", "PERSONA_NAME"), raw, "PERSONA_NAME", "")).strip()
        if not self.persona_name:
            self.persona_name = "EVA" if self.persona_mode == "eva" else "李清菡"
        # 2026-10-16：档案页写着"可在 .env 里用 QBM_AGENT_PERSONA_EXTRA 追加人设补充"，
        # 但这里原来只认裸键 PERSONA_EXTRA / 无前缀环境变量 —— 说法与实现对不上。
        # 改成和 OWNER_QQ/ADDRESS_WORDS 同一套 _val_prefixed：带 QBM_ 前缀的键优先，
        # 裸键继续兼容（老装机不受影响）。
        self.persona_extra = str(_val_prefixed(
            ("QBM_AGENT_PERSONA_EXTRA", "AGENT_PERSONA_EXTRA"),
            raw, "PERSONA_EXTRA", "")).strip()
        self.respect_qqs = [q.strip() for q in str(_val(
            ("QBM_AGENT_RESPECT_QQS", "RESPECT_QQS"), raw, "RESPECT_QQS", "")).split(",") if q.strip()]

        # 下面这些不参与热重载（会话/调度/语音类参数），但构造时要建全：
        # 只有第一次调用（构造）才走这一段。
        if getattr(self, "_fully_loaded", False):
            return
        self._fully_loaded = True
        self.character_map = {}
        for pair in str(_val(
                ("QBM_AGENT_CHARACTER_MAP", "CHARACTER_QQ_MAP"), raw, "CHARACTER_QQ_MAP", "")).split(","):
            pair = pair.strip()
            if ":" in pair:
                qq, name = pair.split(":", 1)
                qq, name = qq.strip(), name.strip()
                if qq and name:
                    self.character_map[qq] = name
        self.image_api_key = str(_val(
            ("IMAGE_API_KEY", "SILICONFLOW_API_KEY"), raw, "SILICONFLOW_API_KEY", ""))
        self.mute_max_minutes = int(_val(
            ("MUTE_MAX_MINUTES",), raw, "MUTE_MAX_MINUTES", "43200"))
        self.mute_all_max_minutes = int(_val(
            ("MUTE_ALL_MAX_MINUTES",), raw, "MUTE_ALL_MAX_MINUTES", "43200"))
        self.interrupt_on_message = str(_val(
            ("INTERRUPT_ON_MESSAGE",), raw, "INTERRUPT_ON_MESSAGE", "true")).lower() != "false"
        self.sleep_enabled = str(_val(
            ("SLEEP_ENABLED",), raw, "SLEEP_ENABLED", "true")).lower() != "false"
        self.work_cooldown_sec = int(_val(
            ("WORK_COOLDOWN_SEC",), raw, "WORK_COOLDOWN_SEC", "600"))
        self.interject_base = float(_val(
            ("INTERJECT_BASE",), raw, "INTERJECT_BASE", "0.02"))
        self.interject_max = float(_val(
            ("INTERJECT_MAX",), raw, "INTERJECT_MAX", "0.15"))
        self.interject_cooldown_min = int(_val(
            ("INTERJECT_COOLDOWN_MIN",), raw, "INTERJECT_COOLDOWN_MIN", "15"))
        self.interject_max_per_hour = int(_val(
            ("INTERJECT_MAX_PER_HOUR",), raw, "INTERJECT_MAX_PER_HOUR", "4"))
        self.activity_window_min = int(_val(
            ("ACTIVITY_WINDOW_MIN",), raw, "ACTIVITY_WINDOW_MIN", "10"))
        self.min_daily_hours = int(_val(
            ("MIN_DAILY_HOURS",), raw, "MIN_DAILY_HOURS", "18"))
        self.conv_window_seconds = int(_val(
            ("CONV_WINDOW_SECONDS",), raw, "CONV_WINDOW_SECONDS", "600"))
        self.conv_end_cooldown = int(_val(
            ("CONV_END_COOLDOWN_SEC",), raw, "CONV_END_COOLDOWN_SEC", "1800"))
        self.interest_refresh_hours = float(_val(
            ("INTEREST_REFRESH_HOURS",), raw, "INTEREST_REFRESH_HOURS", "6"))
        self.interject_anchor_seconds = int(_val(
            ("INTERJECT_ANCHOR_SECONDS",), raw, "INTERJECT_ANCHOR_SECONDS", "120"))
        # ---- 睡眠 ----
        self.sleep_bedtime_hour = int(_val(
            ("SLEEP_BEDTIME_HOUR",), raw, "SLEEP_BEDTIME_HOUR", "23"))
        self.sleep_min_hours = float(_val(
            ("SLEEP_MIN_HOURS",), raw, "SLEEP_MIN_HOURS", "4"))
        self.sleep_max_hours = float(_val(
            ("SLEEP_MAX_HOURS",), raw, "SLEEP_MAX_HOURS", "10"))
        self.sleep_idle_minutes = int(_val(
            ("SLEEP_IDLE_MINUTES",), raw, "SLEEP_IDLE_MINUTES", "15"))
        self.greeting_private_chance = float(_val(
            ("GREETING_PRIVATE_CHANCE",), raw, "GREETING_PRIVATE_CHANCE", "0.3"))
        self.sleep_other_prob = float(_val(
            ("SLEEP_OTHER_PROB",), raw, "SLEEP_OTHER_PROB", "0.6"))
        self.shut_other_prob = float(_val(
            ("SHUT_OTHER_PROB",), raw, "SHUT_OTHER_PROB", "0.6"))
        # ---- 私聊外联 ----
        self.outreach_chance = float(_val(
            ("OUTREACH_CHANCE",), raw, "OUTREACH_CHANCE", "0.5"))
        self.outreach_min_interval_h = float(_val(
            ("OUTREACH_MIN_INTERVAL_H",), raw, "OUTREACH_MIN_INTERVAL_H", "24"))
        self.outreach_reply_window_h = float(_val(
            ("OUTREACH_REPLY_WINDOW_H",), raw, "OUTREACH_REPLY_WINDOW_H", "24"))
        self.outreach_max_unanswered = int(_val(
            ("OUTREACH_MAX_UNANSWERED",), raw, "OUTREACH_MAX_UNANSWERED", "3"))
        # ---- 群管 ----
        self.join_request_delay_h = float(_val(
            ("JOIN_REQUEST_DELAY_H",), raw, "JOIN_REQUEST_DELAY_H", "3"))
        self.data_dir = str(_val(
            ("QBM_AGENT_DATA_DIR",), raw, "DATA_DIR",
            os.path.join(os.getcwd(), "data", "liqinghan")))
        self.log_file = str(_val(
            ("QBM_AGENT_LOG_FILE",), raw, "LOG_FILE",
            os.path.join(os.getcwd(), "logs", "liqinghan.log")))
        self.tz = str(_val(("TZ",), raw, "TZ", "Asia/Shanghai"))
        # ---- 语音条（MOSS-TTSD + silk）----
        self.voice_model = str(_val(
            ("VOICE_MODEL",), raw, "VOICE_MODEL", "fnlp/MOSS-TTSD-v0.5"))
        self.voice_preset = str(_val(
            ("VOICE_PRESET",), raw, "VOICE_PRESET", ""))
        self.voice_ref_url = str(_val(
            ("VOICE_REF_URL",), raw, "VOICE_REF_URL",
            "https://sf-maas-uat-prod.oss-cn-shanghai.aliyuncs.com/voice_template/fish_audio-Anna.mp3"))
        self.voice_ref_text = str(_val(
            ("VOICE_REF_TEXT",), raw, "VOICE_REF_TEXT",
            "他又躺在那里，眼睛闭着，仍然沉浸在梦境的气氛里。那是个庞杂而亮堂的梦"))
        self.voice_speed = float(_val(
            ("VOICE_SPEED",), raw, "VOICE_SPEED", "1.2"))
        self.voice_gain = float(_val(
            ("VOICE_GAIN",), raw, "VOICE_GAIN", "6"))
        self.voice_prob = float(_val(
            ("VOICE_PROB",), raw, "VOICE_PROB", "0.08"))
        self.voice_min_len = int(_val(
            ("VOICE_MIN_LEN",), raw, "VOICE_MIN_LEN", "80"))
        self.voice_min_stage = int(_val(
            ("VOICE_MIN_STAGE",), raw, "VOICE_MIN_STAGE", "2"))
        self.voice_dir = os.path.join(self.data_dir, "voice")
        # ---- 图片生成 ----
        self.image_model = str(_val(
            ("IMAGE_MODEL",), raw, "IMAGE_MODEL", "Kwai-Kolors/Kolors"))
        # ---- 管理面板（插件内不启动，保留字段兼容）----
        self.admin_port = int(_val(("ADMIN_PORT",), raw, "ADMIN_PORT", "8500"))
        self.admin_bind = str(_val(("ADMIN_BIND",), raw, "ADMIN_BIND", "0.0.0.0"))
        self.admin_token = str(_val(("ADMIN_TOKEN",), raw, "ADMIN_TOKEN", ""))

    def now(self):
        return datetime.now(ZoneInfo(self.tz))

    def now_str(self):
        return self.now().strftime("%Y-%m-%d %H:%M:%S %A")

    def today(self):
        return self.now().strftime("%Y-%m-%d")
