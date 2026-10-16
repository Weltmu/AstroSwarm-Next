"""AgentRuntime：统一工具注册表 + 内置工具 + 工具包加载。"""
import os
import time
from pathlib import Path

from .registry import ToolRegistry
from .tool import ToolContext, ToolSpec

_runtime = None


def _load_plugin_module(name: str):
    """把插件里的实现借过来用（跨平台发送 / 自学技能）。

    2026-10-12：以前写死 `qbotmanager.assets.plugins.ai.X`，而机器人侧的 qbotmanager
    副本**只同步 core/、没有 assets/** → 机器人里这句必然 ModuleNotFoundError，
    症状是"跨平台发送永远发不出去，只回一句找不到 qbotmanager.assets 模块"。
    现在按顺序尝试，最能用的是机器人**已经加载好的插件模块**（不会重复注册 matcher）。
    """
    import importlib

    # 顺序很关键（2026-10-12 踩过）：**优先用机器人已经加载好的插件模块**。
    # 走 qbotmanager.assets.* 那份副本时，nonebot_plugin_localstore 认不出"调用方插件"
    # （它的 get_plugin_config_file 是靠调用栈判断插件名的），直接抛
    # "Cannot detect caller plugin" —— 表现就是跨平台发送永远发不出去。
    for mod in (f"src.plugins.ai.{name}",
                f"plugins.ai.{name}",
                f"qbotmanager.assets.plugins.ai.{name}"):
        try:
            return importlib.import_module(mod)
        except Exception:  # noqa: BLE001 —— 换下一个候选
            continue
    return None


class AgentRuntime:
    def __init__(self):
        self.registry = ToolRegistry()
        self._register_builtin()

    def _register_builtin(self):
        self.registry.register(
            ToolSpec(
                name="get_current_time",
                description="获取当前日期和时间",
                parameters={"type": "object", "properties": {}, "required": []},
                permissions=[],
                handler=lambda ctx, args: time.strftime("%Y-%m-%d %H:%M:%S %A"),
            )
        )

        def _knowledge_search(ctx, args):
            import json as _json

            from .. import knowledge_base

            query = str(args.get("query") or "").strip()
            root = os.environ.get("ASTROSWARM_KNOWLEDGE_DIR", "")
            results = knowledge_base.search_dir(root, query, top_k=3) if root else []
            return _json.dumps({"ok": True, "results": results},
                               ensure_ascii=False)

        self.registry.register(
            ToolSpec(
                name="knowledge_search",
                description="从本地知识库检索文档片段（用户上传的 txt/md），返回带出处的结果；"
                            "用于回答用户自己文档里的问题",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索问题"},
                    },
                    "required": ["query"],
                },
                permissions=[],
                handler=_knowledge_search,
            )
        )

        def _learn_skill(ctx, args):
            """聊天里"自学"：不会的事，当场让工坊给自己写个插件（异步，几分钟）。

            只让主人/管理员触发（防群里有人烧 token）；真正的生成放到回合结束后跑，
            所以这里只入队、立刻返回，让 AI 能先回一句"我去学一下"。
            """
            import json as _json

            need = str(args.get("need") or "").strip()
            if not need:
                return _json.dumps({"ok": False, "error": "empty_need"},
                                   ensure_ascii=False)
            scene = str(getattr(ctx, "scene", "") or "")
            sender = str(getattr(ctx, "user_id", "") or "")
            if not getattr(ctx, "is_superuser", False):
                return _json.dumps({
                    "ok": False, "error": "not_allowed",
                    "hint": "只有机器人主人能让它自己写插件；"
                            "你可以把需求告诉他，或者让他教你手动装能力包",
                }, ensure_ascii=False)
            try:
                skill_forge = _load_plugin_module("skill_forge")
                if skill_forge is None:
                    raise ImportError(
                        "插件里的 skill_forge 没找到（qbotmanager.assets 与 src.plugins.ai 都没有）")
            except Exception:  # noqa: BLE001 —— 插件侧拿不到就直接说不行
                return _json.dumps({"ok": False, "error": "forge_unavailable"},
                                   ensure_ascii=False)
            result = skill_forge.enqueue(need, getattr(ctx, "channel", None), sender, scene)
            result.setdefault("note", "已经排上队：装上后我会主动回你一句")
            return _json.dumps(result, ensure_ascii=False)

        self.registry.register(
            ToolSpec(
                name="learn_skill",                description="当用户要你做的事你做不到、而这件事能用现有能力拼出来时，"
                            "用这个给自己现写一个插件（几分钟，装好会自动告诉你）。"
                            "如果缺的是主程序层面的能力（跨平台发消息、撤回、禁言等），"
                            "不要调用它 —— 直接如实告诉用户你做不到、缺什么。",
                parameters={
                    "type": "object",
                    "properties": {
                        "need": {"type": "string",
                                 "description": "用一句大白话描述要学的能力，例如"
                                                "「每天早八播报一次天气」"},
                    },
                    "required": ["need"],
                },
                permissions=[],
                handler=_learn_skill,
            )
        )

        # ---------------- 跨平台互通（内置基础功能，2026-10-04 老板要求不做成插件）----
        def _cross():
            cross_platform = _load_plugin_module("cross_platform")
            if cross_platform is None:
                raise ImportError(
                    "插件里的 cross_platform 没找到（qbotmanager.assets 与 src.plugins.ai 都没有）")
            return cross_platform

        _FALLBACK_EXPLAINED: set = set()   # 已经解释过收不了媒体的平台

        def _subject(ctx) -> str:
            extra = getattr(ctx, "extra", None) or {}
            return str(extra.get("user_key")
                       or getattr(ctx, "user_key", "")
                       or f"{getattr(ctx, 'platform', '')}:{getattr(ctx, 'user_id', '')}")

        def _send_to_my_platform(ctx, args):
            """把一句话发到"这个人自己的另一个平台"（如微信 → 他的 QQ）。"""
            import json as _json

            platform = str(args.get("platform") or "").strip().lower()
            text = str(args.get("text") or "").strip()
            if not platform or not text:
                return _json.dumps({"ok": False, "error": "missing_platform_or_text"},
                                   ensure_ascii=False)
            try:
                out = _cross().enqueue(ctx, _subject(ctx), platform, "text", text=text)
            except Exception as e:  # noqa: BLE001
                return _json.dumps({"ok": False, "error": "unavailable",
                                    "detail": str(e)[:120]}, ensure_ascii=False)
            return _json.dumps(out, ensure_ascii=False)

        def _send_file_to_my_platform(ctx, args):
            """把图片/语音/视频/文件发到"这个人自己的另一个平台"。"""
            import json as _json

            platform = str(args.get("platform") or "").strip().lower()
            source = str(args.get("file") or args.get("url") or args.get("path") or "").strip()
            kind = str(args.get("kind") or "file").strip().lower()
            name = str(args.get("name") or "").strip()
            if not platform or not source:
                return _json.dumps({"ok": False, "error": "missing_platform_or_file"},
                                   ensure_ascii=False)
            try:
                _cross_mod = _cross()
                # 2026-10-15 安全闸：转发的是"本地文件"时，先过 media_guard 的来源预筛
                # （本地只认机器人自己的产出目录，字面量内网地址直接拒）。以前这里
                # 填什么转什么 —— 群里一句提示注入就能把本机文件转出去。权威检查在
                # 投递前（cross_platform._send）还会再跑一次，那次会解析域名。
                _allow_local = bool(getattr(ctx, "is_superuser", False))
                try:
                    # 按 _load_plugin_module 的老规矩拿模块（写死 qbotmanager.assets.* 在
                    # 机器人侧必然 ModuleNotFoundError —— 2026-10-12 踩过同一个坑）
                    _guard = _load_plugin_module("media_guard")
                except Exception:  # noqa: BLE001 —— 拿不到就当没有（下面的 _send 还会拦）
                    _guard = None
                if _guard is not None:
                    _src, _why, _hint = _guard.precheck_source(
                        source, allow_any_local=_allow_local)
                    if _why:
                        return _json.dumps(
                            {"ok": False, "error": _why, "detail": _hint},
                            ensure_ascii=False)
                    source = _src
                # 先查通道能力（微信只能发文字），别等投递才失败
                try:
                    if not _cross_mod.supports(platform, kind):
                        # 软件侧同样降级：**首次解释一句，之后只给链接**（2026-10-12 老板定的）
                        _label = _cross_mod.platform_name(platform)
                        try:
                            _first = platform not in _FALLBACK_EXPLAINED
                            _FALLBACK_EXPLAINED.add(platform)
                            if _first:
                                _bridge = (f"（这段是转发的）：{_label}这边收不了「{kind}」，"
                                           f"先给你个链接：{source}")
                            else:
                                _bridge = f"转发的链接：{source}"
                            _res = _cross_mod.enqueue(
                                ctx, _subject(ctx), platform, "text", text=_bridge)
                            if isinstance(_res, dict):
                                _res["fallback"] = "text_with_link"
                                _res["first_time"] = _first
                                _res["hint"] = (
                                    f"{_label}收不了{kind}，已把链接发过去。"
                                    + ("第一次：可以解释一句，以后别再解释。" if _first
                                       else "解释过了：只提一句链接发过去了就行。"))
                            return _json.dumps(_res, ensure_ascii=False)
                        except Exception:  # noqa: BLE001
                            return _json.dumps(
                                {"ok": False, "error": "platform_cannot",
                                 "hint": "这条通道收不了这类内容（微信现在只能发文字）。"},
                                ensure_ascii=False)
                except AttributeError:
                    pass
                out = _cross_mod.enqueue(ctx, _subject(ctx), platform, kind,
                                         source=source, name=name,
                                         # 别漏这个：enqueue 内部还会再跑一次来源预筛，
                                         # 不带就等于"上面放行了、这里又被拒"（管理员
                                         # 本机文件转发永远发不出去，和 tools_bridge 那条
                                         # 路的行为对不上）—— 2026-10-15 审计发现
                                         allow_any_local=_allow_local)
            except Exception as e:  # noqa: BLE001
                return _json.dumps({"ok": False, "error": "unavailable",
                                    "detail": str(e)[:120]}, ensure_ascii=False)
            return _json.dumps(out, ensure_ascii=False)

        def _my_platforms(ctx, args):
            """这个人绑了哪些平台、各自能收什么（只读，立即答）。"""
            import json as _json

            try:
                cross = _cross()
                subject = _subject(ctx)
                return _json.dumps({"ok": True,
                                    "platforms": cross.other_platforms(subject),
                                    "summary": cross.describe_capabilities(subject)},
                                   ensure_ascii=False)
            except Exception as e:  # noqa: BLE001
                return _json.dumps({"ok": False, "error": str(e)[:120]},
                                   ensure_ascii=False)

        self.registry.register(
            ToolSpec(
                name="send_to_my_platform",
                description="把一句话发到**当前这位用户自己的另一个聊天平台**"
                            "（例如他在微信上说「发到我 QQ」）。目标只能是"
                            "他本人已绑定的平台；他没绑定就如实告诉他先发「绑定」。"
                            "给群里别人发消息不要用这个。",
                parameters={
                    "type": "object",
                    "properties": {
                        "platform": {"type": "string",
                                     "description": "目标平台：qq / qq_official / wechat"},
                        "text": {"type": "string", "description": "要发的内容"},
                    },
                    "required": ["platform", "text"],
                },
                permissions=[],
                handler=_send_to_my_platform,
            )
        )
        self.registry.register(
            ToolSpec(
                name="send_file_to_my_platform",
                description="把他发来的（或你手上有的）图片/语音/视频/文件发到"
                            "**他本人的另一个平台**。file 可以是网址或本地路径；"
                            "kind 填 image / voice / video / file。"
                            "平台收不了这类内容时会如实说，不会假装成功。",
                parameters={
                    "type": "object",
                    "properties": {
                        "platform": {"type": "string",
                                     "description": "目标平台：qq / qq_official / wechat"},
                        "file": {"type": "string", "description": "文件网址或本地路径"},
                        "kind": {"type": "string",
                                 "description": "image / voice / video / file（默认 file）"},
                        "name": {"type": "string", "description": "可选：显示的文件名"},
                    },
                    "required": ["platform", "file"],
                },
                permissions=[],
                handler=_send_file_to_my_platform,
            )
        )
        self.registry.register(
            ToolSpec(
                name="my_platforms",
                description="查这位用户绑定了哪些平台、各能收什么（用户问「你能发到哪」"
                            "或你拿不准能不能跨平台时先查它）。",
                parameters={"type": "object", "properties": {}, "required": []},
                permissions=[],
                handler=_my_platforms,
            )
        )

    def load_packs(self, packs_dir, allowed=None) -> int:
        packs_dir = Path(packs_dir)
        if not packs_dir.exists():
            return 0
        total = 0
        for pack in sorted(packs_dir.iterdir()):
            if (pack / "manifest.json").exists():
                if allowed is not None:
                    try:
                        import json

                        mid = json.loads(
                            (pack / "manifest.json").read_text(encoding="utf-8")
                        ).get("id")
                    except Exception:  # noqa: BLE001
                        continue
                    if mid not in allowed:
                        continue  # 冻结：不加载，但保留文件
                try:
                    total += self.registry.load_pack(pack)
                except Exception:  # noqa: BLE001
                    continue
        return total

    def schemas(self, platform: str = "") -> list:
        """按通道裁剪的工具清单（platform 为空 = 全部，旧行为）。"""
        return self.registry.schemas(platform)

    def packs(self) -> dict:
        return self.registry.packs()

    def pack_meta(self, pack_id: str):
        return self.registry.pack_meta(pack_id)

    def tool_names(self) -> set:
        return self.registry.names()

    def behavior(self, behavior_id: str) -> dict | None:
        return self.registry.behavior(behavior_id)

    def behaviors(self) -> dict:
        return self.registry.behaviors()

    def execute(self, name: str, args: dict, ctx: ToolContext) -> str:
        return self.registry.execute(name, args, ctx)

    async def execute_async(self, name: str, args: dict, ctx: ToolContext,
                            host=None, timeout=None) -> str:
        """异步执行入口：沙箱工具走独立子进程，其余与 execute 完全一致。

        host 是主程序提供的动作执行器 ``async def host(action, params, ctx)``，
        沙箱里的插件只能通过它做事（发消息/记忆/联网等），主程序逐条校验。
        """
        spec = self.registry.check(name, ctx)
        if not spec.sandbox:
            return self.execute(name, args, ctx)
        from . import sandbox

        meta = self.registry.pack_meta(spec.pack_id) or {}
        pack_dir = meta.get("dir") or ""
        if not pack_dir:
            raise sandbox.SandboxError(
                f"沙箱工具 {name} 找不到所属插件目录（包 {spec.pack_id}）")
        # 2026-10-15（安全）：子进程只拿**这个包声明过的权限**（manifest ∪ 工具自身），
        # 再与通道权限取交（在 _ctx_payload 里）。以前是"通道给什么就给什么" ——
        # 声明只写 memory 的包在 QQ 群里同样能拿到 group_admin 去禁言/撤回。
        allowed = set(meta.get("permissions") or []) | set(spec.permissions or [])
        try:
            return await sandbox.run_tool(
                Path(pack_dir), spec.name, args or {}, ctx, host, timeout=timeout,
                allowed_perms=allowed)
        except Exception as exc:  # noqa: BLE001
            # 工坊生成的插件在真实使用中挂了：记一条，工坊页能让 AI 按这条报错改一版。
            # 记不下来也不能影响机器人本体，所以整段吞掉异常。
            try:
                from ..workshop import repair as _repair

                _repair.record_for_pack(Path(pack_dir), spec.name, str(exc))
            except Exception:  # noqa: BLE001
                pass
            raise


def get_runtime() -> AgentRuntime:
    global _runtime
    if _runtime is None:
        _runtime = AgentRuntime()
        packs = os.environ.get("ASTROSWARM_TOOL_PACKS", "")
        allowed_raw = os.environ.get("ASTROSWARM_TOOL_PACKS_ALLOWED")
        allowed = (
            None
            if allowed_raw is None
            else {x for x in allowed_raw.split(",") if x}
        )
        if packs:
            _runtime.load_packs(packs, allowed=allowed)
        personas = os.environ.get("ASTROSWARM_PERSONAS", "")
        persona_allowed_raw = os.environ.get("ASTROSWARM_PERSONAS_ALLOWED")
        persona_allowed = (
            None
            if persona_allowed_raw is None
            else {x for x in persona_allowed_raw.split(",") if x}
        )
        if personas:
            _runtime.load_packs(personas, allowed=persona_allowed)
    return _runtime
