import json
import os
from pathlib import Path
from typing import List, Dict, Any
from nonebot import logger
try:
    from nonebot.adapters.onebot.v11 import MessageEvent
except Exception:  # noqa: BLE001  # 官方通道部署不装 OneBot 适配器，仅注解用
    MessageEvent = object
import nonebot_plugin_localstore as store
from . import pack_params
from . import reply_format
from . import session_policy
from .config import config
from .group_policy import group_allowed

MANAGER_FILE = store.get_plugin_config_file("aichat_manager.json")
logger.info(f"[ai] 管理配置路径: {MANAGER_FILE}")

_DEFAULT_PERSONALITY = "你是星群（AstroSwarm）的 AI 助手，负责跨平台对话与记忆管理。回答保持简洁直接，情绪随心情波动，回答长短看情况，只给关键信息，不啰嗦。"


def _read_manager_file() -> Dict[str, Any]:
    """读管理配置：走统一热读模块（带缓存 + 坏文件回退上次成功的值）。

    拿不到热读模块（老装机）时退回"直接读盘 + 解析失败返回空"；解析失败一律
    返回空 dict，由调用方决定是"保留上一份"还是"用默认值" —— **绝不**在这里
    把默认值写回文件（那等于把用户的人设/模型/开关一起抹掉）。
    """
    try:
        from qbotmanager.core import hotconfig
        return dict(hotconfig.read_json(MANAGER_FILE, {}) or {})
    except Exception:  # noqa: BLE001 —— 老装机没有 hotconfig，按老办法读
        pass
    try:
        with open(MANAGER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError) as e:
        logger.warning(f"[ai] 管理配置读不出来（{type(e).__name__}: {e}），保留上一份")
        return {}


class ChatManager:
    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._reload_key = None
        self._load_manager_config()
        self._reload_key = self._file_key()

    # ------------------------------------------------------------ 热重载
    @staticmethod
    def _pack_config_paths() -> list:
        """也跟着热重载的能力包配置文件（2026-10-15 审计 E）。

        控制台里改「连续对话」这类能力包参数，写的是
        `<tool_packs>/<包 id>/config.json`，**不碰** aichat_manager.json ——
        只盯 MANAGER_FILE 的指纹就会"界面显示保存成功、机器人一直用旧值"。
        """
        paths = []
        try:
            from . import pack_params

            root = pack_params.packs_root()
            if root:
                for pack_id in ("session-chat", "proactive"):
                    for name in ("config.json", "behavior.json"):
                        paths.append(Path(root) / pack_id / name)
        except Exception:  # noqa: BLE001 —— 读不到包目录不影响热重载
            pass
        return paths

    @staticmethod
    def _file_key():
        """配置文件指纹（mtime_ns + 大小），用来判断客户端是不是刚改过。

        包含主配置文件**和跟着热重载的能力包配置文件**（见 `_pack_config_paths`）。
        """
        parts = []
        for path in [MANAGER_FILE] + ChatManager._pack_config_paths():
            try:
                st = os.stat(path)
            except OSError:
                parts.append((str(path), 0, 0))
                continue
            parts.append((str(path), st.st_mtime_ns, st.st_size))
        return tuple(parts)

    def maybe_reload(self) -> bool:
        """客户端改了配置就重新读盘 —— 换了人设/模型/开关都不用重启机器人。

        2026-10-10 修：以前只在启动时读一次，用户改完人设机器人还用旧的，
        界面上看就是"我改了它不理我"（要重启才生效）。
        2026-10-11 再修：读盘统一走 `core.hotconfig`（带 mtime+size 缓存 +
        **坏文件回退上次成功的值**）—— 配置文件被写到一半（非法 JSON）时，
        以前会整份退成默认值（人设/模型/开关全没了，还会被下一次保存写回文件），
        现在只回退、不降级、不写盘，并留一条限流 WARNING。
        只重读设置快照，运行期状态（群活跃度、会话、记忆）都不在这里，不会被冲掉。
        """
        key = self._file_key()
        if key is None or key == self._reload_key:
            return False
        self._load_manager_config()
        # ⚠ 指纹必须在 load **之后**再记（2026-10-15 修）。
        # `_load_manager_config()` → `_merge_env_config()` 末尾会**无条件写盘**
        # （`_save_manager_config()`），写完后 mtime 就变了。以前这里是"先记指纹、
        # 再 load"，于是下一次调用必然又判定"文件变了" → **每条消息都重载一遍 +
        # 重写一遍配置**。两个后果：
        #   ① 写盘放大、和控制台抢写同一个文件、日志刷屏；
        #   ② `proactive._wait_effective` 每 10 秒被这个 True 打断并重新计时
        #      → **主动聊天 / 群聊搭话永远发不出任何消息**（重启后才好，改次设置又死）。
        # 构造函数里本来就是"读完再记"（见上面 __init__），这里对齐同一口径。
        self._reload_key = self._file_key()
        logger.info("聊天管理器配置已热重载（客户端改了配置，无需重启）")
        return True

    def _load_manager_config(self):
        """加载管理配置（坏文件回退上次成功的值，绝不悄悄退成默认值写回盘）。"""
        if not os.path.exists(MANAGER_FILE):
            self._data = self._get_default_config()
            self._save_manager_config()
            logger.info("创建新的聊天管理器配置")
        else:
            data = _read_manager_file()
            if data:
                self._data = data
                logger.info("聊天管理器配置加载成功")
            elif self._data:
                logger.warning(
                    "聊天管理器配置读不出来（非法 JSON / 读盘失败），"
                    "**保留上一份成功配置**继续跑；修好文件后自动恢复")
            else:
                self._data = self._get_default_config()
                logger.error("聊天管理器配置读不出来，且没有可回退的上一份，暂用默认值")
        self._merge_env_config()
    
    def _get_default_config(self) -> Dict[str, Any]:
        """获取默认配置"""
        return {
            "super_users": list(config.aichat_super_users),
            "enabled_groups": [],
            "group_chat_enabled": True,  # 群聊回话总开关（默认允许；关掉则群里一律不吭声）
            "chat_enabled": True,
            "group_chat_probability": 1,  # 群活跃度基础值
            "personality": _DEFAULT_PERSONALITY,
            "ai_configs": [],  # 改为数组形式，支持多个模型配置
            "current_ai_config": 0,  # 当前使用的配置索引
            "image_recognition_enabled": True,   # 图片识别功能开关（2026-10-03 起默认开：能看图的模型现在很常见，关着会让 AI 说"我看不到图"）
            "current_image_recognition_config": 0,  # 当前图片识别配置索引
            "enable_search": False,   # 是否启用搜索功能
            "mcp_enabled": False,     # MCP功能总开关
            "mcp_servers": {},
            # ===== 魔改新增：主动聊天配置 =====
            "proactive_enabled": False,  # 主动聊天总开关（默认关闭，需用命令开启）
            "proactive_targets": list(config.aichat_super_users),  # 主动聊天目标用户列表
            "proactive_groups": list(config.aichat_proactive_groups),  # 主动聊天群聊列表
            "proactive_interval_min": 30,  # 随机间隔最小值（分钟）
            "proactive_interval_max": 90,  # 随机间隔最大值（分钟）
            "proactive_cooldown": 30,      # 同一用户最短发送间隔（分钟）
            "proactive_quiet_start": 0,    # 免打扰时段开始小时（24小时制）
            "proactive_quiet_end": 8,      # 免打扰时段结束小时（24小时制）
            "memory_enabled": True,        # 全局记忆开关（默认开启，可在 AstroSwarm AI 大脑页关闭）
            # ===== 魔改新增：群聊搭话 =====
            "interject_enabled": False,    # 群聊搭话总开关
            "interject_probability": 0.3,  # 搭话概率
            "interject_interval_min": 30,  # 搭话随机间隔最小值（分钟）
            "interject_interval_max": 90,  # 搭话随机间隔最大值（分钟）
            "interject_history_count": 10, # 搭话时读取最近消息条数
            # ===== 魔改新增：群聊免@会话 =====
            "session_expire_seconds": 180,  # 免@会话有效期（秒，30~300）
            "session_mode": "timeout",     # 免@判断模式：timeout=时间窗（默认）/ api=AI 读群记录判断
            "session_unrelated_limit": 5,  # 连续无关消息条数上限
            "group_auto_participate": False,  # 旧版群聊随机参与（默认关闭）
            # ===== 插件主动发到指定目标的白名单（G2，2026-10-02）=====
            # 形如 ["group:123456", "user:abc"]；**留空 = 一个都不许发**（防止插件当群发器）。
            "allowed_send_targets": [],
            # "mcp_servers": {          # MCP服务器配置
            #     # "web_search": {
            #     #     "url": "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/sse",
            #     #     "headers": {
            #     #         "Authorization": "Bearer you-key-here"
            #     #     },
            #     #     "enabled": True
            #     # }
            #     # 可以添加stdio类型的配置示例:
            #     # "local_tool": {
            #     #     "type": "stdio",
            #     #     "command": "python",
            #     #     "args": ["/path/to/mcp/server.py"],
            #     #     "env": {"KEY": "value"},
            #     #     "enabled": True
            #     # }
            # }
        }
    
    def _save_manager_config(self):
        """保存管理配置"""
        try:
            with open(MANAGER_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存聊天管理器配置失败: {e}")

    def _merge_env_config(self):
        """把 .env 中的新配置合并进管理器（.env 优先）"""
        # 超级用户并集
        su = list(self._data.get("super_users", []))
        for uid in config.aichat_super_users:
            if uid and uid not in su:
                su.append(uid)
        self._data["super_users"] = su

        # 主动聊天目标用户（.env 追加，不去重历史数据）
        targets = self._data.get("proactive_targets", [])
        for uid in config.aichat_proactive_users:
            if uid and uid not in targets:
                targets.append(uid)
        self._data["proactive_targets"] = targets

        # 主动聊天群聊（.env 追加）
        groups = self._data.get("proactive_groups", [])
        for gid in config.aichat_proactive_groups:
            if gid and gid not in groups:
                groups.append(gid)
        self._data["proactive_groups"] = groups

        # 各参数：**显式配过**的才覆盖界面设置，没配的只当"缺省值"（键不存在时才补）
        self._fill_param("aichat_proactive_interval_min", "proactive_interval_min",
                         config.aichat_proactive_interval_min, lo=1)
        self._fill_param("aichat_proactive_interval_max", "proactive_interval_max",
                         config.aichat_proactive_interval_max, lo=1)
        self._fill_param("aichat_proactive_cooldown", "proactive_cooldown",
                         config.aichat_proactive_cooldown, lo=1)
        self._fill_param("aichat_proactive_quiet_start", "proactive_quiet_start",
                         config.aichat_proactive_quiet_start, lo=0, hi=23)
        self._fill_param("aichat_proactive_quiet_end", "proactive_quiet_end",
                         config.aichat_proactive_quiet_end, lo=0, hi=23)

        self._fill_param("aichat_interject_enabled", "interject_enabled",
                         bool(config.aichat_interject_enabled))
        self._fill_param("aichat_interject_probability", "interject_probability",
                         max(0.0, min(1.0, config.aichat_interject_probability)))
        self._fill_param("aichat_interject_interval_min", "interject_interval_min",
                         config.aichat_interject_interval_min, lo=1)
        self._fill_param("aichat_interject_interval_max", "interject_interval_max",
                         config.aichat_interject_interval_max, lo=1)
        self._fill_param("aichat_interject_history_count", "interject_history_count",
                         config.aichat_interject_history_count, lo=1)

        # 旧版分钟配置迁移到秒（仅当旧数据存在且新值未配置时）
        if "session_expire_minutes" in self._data and "session_expire_seconds" not in self._data:
            self._data["session_expire_seconds"] = int(self._data["session_expire_minutes"]) * 60
            self._data.pop("session_expire_minutes", None)
        # 配置里的值只当"默认值"：用户已经调过就不再每次启动覆盖回去
        # （以前是无条件覆盖，用户在插件配置里改了 N 秒、重启一次就被打回 30）
        if self._explicit("aichat_session_expire_seconds") and \
                config.aichat_session_expire_seconds > 0:
            self._data["session_expire_seconds"] = session_policy.clamp_expire_seconds(
                config.aichat_session_expire_seconds)
        elif "session_expire_seconds" not in self._data:
            self._data["session_expire_seconds"] = session_policy.clamp_expire_seconds(
                config.aichat_session_expire_seconds)
        if "session_mode" not in self._data:
            self._data["session_mode"] = session_policy.normalize_mode(
                getattr(config, "aichat_session_mode", ""))
        self._fill_param("aichat_session_unrelated_limit", "session_unrelated_limit",
                         config.aichat_session_unrelated_limit, lo=1)
        self._fill_param("aichat_group_auto_participate", "group_auto_participate",
                         bool(config.aichat_group_auto_participate))
        self._apply_session_pack()
        self._save_manager_config()

    @staticmethod
    def _explicit(cfg_name: str) -> bool:
        """这个配置项是不是**显式配过**的（.env / 插件配置里真的写了）。

        2026-10-15：`_merge_env_config` 以前用 `if config.aichat_xxx > 0` 这类守卫，
        可 config.py 里这些字段的**默认值本身就满足条件**（30 / 90 / 0 / 8 …），
        于是每次热重载都拿默认值把界面上的设置盖回去 —— 老板在「AI 大脑」页改的
        主动聊天间隔、免打扰时段、搭话开关与概率，下一次热重载就全被打回默认，
        症状正是"改了没生效 / 自己弹回去 / 打开群聊搭话没反应"。

        区分办法：pydantic 的 `model_fields_set` 只含**显式提供**的字段，
        拿它判"用户到底配没配"。同文件里 `session_expire_seconds` 那段早就是这个
        口径（注释写着"配置里的值只当默认值"），这里统一到所有参数上。
        """
        try:
            return cfg_name in (getattr(config, "model_fields_set", None) or set())
        except Exception:  # noqa: BLE001
            return False

    def _fill_param(self, cfg_name: str, data_key: str, value,
                    *, lo=None, hi=None) -> None:
        """把插件配置的值写进管理器配置。

        规则：**显式配过** → 覆盖界面里已有的值；没显式配 → 只当缺省值（键不存在时才补）。
        lo/hi 是范围检查：不合法就整条跳过，绝不把脏值写进配置。
        """
        if lo is not None and not (value >= lo):
            return
        if hi is not None and not (value <= hi):
            return
        if self._explicit(cfg_name) or data_key not in self._data:
            self._data[data_key] = value

    def _apply_session_pack(self) -> None:
        """把「连续对话能力包」里的参数应用上来（2026-10-01 老板要求做成能力包）。

        优先级：能力包 config.json（用户在「已装能力包 → 配置」里改的）
        > 能力包 behavior.json（包默认）> 插件配置.json / 环境变量 > 代码默认。
        包没装就什么都不做，功能按插件默认值走（不让用户因为没装包而失去免@对话）。
        """
        try:
            root = pack_params.packs_root()
            params = pack_params.read_pack_params(root, "session-chat", "session")
        except Exception as e:  # noqa: BLE001 —— 读包失败绝不能影响启动
            logger.debug(f"[ai] 读连续对话能力包参数失败: {e}")
            return
        if not params:
            return
        if "enabled" in params:
            # 关掉 = mode off（群里只有 @ 才回）
            self._data["session_mode"] = (
                session_policy.MODE_OFF if not params.get("enabled")
                else session_policy.normalize_mode(params.get("mode")
                                                   or self._data.get("session_mode")))
        elif params.get("mode"):
            self._data["session_mode"] = session_policy.normalize_mode(params["mode"])
        if params.get("expire_seconds") is not None:
            self._data["session_expire_seconds"] = session_policy.clamp_expire_seconds(
                params["expire_seconds"])
        if params.get("unrelated_limit") is not None:
            try:
                self._data["session_unrelated_limit"] = max(1, min(20, int(params["unrelated_limit"])))
            except (TypeError, ValueError):
                pass
        # 用 f-string：nonebot 的 logger 是 loguru（{} 风格），写成 %s 不会替换
        logger.info(
            f"[ai] 连续对话能力包参数已应用: mode={self._data.get('session_mode')}"
            f" 有效期={self._data.get('session_expire_seconds')} 秒"
            f" 无关上限={self._data.get('session_unrelated_limit')} 句")
    
    # MCP功能管理
    def is_mcp_enabled(self) -> bool:
        """检查MCP功能是否启用"""
        return self._data.get("mcp_enabled", False)
    
    def set_mcp_enabled(self, enabled: bool) -> bool:
        """设置MCP功能开关"""
        if self._data.get("mcp_enabled", False) != enabled:
            self._data["mcp_enabled"] = enabled
            self._save_manager_config()
            return True
        return False
    
    def get_mcp_servers(self) -> Dict[str, Any]:
        """获取所有MCP服务器配置"""
        return self._data.get("mcp_servers", {})
    
    def get_enabled_mcp_servers(self) -> Dict[str, Any]:
        """获取启用的MCP服务器配置"""
        all_servers = self.get_mcp_servers()
        return {name: config for name, config in all_servers.items() 
                if config.get("enabled", True)}
    
    def set_mcp_server_enabled(self, server_name: str, enabled: bool) -> bool:
        """设置单个MCP服务器的启用状态"""
        servers = self.get_mcp_servers()
        if server_name in servers:
            if servers[server_name].get("enabled", True) != enabled:
                servers[server_name]["enabled"] = enabled
                self._data["mcp_servers"] = servers
                self._save_manager_config()
                return True
        return False
    
    def add_mcp_server(self, server_name: str, server_type: str, **kwargs) -> bool:
        """添加MCP服务器配置
        
        Args:
            server_name: 服务器名称
            server_type: 服务器类型 "sse" 或 "stdio"
            **kwargs: 配置参数
                - 对于sse类型: url, headers
                - 对于stdio类型: command, args, env
        """
        servers = self.get_mcp_servers()
        if server_name in servers:
            return False
        
        base_config = {
            "type": server_type,
            "enabled": True
        }
        
        if server_type == "sse":
            base_config.update({
                "url": kwargs.get("url"),
                "headers": kwargs.get("headers", {})
            })
        elif server_type == "stdio":
            base_config.update({
                "command": kwargs.get("command"),
                "args": kwargs.get("args", []),
                "env": kwargs.get("env", {})
            })
        else:
            return False
        
        servers[server_name] = base_config
        self._data["mcp_servers"] = servers
        self._save_manager_config()
        return True
    
    def remove_mcp_server(self, server_name: str) -> bool:
        """移除MCP服务器配置"""
        servers = self.get_mcp_servers()
        if server_name in servers:
            del servers[server_name]
            self._data["mcp_servers"] = servers
            self._save_manager_config()
            return True
        return False
    
    # 插件主动发到指定目标的白名单（G2）
    def get_send_targets(self) -> list:
        """允许插件主动发送的目标（形如 group:123 / user:abc）。

        **留空 = 一个都不许发** —— 宁可插件发不出去，也不让它变成群发器；
        要放开就在配置里逐个写清楚。
        """
        raw = self._data.get("allowed_send_targets") or []
        if not isinstance(raw, list):
            return []
        return [str(x).strip() for x in raw if str(x).strip()]

    def set_send_targets(self, targets) -> bool:
        clean = [str(x).strip() for x in (targets or []) if str(x).strip()]
        if self._data.get("allowed_send_targets") == clean:
            return False
        self._data["allowed_send_targets"] = clean
        self._save_manager_config()
        return True

    # 入群欢迎（QQ 官方：GROUP_ADD_ROBOT 被动回复，不占主动额度）
    def is_group_welcome_enabled(self) -> bool:
        """被拉进新群时要不要打个招呼（默认开）。"""
        return bool(self._data.get("group_welcome_enabled", True))

    def set_group_welcome_enabled(self, enabled: bool) -> bool:
        self._data["group_welcome_enabled"] = bool(enabled)
        self._save_manager_config()
        return True

    # 群聊触发概率管理
    def get_group_chat_probability(self) -> float:
        """获取群聊触发概率"""
        return self._data.get("group_chat_probability", 0.2)
    
    def set_group_chat_probability(self, probability: float) -> bool:
        """设置群聊触发概率"""
        if not 0 <= probability <= 1:
            return False
        
        if self._data.get("group_chat_probability", 0.2) != probability:
            self._data["group_chat_probability"] = probability
            self._save_manager_config()
            return True
        return False
    
    # 管理员管理
    def is_super_user(self, user_id: str) -> bool:
        """检查用户是否为管理员"""
        return user_id in self._data.get("super_users", [])

    def get_super_users(self) -> List[str]:
        """获取所有管理员"""
        return self._data.get("super_users", [])
    
    # 富内容解析（引用 / 聊天记录 / 附件说明）
    def is_rich_parse_enabled(self) -> bool:
        """群里发引用、聊天记录、图片时，要不要把这些内容也喂给 AI（默认开）。

        以前只把纯文本递过去，引用和聊天记录的内容直接丢，AI 只能瞎猜。
        """
        return bool(self._data.get("rich_parse_enabled", True))

    def set_rich_parse_enabled(self, enabled: bool) -> bool:
        """客户端 AI 大脑页的开关写这里。"""
        self._data["rich_parse_enabled"] = bool(enabled)
        self._save_manager_config()
        return True

    # 跨平台互通总开关（发消息/发文件到用户自己的另一个平台）；默认开
    # ---- Agent 模式（老板 2026-10-12：要像 codex / DSH 那样会调工具干事）----
    # 只管**行为**：先动手干活再说话；人设依旧只管口吻。默认开。
    # ---- 自动通过好友申请（卖家场景）：**默认关**，开了才自动通过 ----
    # ---- 本机能力清单里被关掉的能力（桌面端写，机器人侧读）----
    def get_disabled_tools(self) -> list:
        names = self._data.get("disabled_tools")
        return [str(n) for n in names] if isinstance(names, list) else []

    def is_auto_approve_friend_enabled(self) -> bool:
        return bool(self._data.get("auto_approve_friend", False))

    def set_auto_approve_friend(self, enabled: bool) -> bool:
        self._data["auto_approve_friend"] = bool(enabled)
        self._save_manager_config()
        return True

    def is_agent_mode_enabled(self) -> bool:
        return bool(self._data.get("agent_mode_enabled", True))

    def set_agent_mode_enabled(self, enabled: bool) -> bool:
        self._data["agent_mode_enabled"] = bool(enabled)
        self._save_manager_config()
        return True

    def is_cross_platform_enabled(self) -> bool:
        return bool(self._data.get("cross_platform_enabled", True))

    # 聊天里自学（AI 遇到不会做的事，当场让工坊给自己写个插件）；默认开
    def is_self_forge_enabled(self) -> bool:
        return bool(self._data.get("self_forge_enabled", True))

    def set_self_forge_enabled(self, enabled: bool) -> bool:
        self._data["self_forge_enabled"] = bool(enabled)
        self._save_manager_config()
        return True

    # 群聊管理
    def is_group_chat_enabled(self) -> bool:
        """群聊回话总开关（默认开）。老配置里没有这个字段时按开处理。"""
        return bool(self._data.get("group_chat_enabled", True))

    def set_group_chat_enabled(self, enabled: bool) -> bool:
        """打开/关闭群聊回话（客户端 AI 大脑页的开关写这里）。"""
        self._data["group_chat_enabled"] = bool(enabled)
        self._save_manager_config()
        return True

    def is_group_enabled(self, group_id: str) -> bool:
        """这个群能不能回话（规则集中在 group_policy.group_allowed，默认所有群都允许）。"""
        return group_allowed(self._data, group_id)

    def enable_group(self, group_id: str) -> bool:
        """启用群聊"""
        if group_id not in self._data.get("enabled_groups", []):
            if "enabled_groups" not in self._data:
                self._data["enabled_groups"] = []
            self._data["enabled_groups"].append(group_id)
            self._save_manager_config()
            return True
        return False
    
    def disable_group(self, group_id: str) -> bool:
        """禁用群聊"""
        if group_id in self._data.get("enabled_groups", []):
            self._data["enabled_groups"].remove(group_id)
            self._save_manager_config()
            return True
        return False
    
    def get_enabled_groups(self) -> List[str]:
        """获取所有启用的群聊"""
        return self._data.get("enabled_groups", [])
    
    # AI配置管理
    def get_ai_configs(self) -> List[Dict[str, str]]:
        """获取所有AI配置"""
        return self._data.get("ai_configs", [])
    
    def get_current_ai_config(self) -> Dict[str, str]:
        """获取当前使用的AI配置"""
        configs = self.get_ai_configs()
        current_index = self._data.get("current_ai_config", 0)
        if configs and 0 <= current_index < len(configs):
            return configs[current_index]
        return {}

    # ------------------------------------------------------------ 采样参数（热读）
    # 2026-10-11：温度 / 最大长度以前写死在 chat.py 里（`max_tokens=4096`），
    # 界面上改不了；现在放进管理配置，**每条消息现读**，改完立即生效。
    AI_TEMPERATURE_DEFAULT = None      # None = 不传，跟随服务商默认
    AI_MAX_TOKENS_DEFAULT = 4096
    AI_MAX_TOKENS_FLOOR = 64
    AI_MAX_TOKENS_CEIL = 32768

    def get_temperature(self):
        """采样温度；配置里没写 / 写了-1 就返回 None（不传这个参数，跟随服务商默认）。"""
        raw = self._data.get("ai_temperature", self.AI_TEMPERATURE_DEFAULT)
        if raw is None or raw == "":
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if value < 0:
            return None
        return max(0.0, min(2.0, value))

    def get_max_tokens(self, default: int = 0) -> int:
        """单次回复最大长度（token）。没配就用调用方给的默认值。"""
        raw = self._data.get("ai_max_tokens", self.AI_MAX_TOKENS_DEFAULT)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            value = int(default or self.AI_MAX_TOKENS_DEFAULT)
        return max(self.AI_MAX_TOKENS_FLOOR, min(self.AI_MAX_TOKENS_CEIL, value))

    def sampling_params(self, default_max_tokens: int = 0) -> Dict[str, Any]:
        """给请求用的采样参数（不含 model/messages）。temperature=None 时**不传**。"""
        out: Dict[str, Any] = {"max_tokens": self.get_max_tokens(default_max_tokens)}
        temp = self.get_temperature()
        if temp is not None:
            out["temperature"] = temp
        return out

    def request_preview(self, default_max_tokens: int = 0) -> Dict[str, Any]:
        """**下一次请求实际会用的参数**（不调模型、不打印密钥）。

        给「改完到底生效没」这件事一个可断言的证据：模型名 / 接口地址 / 温度 /
        最大长度 / 人设摘要，全部现读。
        """
        cfg = self.get_current_ai_config() or {}
        key = str(cfg.get("api_key") or "")
        params = self.sampling_params(default_max_tokens)
        personality = self.get_personality()
        return {
            "model": cfg.get("model", ""),
            "api_url": cfg.get("api_url", ""),
            "api_key_set": bool(key),
            "api_key_tail": key[-4:] if len(key) >= 4 else "",
            "temperature": params.get("temperature"),
            "max_tokens": params["max_tokens"],
            "personality_chars": len(personality),
            "personality_head": personality[:40],
            "config_file": str(MANAGER_FILE),
        }

    # ------------------------------------------------------------ 空 @ / 上文窗口（热读）
    # 2026-10-11 老板报的"空@把一小时前的内容当问题答了"：
    # ① 空 @ 怎么办（轻唤 / 不回复）；② 上文回溯上限 T 分钟。
    # 两个都**现读**（每条消息一次 dict 取值），改完立即生效，不用重启机器人。
    EMPTY_AT_DEFAULT = "greet"
    CONTEXT_WINDOW_DEFAULT = 5

    def get_empty_at_action(self) -> str:
        """空 @（@ 了但没正文）怎么办：greet（默认，轻唤一句）/ silent（不回复）。"""
        raw = str(self._data.get("empty_at_action") or "").strip().lower()
        if raw in ("silent", "不回复", "不回", "静默", "none", "off"):
            return "silent"
        return "greet"

    def get_context_window_minutes(self) -> int:
        """上文回溯上限（分钟）：超过它的旧消息不进提示词。默认 5，夹到 1~1440。"""
        try:
            value = int(self._data.get("context_window_minutes",
                                       self.CONTEXT_WINDOW_DEFAULT))
        except (TypeError, ValueError):
            return self.CONTEXT_WINDOW_DEFAULT
        return max(1, min(24 * 60, value))

    # ---- 单条消息长度上限（H-13，2026-10-11 晚）----
    # 上下文原来只按**条数**裁（20 条），字数没有闸门：粘一篇十万字长文进来就是
    # 一次超大请求（400 / 超时 / 烧钱）。默认 4000 字，夹到 200~100000；0 = 不限制。
    MAX_INCOMING_CHARS_DEFAULT = 4000
    MAX_INCOMING_CHARS_MIN = 200
    MAX_INCOMING_CHARS_MAX = 100000

    def get_max_incoming_chars(self) -> int:
        """单条正文上限（字）。0 = 不限制；非数字回落到默认 4000。"""
        raw = self._data.get("max_incoming_chars", self.MAX_INCOMING_CHARS_DEFAULT)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return self.MAX_INCOMING_CHARS_DEFAULT
        if value <= 0:
            return 0                      # 显式关掉限制
        return max(self.MAX_INCOMING_CHARS_MIN,
                   min(self.MAX_INCOMING_CHARS_MAX, value))

    def set_max_incoming_chars(self, chars: int) -> bool:
        """设置单条正文上限（0 = 不限制，其余夹到 200~100000）。"""
        try:
            value = int(chars)
        except (TypeError, ValueError):
            return False
        if value > 0:
            value = max(self.MAX_INCOMING_CHARS_MIN,
                        min(self.MAX_INCOMING_CHARS_MAX, value))
        if value == self.get_max_incoming_chars():
            return False
        self._data["max_incoming_chars"] = value
        self._save_manager_config()
        return True

    # ---- 分条发送（2026-10-12 老板要求：像人一样分几条发，但别做死成"所有回复都多条"）----
    # 五个键都**每条消息现读**（brain 走 chat_manager.get_*），所以控制台改完立即生效。
    MULTI_MESSAGE_MODE_DEFAULT = "auto"
    MAX_REPLY_PARTS_DEFAULT = 3
    MAX_REPLY_PARTS_MIN = 1
    MAX_REPLY_PARTS_MAX = 6
    MAX_PART_CHARS_DEFAULT = 500
    MAX_PART_CHARS_MIN = 50
    MAX_PART_CHARS_MAX = 2000
    PART_DELAY_MS_DEFAULT = 80
    PART_DELAY_MS_MIN = 0
    PART_DELAY_MS_MAX = 500
    PART_DELAY_MAX_MS_DEFAULT = 3000
    PART_DELAY_MAX_MS_MIN = 300
    PART_DELAY_MAX_MS_MAX = 5000

    @staticmethod
    def _clamp_int(raw, default: int, low: int, high: int) -> int:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return max(low, min(high, value))

    def get_multi_message_mode(self) -> str:
        """分条模式：off（永远一条）/ auto（默认，由内容决定）/ always（尽量分条）。

        写坏了不回退成 off（那会让功能悄悄消失），一律回默认 auto。
        """
        return reply_format.normalize_multi_mode(
            self._data.get("multi_message_mode", self.MULTI_MESSAGE_MODE_DEFAULT))

    def set_multi_message_mode(self, mode) -> bool:
        value = reply_format.normalize_multi_mode(mode)
        if value == self.get_multi_message_mode():
            return False
        self._data["multi_message_mode"] = value
        self._save_manager_config()
        return True

    def get_max_reply_parts(self) -> int:
        """一次回复最多分几条。默认 3，夹到 1~6。"""
        return self._clamp_int(self._data.get("max_reply_parts"),
                               self.MAX_REPLY_PARTS_DEFAULT,
                               self.MAX_REPLY_PARTS_MIN, self.MAX_REPLY_PARTS_MAX)

    def set_max_reply_parts(self, count) -> bool:
        value = self._clamp_int(count, self.MAX_REPLY_PARTS_DEFAULT,
                                self.MAX_REPLY_PARTS_MIN, self.MAX_REPLY_PARTS_MAX)
        if value == self.get_max_reply_parts():
            return False
        self._data["max_reply_parts"] = value
        self._save_manager_config()
        return True

    def get_max_part_chars(self) -> int:
        """单条发出的字数上限。默认 500，夹到 50~2000；0 = 不限制。

        超了是**截断**（绝不硬拆成更多条）—— 硬拆会让一条长回复变成刷屏。
        """
        return self._clamp_int(self._data.get("max_part_chars"),
                               self.MAX_PART_CHARS_DEFAULT,
                               self.MAX_PART_CHARS_MIN, self.MAX_PART_CHARS_MAX)

    def set_max_part_chars(self, chars) -> bool:
        value = self._clamp_int(chars, self.MAX_PART_CHARS_DEFAULT,
                                self.MAX_PART_CHARS_MIN, self.MAX_PART_CHARS_MAX)
        if value == self.get_max_part_chars():
            return False
        self._data["max_part_chars"] = value
        self._save_manager_config()
        return True

    def get_part_delay_ms(self) -> int:
        """每条间隔的**每字**毫秒数（打字速度）。默认 80，夹到 0~500。"""
        return self._clamp_int(self._data.get("part_delay_ms"),
                               self.PART_DELAY_MS_DEFAULT,
                               self.PART_DELAY_MS_MIN, self.PART_DELAY_MS_MAX)

    def set_part_delay_ms(self, ms) -> bool:
        value = self._clamp_int(ms, self.PART_DELAY_MS_DEFAULT,
                                self.PART_DELAY_MS_MIN, self.PART_DELAY_MS_MAX)
        if value == self.get_part_delay_ms():
            return False
        self._data["part_delay_ms"] = value
        self._save_manager_config()
        return True

    def get_part_delay_max_ms(self) -> int:
        """单条间隔的硬上限（毫秒）。默认 3000，夹到 300~5000 —— 不会等 10 秒。"""
        return self._clamp_int(self._data.get("part_delay_max_ms"),
                               self.PART_DELAY_MAX_MS_DEFAULT,
                               self.PART_DELAY_MAX_MS_MIN, self.PART_DELAY_MAX_MS_MAX)

    def set_part_delay_max_ms(self, ms) -> bool:
        value = self._clamp_int(ms, self.PART_DELAY_MAX_MS_DEFAULT,
                                self.PART_DELAY_MAX_MS_MIN, self.PART_DELAY_MAX_MS_MAX)
        if value == self.get_part_delay_max_ms():
            return False
        self._data["part_delay_max_ms"] = value
        self._save_manager_config()
        return True
    
    def add_ai_config(self, name: str, api_key: str, api_url: str, model: str) -> bool:
        """添加新的AI配置"""
        if "ai_configs" not in self._data:
            self._data["ai_configs"] = []
        
        for config in self._data["ai_configs"]:
            if config.get("name") == name:
                return False
        
        new_config = {
            "name": name,
            "api_key": api_key,
            "api_url": api_url,
            "model": model
        }
        
        self._data["ai_configs"].append(new_config)
        self._save_manager_config()
        return True
    
    def remove_ai_config(self, name: str) -> bool:
        """移除AI配置"""
        configs = self.get_ai_configs()
        for i, config in enumerate(configs):
            if config.get("name") == name:
                is_current_chat_config = (self._data.get("current_ai_config", 0) == i)
                is_current_image_config = (self._data.get("current_image_recognition_config", 0) == i)
                
                self._data["ai_configs"].pop(i)
                
                current_chat_index = self._data.get("current_ai_config", 0)
                if current_chat_index >= i:
                    self._data["current_ai_config"] = max(0, current_chat_index - 1)
                
                current_image_index = self._data.get("current_image_recognition_config", 0)
                if current_image_index >= i:
                    self._data["current_image_recognition_config"] = max(0, current_image_index - 1)
                
                self._save_manager_config()
                return True, is_current_chat_config, is_current_image_config
        return False, False, False
    
    def switch_ai_config(self, name: str) -> bool:
        """切换到指定的AI配置"""
        configs = self.get_ai_configs()
        for i, config in enumerate(configs):
            if config.get("name") == name:
                self._data["current_ai_config"] = i
                self._save_manager_config()
                return True
        return False
    
    def get_current_config_index(self) -> int:
        """获取当前配置索引"""
        return self._data.get("current_ai_config", 0)
    
    # 全局开关管理
    def is_chat_enabled(self) -> bool:
        """检查AI聊天是否启用"""
        return self._data.get("chat_enabled", True)
    
    def set_chat_enabled(self, enabled: bool) -> bool:
        """设置AI聊天开关"""
        if self._data.get("chat_enabled", True) != enabled:
            self._data["chat_enabled"] = enabled
            self._save_manager_config()
            return True
        return False
    
    # 搜索功能开关
    def is_search_enabled(self) -> bool:
        """检查搜索功能是否启用"""
        return self._data.get("enable_search", False)
    
    def set_search_enabled(self, enabled: bool) -> bool:
        """设置搜索功能开关"""
        if self._data.get("enable_search", False) != enabled:
            self._data["enable_search"] = enabled
            self._save_manager_config()
            return True
        return False
    
    # 人设管理
    def get_personality(self) -> str:
        """获取人设配置"""
        return self._data.get("personality", "")
    
    def set_personality(self, personality: str) -> bool:
        """设置人设配置"""
        if self._data.get("personality", "") != personality:
            self._data["personality"] = personality
            self._save_manager_config()
            return True
        return False
    
    # 权限检查
    def check_permission(self, event: MessageEvent) -> bool:
        """检查用户是否有权限操作"""
        user_id = str(event.user_id)
        return self.is_super_user(user_id)

    # 图片识别相关方法
    def is_image_recognition_enabled(self) -> bool:
        """检查图片识别功能是否启用（老配置缺这个键时按"开"处理）。"""
        return bool(self._data.get("image_recognition_enabled", True))

    def set_image_recognition_enabled(self, enabled: bool) -> bool:
        """设置图片识别功能开关"""
        if self._data.get("image_recognition_enabled", False) != enabled:
            self._data["image_recognition_enabled"] = enabled
            self._save_manager_config()
            return True
        return False

    def get_current_image_recognition_config(self) -> Dict[str, str]:
        """获取当前图片识别使用的AI配置"""
        configs = self.get_ai_configs()
        current_index = self._data.get("current_image_recognition_config", 0)
        if configs and 0 <= current_index < len(configs):
            return configs[current_index]
        return {}

    def switch_image_recognition_config(self, name: str) -> bool:
        """切换到指定的图片识别配置"""
        configs = self.get_ai_configs()
        for i, config in enumerate(configs):
            if config.get("name") == name:
                self._data["current_image_recognition_config"] = i
                self._save_manager_config()
                return True
        return False

    def get_current_image_config_index(self) -> int:
        """获取当前图片识别配置索引"""
        return self._data.get("current_image_recognition_config", 0)

    # ===== 魔改新增：主动聊天管理 =====
    def is_proactive_enabled(self) -> bool:
        """检查主动聊天是否开启"""
        return self._data.get("proactive_enabled", False)

    def set_proactive_enabled(self, enabled: bool) -> bool:
        """设置主动聊天总开关"""
        if self._data.get("proactive_enabled", False) != enabled:
            self._data["proactive_enabled"] = enabled
            self._save_manager_config()
            return True
        return False

    # 全局记忆管理
    def is_memory_enabled(self) -> bool:
        """全局记忆是否启用（默认开启）。"""
        return self._data.get("memory_enabled", True)

    def set_memory_enabled(self, enabled: bool) -> bool:
        if self._data.get("memory_enabled", True) != enabled:
            self._data["memory_enabled"] = enabled
            self._save_manager_config()
            return True
        return False

    def get_proactive_targets(self) -> List[str]:
        """获取主动聊天目标用户列表"""
        return self._data.get("proactive_targets", [])

    def add_proactive_target(self, user_id: str) -> bool:
        """添加主动聊天目标用户"""
        targets = self._data.setdefault("proactive_targets", [])
        if user_id not in targets:
            targets.append(user_id)
            self._save_manager_config()
            return True
        return False

    def remove_proactive_target(self, user_id: str) -> bool:
        """移除主动聊天目标用户"""
        targets = self._data.get("proactive_targets", [])
        if user_id in targets:
            targets.remove(user_id)
            self._save_manager_config()
            return True
        return False

    def get_proactive_interval(self) -> tuple:
        """获取主动聊天随机间隔范围（分钟）"""
        return (
            self._data.get("proactive_interval_min", 30),
            self._data.get("proactive_interval_max", 90),
        )

    def set_proactive_interval(self, min_minutes: int, max_minutes: int) -> bool:
        """设置主动聊天随机间隔范围（分钟）"""
        if min_minutes <= 0 or max_minutes < min_minutes:
            return False
        self._data["proactive_interval_min"] = int(min_minutes)
        self._data["proactive_interval_max"] = int(max_minutes)
        self._save_manager_config()
        return True

    def get_proactive_cooldown(self) -> int:
        """获取同一用户最短发送间隔（分钟）"""
        return self._data.get("proactive_cooldown", 30)

    def set_proactive_cooldown(self, minutes: int) -> bool:
        """设置同一用户最短发送间隔（分钟）"""
        if minutes < 0:
            return False
        self._data["proactive_cooldown"] = int(minutes)
        self._save_manager_config()
        return True

    def get_proactive_quiet_hours(self) -> tuple:
        """获取免打扰时段（开始小时, 结束小时）"""
        return (
            self._data.get("proactive_quiet_start", 0),
            self._data.get("proactive_quiet_end", 8),
        )

    def set_proactive_quiet_hours(self, start_hour: int, end_hour: int) -> bool:
        """设置免打扰时段（0-23 小时，支持跨天如 23-7）"""
        if not (0 <= start_hour <= 23 and 0 <= end_hour <= 23):
            return False
        self._data["proactive_quiet_start"] = int(start_hour)
        self._data["proactive_quiet_end"] = int(end_hour)
        self._save_manager_config()
        return True

    # ===== 魔改新增：主动聊天群聊 =====
    def get_proactive_groups(self) -> List[str]:
        """获取主动聊天群聊列表"""
        return self._data.get("proactive_groups", [])

    def add_proactive_group(self, group_id: str) -> bool:
        """添加主动聊天群聊"""
        groups = self._data.setdefault("proactive_groups", [])
        if group_id not in groups:
            groups.append(group_id)
            self._save_manager_config()
            return True
        return False

    def remove_proactive_group(self, group_id: str) -> bool:
        """移除主动聊天群聊"""
        groups = self._data.get("proactive_groups", [])
        if group_id in groups:
            groups.remove(group_id)
            self._save_manager_config()
            return True
        return False

    # ===== 魔改新增：群聊搭话 =====
    def is_interject_enabled(self) -> bool:
        """检查群聊搭话是否开启"""
        return self._data.get("interject_enabled", False)

    def set_interject_enabled(self, enabled: bool) -> bool:
        """设置群聊搭话总开关"""
        if self._data.get("interject_enabled", False) != enabled:
            self._data["interject_enabled"] = enabled
            self._save_manager_config()
            return True
        return False

    def get_interject_probability(self) -> float:
        """获取搭话概率"""
        return self._data.get("interject_probability", 0.3)

    def set_interject_probability(self, probability: float) -> bool:
        """设置搭话概率（0.0-1.0）"""
        if not 0 <= probability <= 1:
            return False
        if self._data.get("interject_probability", 0.3) != probability:
            self._data["interject_probability"] = probability
            self._save_manager_config()
            return True
        return False

    def get_interject_interval(self) -> tuple:
        """获取搭话随机间隔范围（分钟）"""
        return (
            self._data.get("interject_interval_min", 30),
            self._data.get("interject_interval_max", 90),
        )

    def set_interject_interval(self, min_minutes: int, max_minutes: int) -> bool:
        """设置搭话随机间隔范围（分钟）"""
        if min_minutes <= 0 or max_minutes < min_minutes:
            return False
        self._data["interject_interval_min"] = int(min_minutes)
        self._data["interject_interval_max"] = int(max_minutes)
        self._save_manager_config()
        return True

    def get_interject_history_count(self) -> int:
        """获取搭话时读取的最近消息条数"""
        return self._data.get("interject_history_count", 10)

    def set_interject_history_count(self, count: int) -> bool:
        """设置搭话时读取的最近消息条数"""
        if count <= 0:
            return False
        self._data["interject_history_count"] = int(count)
        self._save_manager_config()
        return True

    # ===== 魔改新增：群聊免@会话 =====
    def get_session_expire_seconds(self) -> int:
        """获取免@会话有效期（秒）；非法值回落到默认 30。"""
        return session_policy.clamp_expire_seconds(
            self._data.get("session_expire_seconds", session_policy.DEFAULT_SESSION_SECONDS))

    def set_session_expire_seconds(self, seconds: int) -> bool:
        """设置免@会话有效期（秒，夹到 30~300）。"""
        value = session_policy.clamp_expire_seconds(seconds)
        if value == self.get_session_expire_seconds():
            return False
        self._data["session_expire_seconds"] = value
        self._save_manager_config()
        return True

    def get_session_mode(self) -> str:
        """免@连续对话的判断模式：timeout（默认）/ api。"""
        return session_policy.normalize_mode(self._data.get("session_mode", ""))

    def set_session_mode(self, mode: str) -> bool:
        """切换免@判断模式；认不出的模式一律拒绝（保持原值）。"""
        text = str(mode or "").strip().lower()
        if text not in session_policy.MODES:
            return False
        if text == self.get_session_mode():
            return False
        self._data["session_mode"] = text
        self._save_manager_config()
        return True

    def get_session_unrelated_limit(self) -> int:
        """获取连续无关消息条数上限"""
        return self._data.get("session_unrelated_limit", 5)

    def set_session_unrelated_limit(self, count: int) -> bool:
        """设置连续无关消息条数上限"""
        if count <= 0:
            return False
        self._data["session_unrelated_limit"] = int(count)
        self._save_manager_config()
        return True

    def is_group_auto_participate_enabled(self) -> bool:
        """检查旧版群聊随机参与是否开启"""
        return self._data.get("group_auto_participate", False)

    def set_group_auto_participate(self, enabled: bool) -> bool:
        """设置旧版群聊随机参与开关"""
        if self._data.get("group_auto_participate", False) != enabled:
            self._data["group_auto_participate"] = enabled
            self._save_manager_config()
            return True
        return False

# 全局管理器实例
chat_manager = ChatManager()
