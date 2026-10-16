# -*- coding: utf-8 -*-
"""NoneBot 机器人项目骨架：生成 / 插件列表读写 / 启停。"""
import json
import logging
import re
import shutil
import sys
from pathlib import Path

from ..constants import AI_PLUGIN_DEPS
from . import license as lic_mod
from .process import start_process, stop_process_tree
from .process import run_capture
from .python_runtime import install_with_fallback

logger = logging.getLogger("qbotmanager")

PYPROJECT_TPL = '''[project]
name = "qbot"
version = "1.1.0"
description = "由 星巢 Nest 管理的多平台机器人"
requires-python = ">=3.10"
dependencies = []

[tool.nonebot]
plugin_dirs = ["src/plugins"]
adapters = [
{adapters}
]
'''

RUN_PY_TPL = '''import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中：
# 内置 Python 的 _pth 机制不会自动加入脚本目录，缺少此行会导致
# 使用 src.plugins.xxx 绝对导入的插件报 "No module named 'src'"
sys.path.insert(0, str(Path(__file__).resolve().parent))

import nonebot

nonebot.init()

driver = nonebot.get_driver()
{adapter_imports}
{adapter_registers}

nonebot.load_from_toml("pyproject.toml")

if __name__ == "__main__":
    nonebot.run()
'''


def _onebot_active(settings) -> bool:
    """第三方 OneBot 通道是否启用。

    反向 WS 模式不依赖正向端口即可生效（协议端主动连回星群）；
    正向模式要求协议端端口已填写。
    """
    return getattr(settings, "qq_channel", "official") == "onebot" and (
        str(getattr(settings, "qq_onebot_mode", "") or "reverse").lower() == "reverse"
        or int(getattr(settings, "qq_onebot_port", 0) or 0) > 0
    )


def _enabled_qq_adapters(settings) -> list:
    """按设置返回要注册的适配器列表 [(显示名, 模块名), ...]。

    QQ 支持两种通道：
    - official：官方机器人（nonebot-adapter-qq）；
    - onebot：第三方 OneBot V11 协议端（nonebot-adapter-onebot，
      AstroSwarm 不内置/不分发协议端，只做标准正向 WS 对接）。
    飞书（feishu）是**并行**的额外通道：开了开关又有凭证就一起注册，
    与 QQ 走哪条通道无关（以前这里只认 QQ，飞书没地方注册）。
    dsh 通道启用时 QQ 消息由 dsh（DeepSeek Harness）接管，
    NoneBot 不再注册 QQ 适配器，避免同一 AppID 双网关连接冲突。
    李清菡智能体启用时由李清菡接管 QQ（与 dsh 互斥，优先李清菡）。

    接入方式优先：用户在面板上选了第三方 OneBot，就必须注册 OneBot 适配器。
    以前 dsh / 智能体档案两个分支排在前面，切到第三方后适配器列表要么为空、
    要么还是官方：协议端连不上，整条通道静默失效（「切了通道机器人不吭声」）。
    """
    adapters = _qq_adapters(settings)
    if _feishu_ready(settings):
        adapters.append(("飞书", "nonebot.adapters.feishu"))
    return adapters


def _feishu_ready(settings) -> bool:
    """飞书开关开了、凭证也填了才算就绪（缺一样都不注册，免得机器人起不来）。"""
    return bool(
        getattr(settings, "feishu_enabled", False)
        and str(getattr(settings, "feishu_app_id", "") or "").strip()
        and str(getattr(settings, "feishu_app_secret", "") or "").strip()
        and str(getattr(settings, "feishu_verification_token", "") or "").strip()
    )


def _qq_adapters(settings) -> list:
    """只算 QQ 那部分适配器（原 _enabled_qq_adapters 的逻辑）。"""
    official_creds = bool(
        getattr(settings, "qq_official_appid", "")
        and getattr(settings, "qq_official_token", "")
        and getattr(settings, "qq_official_secret", "")
    )
    if _onebot_active(settings):
        return [("QQ", "nonebot.adapters.onebot.v11")]
    if _agent_profile_enabled(settings):
        # 档案开着但没填官方凭据：以前这里直接 return []，机器人一个适配器都不加载、
        # 永远收不到消息（用户看到的就是「启动成功但完全没有反应」）。
        # 第三方通道在上面已经接管，这里没有凭据就是真的没东西可连。
        if official_creds:
            return [("QQ", "nonebot.adapters.qq")]
        return []
    if getattr(settings, "dsh_enabled", False):
        # 官方通道 + dsh：QQ 消息由 dsh 网关接管，NoneBot 不注册 QQ 适配器，
        # 避免同一个 AppID 双网关抢线。
        return []
    if getattr(settings, "qq_channel", "official") != "onebot" and official_creds:
        return [("QQ", "nonebot.adapters.qq")]
    return []


def _adapters_toml(settings) -> str:
    """生成 pyproject.toml 的 adapters 块内容。"""
    lines = []
    for name, module in _enabled_qq_adapters(settings):
        lines.append(f'    {{ name = "{name}", module_name = "{module}" }},')
    return "\n".join(lines)


def _run_py_text(settings) -> str:
    """生成 run.py：按设置注册 QQ 适配器（官方或 OneBot；微信 iLink 由插件目录自动发现）。"""
    adapters = _enabled_qq_adapters(settings)
    imports = "\n".join(
        f"from {mod} import Adapter as Adapter{i}"
        for i, (_, mod) in enumerate(adapters)
    )
    registers = "\n".join(
        f"driver.register_adapter(Adapter{i})" for i in range(len(adapters))
    )
    return RUN_PY_TPL.format(adapter_imports=imports, adapter_registers=registers)

def ensure_ilink_adapter(settings) -> None:
    """把内置的 nonebot_adapter_ilink（微信 ClawBot 适配器）同步到机器人插件目录。

    NoneBot 的 plugin_dirs 会自动发现该目录并注册适配器，无需额外配置。
    """
    try:
        bundled = Path(__file__).resolve().parent.parent / "assets" / "plugins" / "nonebot_adapter_ilink"
        if not bundled.is_dir():
            return
        target = settings.plugins_dir / "nonebot_adapter_ilink"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(bundled, target, dirs_exist_ok=True)
    except OSError as e:
        logger.warning("同步 ilink 适配器失败（微信通道将不可用）: %s", e)


def apply_wechat_gate(settings, full: bool = True) -> bool:
    """按功能闸门同步微信 iLink 适配器。

    full=True：正式授权，恢复微信适配器；
    full=False：试用/未激活，移除微信适配器（QQ 通道不受影响）。
    返回微信通道是否可用。
    """
    target = settings.plugins_dir / "nonebot_adapter_ilink"
    if full:
        try:
            ensure_ilink_adapter(settings)
        except OSError as e:
            logger.warning("同步微信适配器失败: %s", e)
        return True
    try:
        if target.exists():
            shutil.rmtree(target)
    except OSError as e:
        logger.warning("移除微信适配器失败: %s", e)
    return False


def ensure_ai_plugin(settings) -> None:
    """把内置 AI 对话插件（assets/plugins/ai）同步到机器人插件目录（不可卸载）。"""
    try:
        bundled = Path(__file__).resolve().parent.parent / "assets" / "plugins" / "ai"
        if not bundled.is_dir():
            return
        target = settings.plugins_dir / "ai"
        target.parent.mkdir(parents=True, exist_ok=True)
        # 不要在客户插件目录里留 .pyc / __pycache__（裸 .pyc 会让 Python 版本一换就炸）
        shutil.copytree(
            bundled, target, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        for pyc in list(target.rglob("__pycache__")):
            shutil.rmtree(pyc, ignore_errors=True)
    except OSError as e:
        logger.warning("同步内置 AI 插件失败: %s", e)


def ensure_agent_runtime(settings, log=None) -> None:
    """把 AgentRuntime 子包同步到机器人目录（bot/qbotmanager）。

    机器人进程是独立 venv，装不进桌面主程序源码；AI 插件里
    `from qbotmanager.core.agent.runtime import get_runtime` 与
    `from qbotmanager.core.agent_profile import channel_capabilities`
    都依赖这个子包（纯标准库，自包含），随启动自愈同步，工具包才能被加载。
    v1.2.8 修：以前漏同步 core/agent_profile.py，机器人一启动 ai 插件就
    ModuleNotFoundError，网关连上了却永远不回消息。
    """
    try:
        import sys as _sys
        if getattr(_sys, "frozen", False) and getattr(_sys, "_MEIPASS", ""):
            # 打包版：qbotmanager 源码在 PYZ 里不在磁盘，改用 appr/ 数据目录的副本
            src_pkg = Path(_sys._MEIPASS) / "appr" / "qbotmanager"
        else:
            src_pkg = Path(__file__).resolve().parent.parent  # src/qbotmanager
        target = settings.bot_dir / "qbotmanager"
        (target / "core" / "agent").mkdir(parents=True, exist_ok=True)
        # ① 包标识文件（必须有，机器人侧按 qbotmanager.xxx 导入）
        for rel in ("__init__.py", "core/__init__.py"):
            src = src_pkg / rel
            if src.exists():
                dst = target / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        # ② **整个 core/ 目录一次性同步**：以前是"点名清单"，每加一个新文件就漏一次
        #    （v1.2.8 漏 core/agent_profile.py、2026-10-03 漏 core/web_fetch.py，
        #    症状都是"网关连上了但机器人永远不回消息"）。整目录复制，从根上不再犯。
        core_src = src_pkg / "core"
        core_dst = target / "core"
        if core_src.is_dir():
            shutil.copytree(core_src, core_dst, dirs_exist_ok=True)
            for pyc in list(core_dst.rglob("__pycache__")):
                shutil.rmtree(pyc, ignore_errors=True)
        agent_src = src_pkg / "core" / "agent"
        agent_dst = target / "core" / "agent"
        if agent_src.is_dir():
            shutil.copytree(agent_src, agent_dst, dirs_exist_ok=True)
        for pyc in list(agent_dst.rglob("__pycache__")):
            shutil.rmtree(pyc, ignore_errors=True)
    except OSError as e:
        logger.warning("同步 AgentRuntime 子包失败（工具包将不可用）: %s", e)
        if log:
            log("同步 AgentRuntime 子包失败: " + str(e))


def selfcheck_ai_plugin(settings, log=None) -> list:
    """机器人启动前自检内置 AI 插件：编译 + 适配器字段是否都对得上。

    为什么要有这一步（2026-10-03 两次事故）：
    - 插件 import 失败（新模块没同步）→ 网关连上了却完全不回话；
    - 适配器给 ChannelContext 传了数据类里没有的字段 → 每条消息都 TypeError，
      同样表现为"连上了不回话"。
    这两种在启动日志里都只是普通报错，用户根本不会去看。这里主动检查一遍，
    有问题就把"人话结论"写进部署日志（客户端会显示），而不是等用户来问。
    返回问题列表（空 = 没问题）。
    """
    import ast
    import dataclasses
    import py_compile

    problems = []
    ai_dir = settings.plugins_dir / "ai"
    if not ai_dir.is_dir():
        return ["内置 AI 插件目录不存在（机器人不会回话）"]

    # ① 编译一遍（语法错误 / 编码错误都会在这里现形）
    for path in sorted(ai_dir.rglob("*.py")):
        try:
            py_compile.compile(str(path), doraise=True, cfile=str(path) + ".selfcheck.pyc")
        except Exception as e:  # noqa: BLE001
            problems.append(f"{path.name} 编译不过：{str(e)[:120]}")
        finally:
            try:
                Path(str(path) + ".selfcheck.pyc").unlink(missing_ok=True)
            except OSError:
                pass

    # ①' 动作表契约（2026-10-12 加）：动作函数必须写在 _ACTIONS **之前**，
    #     给模型看的工具必须有实现 —— 这两类问题都会让"机器人连上了却不回话"，
    #     而启动日志里只是一行普通报错，用户根本看不出来。
    tb_path = ai_dir / "tools_bridge.py"
    try:
        import re as _re

        tb_src = tb_path.read_text(encoding="utf-8-sig")
        at = tb_src.index("\n_ACTIONS = {")
        head_src = tb_src[:at]
        body_start = tb_src.index("{", at)
        depth, cursor = 0, body_start
        while cursor < len(tb_src):
            if tb_src[cursor] == "{":
                depth += 1
            elif tb_src[cursor] == "}":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        body = tb_src[body_start:cursor + 1]
        entries = {}
        for line in body.splitlines():
            m = _re.match(r'\s+"([a-z_]+)":\s*(.+?),?\s*$', line)
            if m:
                entries[m.group(1)] = set(_re.findall(r"([A-Za-z_]\w*)\s*\(", m.group(2)))
        for action, funcs in entries.items():
            for fn in funcs:
                if fn in ("lambda", "dict", "str", "int", "len", "bool"):
                    continue
                if f"def {fn}(" not in head_src:
                    problems.append(
                        f"动作 {action} 用的 {fn}() 定义在动作表之后 → 会 NameError，机器人不回话")
        exposed = set(_re.findall(r'_tool\("([a-z_]+)"', tb_src))
        for name in sorted(exposed - set(entries)):
            problems.append(f"工具 {name} 声明给了模型，但动作表里没有实现 → 模型调了必报错")
    except Exception as e:  # noqa: BLE001
        problems.append(f"tools_bridge.py 契约自检没跑起来：{str(e)[:120]}")

    # ② 适配器传给 ChannelContext 的字段，必须都在数据类里
    #    这里用 AST 直接读 platform.py 的字段名（不 import：机器人进程里 import
    #    插件会带进一堆运行时依赖，得不偿失）
    plat_path = ai_dir / "platform.py"
    fields = set()
    try:
        plat_tree = ast.parse(plat_path.read_text(encoding="utf-8-sig"))
    except Exception as e:  # noqa: BLE001
        return problems + [f"platform.py 读不出来：{str(e)[:120]}"]
    for node in ast.walk(plat_tree):
        if isinstance(node, ast.ClassDef) and node.name == "ChannelContext":
            for sub in node.body:
                if isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    fields.add(sub.target.id)
                elif isinstance(sub, ast.Assign):
                    for tgt in sub.targets:
                        if isinstance(tgt, ast.Name):
                            fields.add(tgt.id)
    if not fields:
        return problems + ["platform.py 里找不到 ChannelContext 的字段定义"]

    for path in sorted(ai_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name != "ChannelContext":
                continue
            for kw in node.keywords:
                if kw.arg and kw.arg not in fields:
                    problems.append(
                        f"{path.name}:{node.lineno} 传了 ChannelContext 没有的字段 "
                        f"`{kw.arg}`（消息一来就会报错、机器人不回话）")

    if log:
        if problems:
            for item in problems[:5]:
                log("⚠ AI 插件自检没过：" + item)
        else:
            log("AI 插件自检通过")
    return problems


def ensure_bridge_client(settings) -> None:
    """把微信桥客户端插件（qbm-bridge-client）同步到机器人插件目录（不可卸载）。"""
    try:
        bundled = Path(__file__).resolve().parent.parent / "assets" / "plugins" / "qbm_bridge_client"
        if not bundled.is_dir():
            return
        target = settings.plugins_dir / "qbm_bridge_client"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(bundled, target, dirs_exist_ok=True)
    except OSError as e:
        logger.warning("同步微信桥客户端失败: %s", e)


def ensure_liqinghan_plugin(settings) -> None:
    """把李清菡智能体插件（assets/plugins/liqinghan）同步到机器人插件目录。"""
    try:
        bundled = Path(__file__).resolve().parent.parent / "assets" / "plugins" / "liqinghan"
        if not bundled.is_dir():
            return
        target = settings.plugins_dir / "liqinghan"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(bundled, target, dirs_exist_ok=True)
    except OSError as e:
        logger.warning("同步李清菡智能体插件失败: %s", e)


def ensure_session_pack(settings) -> None:
    """把随软件自带的「连续对话能力包」装到 <机器人根>/tool_packs/session-chat。

    为什么随软件自带：免@连续对话的开关/模式/有效期是这个包的参数，包不在，
    用户在「已装能力包 → 配置」里就没得调。市场（astroswarm.cn）上架前，先让
    自带包里那三个文件到位，用户一打开就能看到并改参数。
    **已经装过就不覆盖用户的 config.json**（那是他自己调的值）。
    """
    try:
        bundled = (Path(__file__).resolve().parent.parent
                   / "assets" / "tool_packs" / "session-chat")
        if not bundled.is_dir():
            return
        target = Path(settings.root) / "tool_packs" / "session-chat"
        target.mkdir(parents=True, exist_ok=True)
        for item in bundled.iterdir():
            dest = target / item.name
            if item.name == "config.json" and dest.exists():
                continue        # 保住用户自己调的参数
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
    except OSError as e:
        logger.warning("同步连续对话能力包失败: %s", e)


def ensure_ai_deps(settings, log=None) -> None:
    """确保内置 AI 插件依赖（openai / mcp）已安装；缺失时自动补装（老部署自愈）。"""
    py = settings.python_exe
    if not py.exists():
        return
    try:
        rc, _ = run_capture([str(py), "-m", "pip", "show", "openai", "mcp", "PyJWT", "cryptography"], timeout=60)
    except Exception:  # noqa: BLE001
        rc = 1
    if rc == 0:
        return
    if log:
        log("内置 AI 插件依赖缺失，正在自动安装（openai / mcp）...")
    try:
        install_with_fallback(settings, list(AI_PLUGIN_DEPS), log=log)
        if log:
            log("AI 插件依赖安装完成")
    except Exception as e:  # noqa: BLE001
        logger.warning("AI 插件依赖安装失败: %s", e)
        if log:
            log("AI 插件依赖安装失败: " + str(e))


def ensure_official_qq_deps(settings, log=None) -> None:
    """确保官方 QQ 适配器（nonebot-adapter-qq）已安装；缺失时自动补装。"""
    py = settings.python_exe
    if not py.exists():
        return
    try:
        rc, _ = run_capture([str(py), "-m", "pip", "show", "nonebot-adapter-qq"], timeout=60)
    except Exception:  # noqa: BLE001
        rc = 1
    if rc == 0:
        return
    if log:
        log("官方 QQ 适配器缺失，正在自动安装（nonebot-adapter-qq）...")
    try:
        install_with_fallback(settings, ["nonebot-adapter-qq"], log=log)
        if log:
            log("官方 QQ 适配器安装完成")
    except Exception as e:  # noqa: BLE001
        logger.warning("官方 QQ 适配器安装失败: %s", e)
        if log:
            log("官方 QQ 适配器安装失败: " + str(e))


def ensure_feishu_deps(settings, log=None) -> bool:
    """确保飞书适配器（nonebot-adapter-feishu）已安装；缺失时自动补装。

    只飞书通道真正要用时才装（开了开关 + 凭证齐），不占别人磁盘。
    返回装好了没有 —— 客户端的飞书页据此显示"还差适配器"。
    """
    py = settings.python_exe
    if not py.exists():
        return False
    try:
        rc, _ = run_capture([str(py), "-m", "pip", "show", "nonebot-adapter-feishu"], timeout=60)
    except Exception:  # noqa: BLE001
        rc = 1
    if rc == 0:
        return True
    if log:
        log("飞书适配器缺失，正在自动安装（nonebot-adapter-feishu）...")
    try:
        install_with_fallback(settings, ["nonebot-adapter-feishu"], log=log)
    except Exception as e:  # noqa: BLE001
        logger.warning("飞书适配器安装失败: %s", e)
        if log:
            log("飞书适配器安装失败：" + str(e))
        return False
    try:
        rc, _ = run_capture([str(py), "-m", "pip", "show", "nonebot-adapter-feishu"], timeout=60)
    except Exception:  # noqa: BLE001
        rc = 1
    if log:
        log("飞书适配器安装完成" if rc == 0 else "飞书适配器装完但校验没通过")
    return rc == 0


def feishu_adapter_installed(settings) -> bool:
    """飞书适配器装没装（给界面显示状态用，不触发安装）。"""
    py = settings.python_exe
    if not py.exists():
        return False
    try:
        rc, _ = run_capture([str(py), "-m", "pip", "show", "nonebot-adapter-feishu"], timeout=60)
    except Exception:  # noqa: BLE001
        return False
    return rc == 0


def ensure_onebot_deps(settings, log=None) -> None:
    """确保 OneBot V11 适配器（nonebot-adapter-onebot）已安装；缺失时自动补装。"""
    py = settings.python_exe
    if not py.exists():
        return
    try:
        rc, _ = run_capture([str(py), "-m", "pip", "show", "nonebot-adapter-onebot"], timeout=60)
    except Exception:  # noqa: BLE001
        rc = 1
    if rc == 0:
        return
    if log:
        log("OneBot 适配器缺失，正在自动安装（nonebot-adapter-onebot）...")
    try:
        install_with_fallback(settings, ["nonebot-adapter-onebot"], log=log)
        if log:
            log("OneBot 适配器安装完成")
    except Exception as e:  # noqa: BLE001
        logger.warning("OneBot 适配器安装失败: %s", e)
        if log:
            log("OneBot 适配器安装失败: " + str(e))


def build_env_text(settings) -> str:
    su = json.dumps(settings.superusers, ensure_ascii=False)
    reverse_onebot = _onebot_active(settings) and str(
        getattr(settings, "qq_onebot_mode", "") or "reverse"
    ).lower() == "reverse"
    listen_host = "127.0.0.1"
    if reverse_onebot:
        listen_host = str(
            getattr(settings, "qq_onebot_listen_host", "") or "127.0.0.1"
        ).strip() or "127.0.0.1"
    lines = [
        "ENVIRONMENT=prod\n"
        f"HOST={listen_host}\n"
        f"PORT={settings.nonebot_port}\n"
        "DRIVER=~fastapi+~httpx+~websockets\n"
        f"SUPERUSERS={su}\n"
        "COMMAND_START=[\"\"]\n"
        "LOG_LEVEL=INFO\n"
        "LOCALSTORE_USE_CWD=true\n"
    ]
    if (
        getattr(settings, "qq_channel", "official") != "onebot"
        and getattr(settings, "qq_official_appid", "")
        and getattr(settings, "qq_official_token", "")
        and getattr(settings, "qq_official_secret", "")
    ):
        lines.append(
            "QQ_IS_SANDBOX="
            + ("true" if getattr(settings, "qq_official_sandbox", False) else "false")
            + "\n"
        )
        bots = [{
            "id": settings.qq_official_appid,
            "token": settings.qq_official_token,
            "secret": settings.qq_official_secret,
            "intent": {"c2c_group_at_messages": True},
        }]
        lines.append(
            "QQ_BOTS=" + json.dumps(bots, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
    # 飞书通道（G4，2026-10-02）：把凭证写进 .env，适配器（nonebot-adapter-feishu）
    # 读 FEISHU_BOTS 启动。凭证不全或没开开关就不写 —— 免得机器人起不来。
    if (
        getattr(settings, "feishu_enabled", False)
        and str(getattr(settings, "feishu_app_id", "") or "").strip()
        and str(getattr(settings, "feishu_app_secret", "") or "").strip()
    ):
        feishu_bot = {
            "app_id": str(settings.feishu_app_id).strip(),
            "app_secret": str(settings.feishu_app_secret).strip(),
            "verification_token": str(
                getattr(settings, "feishu_verification_token", "") or "").strip(),
        }
        encrypt = str(getattr(settings, "feishu_encrypt_key", "") or "").strip()
        if encrypt:
            feishu_bot["encrypt_key"] = encrypt
        lines.append(
            "FEISHU_BOTS=" + json.dumps([feishu_bot], ensure_ascii=False,
                                        separators=(",", ":")) + "\n"
        )
    if _onebot_active(settings):
        token = str(getattr(settings, "qq_onebot_token", "") or "").strip()
        if reverse_onebot:
            # 反向 WS：协议端主动连回星群，NoneBot 只监听，无需写 ONEBOT_WS_URLS
            if token:
                lines.append(f"ONEBOT_ACCESS_TOKEN={token}\n")
        else:
            host = str(getattr(settings, "qq_onebot_host", "") or "127.0.0.1").strip()
            port = int(getattr(settings, "qq_onebot_port", 0) or 0)
            path = str(getattr(settings, "qq_onebot_path", "") or "/onebot/v11/ws").strip()
            if not path.startswith("/"):
                path = "/" + path
            url = f"ws://{host}:{port}{path}"
            lines.append(
                "ONEBOT_WS_URLS=" + json.dumps([url], ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            if token:
                lines.append(f"ONEBOT_ACCESS_TOKEN={token}\n")
    if _agent_profile_enabled(settings):
        from . import ai_config
        llm = ai_config.current_config(settings)
        lines.append("QBM_AGENT_PROFILE_ENABLED=1\n")
        lines.append(f"QBM_AGENT_DATA_DIR={str(settings.root / 'data' / 'liqinghan').replace(chr(92), '/')}\n")
        lines.append(f"QBM_AGENT_LOG_FILE={str(settings.logs_dir / 'liqinghan.log').replace(chr(92), '/')}\n")
        owner = str(getattr(settings, "agent_owner_openid", "") or "").strip()
        lines.append(f"QBM_AGENT_OWNER_QQ={owner}\n")
        words = list(getattr(settings, "agent_wake_words", []) or [])
        if words:
            lines.append("QBM_AGENT_ADDRESS_WORDS=" + ",".join(words) + "\n")
        if str(llm.get("api_url") or ""):
            lines.append(f"DEEPSEEK_API_URL={str(llm['api_url']).rstrip('/')}\n")
        if str(llm.get("api_key") or ""):
            lines.append(f"DEEPSEEK_API_KEY={llm['api_key']}\n")
        if str(llm.get("model") or ""):
            lines.append(f"DEEPSEEK_MODEL={llm['model']}\n")
        if str(getattr(settings, "vision_api_key", "") or ""):
            lines.append(f"SILICONFLOW_API_KEY={settings.vision_api_key}\n")
        if str(getattr(settings, "vision_api_url", "") or ""):
            lines.append(f"VISION_API_URL={str(settings.vision_api_url).rstrip('/')}\n")
        if str(getattr(settings, "vision_model", "") or ""):
            lines.append(f"VISION_MODEL={settings.vision_model}\n")
    return "".join(lines)


def _agent_profile_enabled(settings) -> bool:
    """李清菡档案启用（其他档案不注入李清菡插件配置）。"""
    return bool(getattr(settings, "agent_profile_enabled", False)) and (
        getattr(settings, "agent_profile_id", "") == "liqinghan")


def apply_channel_conflicts(settings) -> list:
    """通道互斥收敛：切到第三方 OneBot 时关掉只吃官方通道的能力。

    返回被自动关闭的能力名（给提示文案用），不改就别返回。

    dsh 只支持 QQ 官方通道（配置里直接写 appId/appSecret，没有协议端那条路）。
    切到第三方后如果 dsh 还挂着「启用」标记：start_all 照旧把 dsh 拉起来连官方网关，
    而第三方协议端连不上，用户看到的就是「切了通道机器人不吭声、官方那个号还在回话」。
    所以保存通道配置时把 dsh 关掉——安装、模型、人设都保留，切回官方重新勾选即可。
    """
    if str(getattr(settings, "qq_channel", "") or "").lower() != "onebot":
        return []
    if not getattr(settings, "dsh_enabled", False):
        return []
    settings.dsh_enabled = False
    return ["dsh"]


def sync_qq_adapters(settings, log=None) -> None:
    """按当前设置重写 pyproject.toml 的 adapters 列表与 run.py（保留插件列表）。"""
    if not (settings.bot_dir / "pyproject.toml").exists():
        scaffold_bot(settings)
        return
    pf = settings.bot_dir / "pyproject.toml"
    text = pf.read_text(encoding="utf-8")
    new_block = "adapters = [\n" + _adapters_toml(settings) + "\n]\n"
    marker = "[tool.nonebot]"
    idx = text.find(marker)
    if idx == -1:
        text += "\n[tool.nonebot]\n" + new_block
    else:
        rest = text[idx + len(marker):]
        am = re.search(r"adapters\s*=\s*\[(.*?)\]", rest, re.S)
        if am:
            text = text[:idx + len(marker) + am.start()] + new_block + text[idx + len(marker) + am.end():]
        else:
            insert_at = idx + len(marker) + 1
            text = text[:insert_at] + "\n" + new_block + text[insert_at:]
    pf.write_text(text, encoding="utf-8")
    (settings.bot_dir / "run.py").write_text(_run_py_text(settings), encoding="utf-8")
    if log:
        log("已同步 QQ 适配器配置")


def scaffold_bot(settings, log=None) -> None:
    """生成机器人项目骨架（pyproject.toml / run.py / .env / src/plugins）。"""
    if log:
        log("生成机器人项目骨架 ...")
    settings.bot_dir.mkdir(parents=True, exist_ok=True)
    settings.plugins_dir.mkdir(parents=True, exist_ok=True)
    (settings.bot_dir / "pyproject.toml").write_text(
        PYPROJECT_TPL.format(adapters=_adapters_toml(settings)), encoding="utf-8"
    )
    (settings.bot_dir / "run.py").write_text(_run_py_text(settings), encoding="utf-8")
    (settings.bot_dir / ".env").write_text(build_env_text(settings), encoding="utf-8")
    (settings.plugins_dir / ".gitkeep").write_text("", encoding="utf-8")
    ensure_agent_runtime(settings, log=log)
    ensure_ilink_adapter(settings)
    ensure_ai_plugin(settings)
    ensure_bridge_client(settings)
    ensure_liqinghan_plugin(settings)
    # 连续对话能力包：桌面端 start_bot 里也调一次，这里是给无头端（Linux）用的
    # —— 无头端部署只走 scaffold_bot，不调它的话 Linux 用户就没有这个包可调参（G5）。
    ensure_session_pack(settings)


def write_env(settings) -> None:
    settings.bot_dir.mkdir(parents=True, exist_ok=True)
    (settings.bot_dir / ".env").write_text(build_env_text(settings), encoding="utf-8")


def read_plugins(settings) -> list:
    """读取 pyproject.toml 中 [tool.nonebot] 下的 plugins 列表。"""
    pf = settings.bot_dir / "pyproject.toml"
    if not pf.exists():
        return []
    text = pf.read_text(encoding="utf-8")
    m = re.search(r"\[tool\.nonebot\](.*?)(\n\[[^\]]+\]|\Z)", text, re.S)
    if not m:
        return []
    sec = m.group(1)
    pm = re.search(r"plugins\s*=\s*\[(.*?)\]", sec, re.S)
    if not pm:
        return []
    try:
        return json.loads("[" + pm.group(1) + "]")
    except ValueError:
        return []


def write_plugins(settings, plugins: list) -> None:
    """把 pip 安装的商店插件名写入 pyproject.toml 的 plugins 列表。"""
    pf = settings.bot_dir / "pyproject.toml"
    if not pf.exists():
        scaffold_bot(settings)
    text = pf.read_text(encoding="utf-8")
    new_line = "plugins = [" + ", ".join(json.dumps(p, ensure_ascii=False) for p in plugins) + "]\n"
    marker = "[tool.nonebot]"
    idx = text.find(marker)
    if idx == -1:
        text += "\n[tool.nonebot]\n" + new_line
    else:
        rest = text[idx + len(marker):]
        pm = re.search(r"plugins\s*=\s*\[(.*?)\]", rest, re.S)
        if pm:
            text = text[:idx + len(marker) + pm.start()] + new_line + text[idx + len(marker) + pm.end():]
        else:
            # 插入到 [tool.nonebot] 节内第一行
            insert_at = idx + len(marker) + 1
            text = text[:insert_at] + "\n" + new_line + text[insert_at:]
    pf.write_text(text, encoding="utf-8")


AI_PLATFORM_KEYS = ("qq", "wechat", "feishu", "telegram")


def live_gates(settings) -> dict:
    """算好的**有效开关**快照（机器人侧热读用，2026-10-11 热重载）。

    为什么要单独落一份快照，而不是让机器人自己读 settings.json：
    平台开关的**有效值**不只是 `settings.ai_platforms` 里那个布尔量 ——
    还要叠加 dsh 统一大脑（微信交给桥）、智能体档案（QQ/微信交给档案）、
    功能闸门（未开通时关微信）。让插件去复刻这套判定，早晚和程序侧对不上。
    所以程序侧算一次、原子写进 ``<bot>/config/ai/astroswarm_live.json``，
    机器人侧每条消息热读它 —— 开关一改就生效，不必重启。
    """
    try:
        env = {}
        if not getattr(settings, "ai_enabled", True):
            env["AI_DISABLED"] = "1"
        else:
            env.update(ai_platform_env(settings))
        data = {
            "ai_disabled": env.get("AI_DISABLED") == "1",
            "platforms": {k: env.get("AI_PLATFORM_" + k.upper(), "1") == "1"
                          for k in AI_PLATFORM_KEYS},
        }
        # 「被叫到称呼就回话」的称呼词（AI 插件热读；2026-10-12 老板报
        # 「群里喊奈姐没回复」）。空列表 = 该功能关闭。
        try:
            data["wake_words"] = [str(w).strip() for w in
                                  (getattr(settings, "agent_wake_words", None) or [])
                                  if str(w).strip()]
        except Exception:  # noqa: BLE001 —— 词表读不到不该挡住开关快照
            pass
        # 档位只是顺带镜像一份（它的权威身份在 memory 模块里，两边读同一份）
        try:
            from . import ai_config as ai_config_mod
            data["memory_level"] = ai_config_mod.read_memory_level_or_off(settings)
            data["memory_enabled"] = data["memory_level"] != "off"
        except Exception:  # noqa: BLE001 —— 档位读不到不该挡住开关快照
            pass
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("计算有效开关快照失败（机器人将回退读环境变量）: %s", exc)
        return {}


def write_live_gates(settings, log=None) -> "Path | None":
    """把有效开关快照写到机器人目录（设置一改就调它 → 立即生效）。"""
    try:
        from . import hotconfig as hotconfig_mod
    except Exception as exc:  # noqa: BLE001
        logger.warning("热配置模块缺失，开关改动要重启才生效: %s", exc)
        return None
    data = live_gates(settings)
    if not data:
        return None
    path = hotconfig_mod.write_live(data, bot_dir=settings.bot_dir)
    if path is not None and log:
        log("已刷新机器人热配置：%s（平台开关/总开关/记忆档位改完立即生效，不用重启）"
            % path)
    return path


def refresh_hot_config(settings, log=None) -> bool:
    """把机器人侧的热配置整个刷一遍：`.env`（主人 QQ / 唤醒词 …）+ 开关快照。

    界面上任何"设置类"改动存完都该调它一次。机器人进程（AI 插件 / 李清菡档案 /
    内置记忆）都是**热读**这两份文件，所以刷完就是生效了 —— **不用重启机器人**。

    返回是否至少刷成功了快照。绝不抛异常：刷不动只记日志（用户还能点重启兜底）。
    """
    ok = False
    try:
        write_env(settings)
    except Exception as exc:  # noqa: BLE001 —— 写 .env 失败不能挡住开关快照
        logger.warning("刷新机器人 .env 失败（主人/唤醒词改动要重启才生效）: %s", exc)
    try:
        ok = write_live_gates(settings, log=log) is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("刷新热配置快照失败（开关改动要重启才生效）: %s", exc)
    return ok


def ai_platform_env(settings) -> dict:
    """AI 平台开关 → 机器人侧环境变量（AI_PLATFORM_QQ=1/0 ...）。

    平台开关只影响 AI 大脑是否处理该平台的消息，不影响平台适配器本身运行。
    默认全开；旧 settings.json 没有该字段时同样按全开处理。
    """
    platforms = getattr(settings, "ai_platforms", None)
    if not isinstance(platforms, dict):
        platforms = {}
    if getattr(settings, "dsh_enabled", False):
        # 统一大脑开启后：微信由 dsh 桥接管，旧 AI 插件不再处理微信，避免双回复
        platforms = dict(platforms)
        platforms["wechat"] = False
    if _agent_profile_enabled(settings):
        # 李清菡接管 QQ 与微信（微信走 18650 桥），旧 AI 插件全部停用避免双回复
        platforms = dict(platforms)
        platforms["wechat"] = False
        if not _onebot_active(settings):
            # 第三方通道下档案收不到 QQ 事件（插件里按官方适配器过滤），
            # QQ 交回 AI 插件，否则协议端连上了也没人回话
            platforms["qq"] = False
    env = {}
    for key in AI_PLATFORM_KEYS:
        env[f"AI_PLATFORM_{key.upper()}"] = "1" if platforms.get(key, True) else "0"
    return env


def build_bot_env(settings) -> dict:
    """构建 NoneBot 进程环境变量（含功能闸门：微信通道已免费开放）。"""
    env = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    # 内置记忆档位必须走**环境变量注入**：机器人进程不读 .env 的 os.environ
    # （2026-10-11 踩过的坑），只有这里塞进去才真的到得了插件。
    try:
        from . import ai_config as ai_config_mod
        env["ASTROSWARM_MEMORY_LEVEL"] = ai_config_mod.read_memory_level_or_off(settings)
        # 给内置记忆模块指路（它自己也有回退，这里是显式约定）
        env["ASTROSWARM_MEMORY_DB"] = str(
            Path(settings.bot_dir) / "data" / "ai" / "astroswarm_memory.db")
        env["ASTROSWARM_BOT_DIR"] = str(settings.bot_dir)
    except Exception as exc:  # noqa: BLE001 —— 记忆档位读不到不能挡住启动
        logger.warning("记忆档位读取失败，按基础档启动: %s", exc)
        env["ASTROSWARM_MEMORY_LEVEL"] = "basic"
    if not getattr(settings, "ai_enabled", True):
        env["AI_DISABLED"] = "1"
        return env
    env.update(ai_platform_env(settings))
    gate = lic_mod.feature_gate()
    # 微信通道不再需要开通验证（2026-10-01 老板拍板：所有人可用），这里只在
    # 将来把 WECHAT_FREE_FOR_ALL 改回 False 时才会把通道关掉。
    if not gate.get("member", True):
        env["AI_PLATFORM_WECHAT"] = "0"
    return env


def start_bot(settings, on_line=None):
    """启动 NoneBot。返回 Popen。"""
    py = settings.python_exe
    if not py.exists():
        raise RuntimeError("Python 运行时不存在，请先完成部署")
    run_py = settings.bot_dir / "run.py"
    if not run_py.exists():
        raise RuntimeError("机器人项目不完整（缺少 bot/run.py），请重新完成部署")
    # 微信适配器：通道已免费开放，直接保证适配器在位（不再按权益增删）
    gate = lic_mod.feature_gate()
    apply_wechat_gate(settings, full=bool(gate.get("member", True)))
    ensure_ai_plugin(settings)
    ensure_bridge_client(settings)
    ensure_liqinghan_plugin(settings)
    ensure_session_pack(settings)
    ensure_agent_runtime(settings, log=on_line)
    # 有效开关快照：机器人进程每条消息热读它，所以平台开关 / AI 总开关改完
    # 立即生效（2026-10-11 热重载）。启动时先刷一遍，保证进程一上来就是新值。
    write_live_gates(settings, log=on_line)
    # 插件自检：把"装上了却不会回话"（导入失败 / 适配器字段对不上）这类问题
    # 在启动时就写进部署日志，而不是等用户发现机器人不理人（2026-10-03 两次事故）
    try:
        selfcheck_ai_plugin(settings, log=on_line)
    except Exception as e:  # noqa: BLE001 —— 自检本身出错不能挡住启动
        logger.warning("AI 插件自检异常（不影响启动）: %s", e)
    # 自愈：按当前设置重新生成适配器列表（QQ 官方 / 微信 iLink），避免填了凭证但没生效
    sync_qq_adapters(settings, log=on_line)
    if _onebot_active(settings):
        ensure_onebot_deps(settings, log=on_line)
    elif getattr(settings, "qq_official_appid", ""):
        ensure_official_qq_deps(settings, log=on_line)
    # 飞书通道：开了开关又有凭证才装适配器（G4）
    if _feishu_ready(settings):
        ensure_feishu_deps(settings, log=on_line)
    if getattr(settings, "ai_enabled", True):
        ensure_ai_deps(settings, log=on_line)
    # 自愈：刷新 .env（保证 LOCALSTORE_USE_CWD 等新配置写入旧部署）
    write_env(settings)
    env = build_bot_env(settings)
    # 注入已安装能力包：目录 + 允许加载的包列表（能力包自 2026-09 起全部免费）
    try:
        from . import tool_packs as tool_packs_mod
        env.update(tool_packs_mod.pack_env(settings))
    except Exception:  # noqa: BLE001
        pass
    # 注入和风天气工具包配置（项目ID/凭据ID/私钥路径 → QWEATHER_* 环境变量）
    try:
        env.update(tool_packs_mod.qweather_env(settings))
    except Exception:  # noqa: BLE001
        pass
    # 注入本地人设工坊：用户自制人设包（人格 + 工具）
    try:
        from . import persona_workshop as persona_workshop_mod
        env.update(persona_workshop_mod.pack_env(settings))
    except Exception:  # noqa: BLE001
        pass
    # 注入本地知识库目录（AI 内置 knowledge_search 工具检索用）
    try:
        env["ASTROSWARM_KNOWLEDGE_DIR"] = str(
            Path(settings.root) / "knowledge")
    except Exception:  # noqa: BLE001
        pass
    return start_process(
        [str(py), "run.py"], cwd=settings.bot_dir, on_line=on_line,
        env=env,
        log_file=settings.logs_dir / "nonebot.log",
    )


def stop_bot(proc):
    stop_process_tree(proc)
