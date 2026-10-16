# -*- coding: utf-8 -*-
"""沙箱加固（2026-10-15）：加载期不再跑插件代码 + 权限按声明收窄。

两件事都是"设计声明"与"实现"对不上：
1. **加载期逃逸**：`registry.load_pack` 以前对每个工具无条件 `exec_module` ——
   沙箱包的工具模块**顶层代码在机器人主进程里跑**（配置、密钥、文件全在手边），
   文件头承诺的"子进程 + 导入白名单 + 禁文件读写"只在**执行期**成立；
   `sandbox_runner.main` 里守卫也装晚了一步（先 exec 插件、后装守卫），
   连子进程里的顶层代码都是裸奔的。
2. **权限按通道给、不按声明给**：一个只在 manifest 里声明 memory 的包，
   在 QQ 群通道里照样拿到 group_admin，可以借 ctx.send("mute_user") 禁言别人。

跑法：
    .\\.test_venv\\Scripts\\python.exe -m pytest tests/test_sandbox_hardening.py -q
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qbotmanager.core.agent import sandbox  # noqa: E402
from qbotmanager.core.agent.registry import ToolRegistry  # noqa: E402
from qbotmanager.core.agent.tool import ToolContext, ToolUnavailableError  # noqa: E402

RUNNER = ROOT / "src" / "qbotmanager" / "core" / "agent" / "sandbox_runner.py"


def _make_pack(tmp: Path, *, sandbox_flag=True, top_level_code="") -> Path:
    pack = tmp / "pack_demo"
    (pack / "tools").mkdir(parents=True, exist_ok=True)
    (pack / "manifest.json").write_text(json.dumps({
        "id": "pack_demo", "name": "示例包", "version": "1.0.0",
        "kind": "tool-pack", "description": "测试用",
        "adapters": ["qq_official"], "min_version": "0.1.0",
        "permissions": ["memory"], "sandbox": sandbox_flag,
        "tools": [{"name": "demo", "description": "示例",
                   "parameters": {"type": "object", "properties": {}},
                   "permissions": []}],
    }, ensure_ascii=False), encoding="utf-8")
    (pack / "tools" / "demo.py").write_text(
        top_level_code + "\ndef handle(ctx, args):\n    return '{\"ok\": true}'\n",
        encoding="utf-8")
    return pack


def test_sandbox_module_is_not_executed_in_main_process(tmp_path):
    """沙箱工具的模块顶层代码**不许**在主进程里被执行。"""
    marker = tmp_path / "escaped.txt"
    pack = _make_pack(
        tmp_path, sandbox_flag=True,
        top_level_code="open(r'%s', 'w').write('顶层代码跑了')\n" % str(marker))
    reg = ToolRegistry()
    reg.load_pack(pack)
    assert not marker.exists(), \
        "沙箱包的顶层代码在主进程里跑了 —— 等于市场装个包就能在机器人进程里执行任意代码"
    spec = reg.get("demo")
    assert spec is not None and spec.sandbox is True


def test_non_sandbox_pack_still_loads_normally(tmp_path):
    """非沙箱包（内置能力包那种）行为不变：还是主进程直接加载。"""
    pack = _make_pack(tmp_path, sandbox_flag=False)
    reg = ToolRegistry()
    assert reg.load_pack(pack) == 1
    ctx = ToolContext(platform="qq_official", scene="group",
                      permissions={"send_message", "memory", "group_admin"})
    assert reg.execute("demo", {}, ctx) == '{"ok": true}'


def test_sandbox_tool_execute_refuses_placeholder(tmp_path):
    """沙箱工具走同步 execute 必须拒绝（真执行要走 execute_async 子进程）。"""
    pack = _make_pack(tmp_path, sandbox_flag=True)
    reg = ToolRegistry()
    reg.load_pack(pack)
    ctx = ToolContext(platform="qq_official", scene="group",
                      permissions={"send_message", "memory"})
    try:
        reg.execute("demo", {}, ctx)
    except ToolUnavailableError as exc:
        assert "execute_async" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("沙箱工具不该能从主进程同步执行")


# ================================================================ 权限收窄
def test_ctx_payload_narrows_permissions_by_declaration():
    """子进程只拿得到"包声明过的权限" ∩ "通道给的权限"。"""
    ctx = ToolContext(platform="qq:group", scene="group",
                      permissions={"send_message", "memory", "timer",
                                   "network", "media", "group_admin"})
    full = sandbox._ctx_payload(ctx)                      # 不传 = 老行为（通道给什么给什么）
    assert "group_admin" in full["permissions"]
    narrowed = sandbox._ctx_payload(ctx, allowed_perms={"memory", "send_message"})
    assert set(narrowed["permissions"]) == {"memory", "send_message"}, narrowed
    assert "group_admin" not in narrowed["permissions"], \
        "只声明 memory 的包不该拿到群管权限（能禁言/撤回别人）"


def test_ctx_payload_narrowing_keeps_channel_limits():
    """收窄只在通道权限之内做：通道本来没有的权限，声明了也不给。"""
    ctx = ToolContext(platform="wechat:private", scene="private",
                      permissions={"send_message", "memory"})
    payload = sandbox._ctx_payload(ctx, allowed_perms={"memory", "group_admin"})
    assert set(payload["permissions"]) == {"memory"}, payload


def test_source_lock_guard_installed_before_loading_plugin():
    """源码锁：子进程里守卫必须**先装**、插件代码后加载。"""
    src = RUNNER.read_text(encoding="utf-8")
    body = src.split("def main()", 1)[1]
    # 注释里也会提到这两个函数名（说明"以前是什么顺序"），先剥注释再比位置
    code = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())
    i_guard = code.index("_install_guard()")
    i_load = code.index("_load_module(")
    assert i_guard < i_load, \
        "守卫装晚了：插件模块的顶层语句会在「导入白名单 / 禁 open」生效之前跑完"
    assert "importlib.util" in src.split("def ")[0], \
        "importlib 要在文件顶部导入（守卫装上后就导不进来了）"


if __name__ == "__main__":
    print("请用 pytest 跑：pytest tests/test_sandbox_hardening.py -q")
