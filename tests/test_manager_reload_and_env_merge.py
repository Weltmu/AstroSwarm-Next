# -*- coding: utf-8 -*-
"""manager.py 两条「改了没生效」的根因（2026-10-15 修）。

## ① maybe_reload 的指纹记早了 → 每条消息重载+重写一次配置

`_load_manager_config()` → `_merge_env_config()` 末尾会**无条件写盘**
（`_save_manager_config()`），写完后文件 mtime 就变了。
`maybe_reload` 以前是「先记指纹、再 load」，于是下一次调用必然又判定"文件变了"：

    call1 → False（首次，指纹是启动时记的）
    call2 → True, call3 → True, call4 → True …  永不收敛

后果除了写盘放大，**还有一条致命的**：`proactive._wait_effective` 每 10 秒被这个
True 打断并「按新间隔重新计时」，于是永远凑不满一个间隔 → **主动聊天 / 群聊搭话
一条都发不出来**（重启机器人暂时恢复，改一次设置又死）。

构造函数里本来就是「读完再记」（`__init__`），只有 `maybe_reload` 写反了。

## ② _merge_env_config 拿 .env 的**默认值**盖界面设置

守卫写成 `if config.aichat_xxx > 0` / `0 <= quiet <= 23`，而 config.py 里这些字段的
默认值本身就满足条件（30 / 90 / 0 / 8 …），于是每次热重载都拿默认值把界面值盖回去：
「AI 大脑」页改完间隔/免打扰/搭话概率，下一次热重载全被打回默认。

修法：用 pydantic 的 `model_fields_set`（只含**显式提供**的字段）区分"用户配了"
和"这是默认值" —— 同文件里 `session_expire_seconds` 早就是这个口径。

跑法：
    .\\.build_venv_py314\\Scripts\\python.exe -m pytest tests/test_manager_reload_and_env_merge.py -q
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai" / "manager.py"


def _code(text: str) -> str:
    """去掉注释行与行尾注释，避免注释里的字样让断言误判。"""
    out = []
    for ln in text.splitlines():
        s = ln.split("#", 1)[0]
        out.append(s)
    return "\n".join(out)


def _func_body(src: str, name: str, end_marker: str) -> str:
    start = src.index("def %s" % name)
    end = src.index(end_marker, start)
    return src[start:end]


# ================================================================ ① 指纹顺序
def test_reload_key_is_recorded_after_load():
    """`_reload_key` 必须在 `_load_manager_config()` **之后**记。"""
    body = _code(_func_body(MANAGER.read_text(encoding="utf-8"),
                            "maybe_reload", "def _load_manager_config"))
    i_load = body.index("_load_manager_config()")
    i_key = body.index("_reload_key = self._file_key()")
    assert i_load < i_key, (
        "又变回「先记指纹、再 load」了 —— 因为 load 末尾会无条件写盘，"
        "下次调用必然又判成'文件变了'，于是每条消息都重载+重写配置，"
        "并且主动聊天/群聊搭话会被 _wait_effective 的重新计时**永久卡死**。")


def test_constructor_still_records_key_after_load():
    """构造函数里那条正确写法别被顺手改掉。"""
    src = _code(MANAGER.read_text(encoding="utf-8"))
    init = _func_body(src, "__init__", "def _file_key")
    assert init.index("_load_manager_config()") < init.index("_reload_key = self._file_key()"), \
        "构造函数也必须读完再记指纹"


# ================================================================ ② .env 默认值不许盖界面
def test_explicit_helper_exists():
    src = MANAGER.read_text(encoding="utf-8")
    assert "def _explicit(" in src, "缺少「这一项是不是显式配过」的判定函数"
    assert "model_fields_set" in src, \
        "要用 pydantic 的 model_fields_set 区分「显式配了」和「这是默认值」"


def test_fill_param_helper_exists():
    src = MANAGER.read_text(encoding="utf-8")
    assert "def _fill_param(" in src, "缺少统一的参数写入函数（显式覆盖 / 否则只补缺省）"


def test_no_unconditional_default_override():
    """老的「用默认值无条件盖界面」写法不许回来。"""
    code = _code(MANAGER.read_text(encoding="utf-8"))
    banned = [
        'self._data["interject_enabled"] = bool(config.aichat_interject_enabled)',
        'self._data["group_auto_participate"] = bool(config.aichat_group_auto_participate)',
        'self._data["interject_probability"] = max(0.0, min(1.0',
    ]
    for b in banned:
        assert b not in code, (
            "又出现无条件覆盖：「%s」\n"
            "config.py 里这些字段的默认值本身就满足 >0 / 范围守卫，"
            "所以每次热重载都会把界面设置打回默认（症状：改了没生效、自己弹回去）。" % b)


def test_all_merged_params_go_through_fill_param():
    """`_merge_env_config` 里的数值/布尔参数必须走 `_fill_param`（即带显式判定）。"""
    src = MANAGER.read_text(encoding="utf-8")
    body = _code(_func_body(src, "_merge_env_config", "def _explicit"))
    for key in ("proactive_interval_min", "proactive_interval_max", "proactive_cooldown",
                "proactive_quiet_start", "proactive_quiet_end", "interject_enabled",
                "interject_probability", "interject_interval_min",
                "interject_interval_max", "interject_history_count",
                "session_unrelated_limit", "group_auto_participate"):
        assert re.search(r'_fill_param\([^)]*"%s"' % re.escape(key), body), \
            "参数 %s 没有走 _fill_param —— 它会被默认值盖掉" % key


def test_targets_and_superusers_still_merge():
    """反面对照：目标列表 / 超管这些**并集**语义不能被误改。"""
    body = _code(_func_body(MANAGER.read_text(encoding="utf-8"),
                            "_merge_env_config", "def _explicit"))
    assert 'self._data["super_users"] = su' in body
    assert "proactive_groups" in body
