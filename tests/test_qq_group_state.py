# -*- coding: utf-8 -*-
"""QQ 官方通道"收消息模式"：机器人侧记录 + 客户端状态卡显示。

用户和排查的人都看不出机器人到底在收「全量群消息」还是「只收 @ 消息」，
而这是"群里 @ 没反应"最常见的原因。这里两头都锁住：
插件按真实事件类型记状态，客户端把状态翻译成大白话。
"""
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

AI_DIR = ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"


def _load_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_record_and_mode():
    """收到全量事件 → 全量模式；只收到 @ 事件 → 仅 @ 模式。"""
    st = _load_by_path("ai_group_state_t", AI_DIR / "group_state.py")
    state = st.empty_state()
    assert st.mode_of(state) == st.MODE_NONE

    state = st.record(state, st.EVENT_AT, at_me=True, now=1000.0)
    assert st.mode_of(state) == st.MODE_AT_ONLY
    assert state["last_at_me"] is True and state["last_ts"] == 1000.0

    state = st.record(state, st.EVENT_FULL, at_me=False, now=2000.0)
    assert st.mode_of(state) == st.MODE_FULL, "收到过全量事件就是全量模式"
    assert state["events"][st.EVENT_FULL] == 1, "次数要累加，方便排查"
    assert state["events"][st.EVENT_AT] == 1


def test_event_type_normalization():
    """适配器给的是 str-Enum，必须规范化成纯名字，否则客户端认不出模式。"""
    import enum

    st = _load_by_path("ai_group_state_norm", AI_DIR / "group_state.py")

    class EventType(str, enum.Enum):
        GROUP_MESSAGE_CREATE = "GROUP_MESSAGE_CREATE"
        GROUP_AT_MESSAGE_CREATE = "GROUP_AT_MESSAGE_CREATE"

    assert st.normalize_event_type(EventType.GROUP_MESSAGE_CREATE) == "GROUP_MESSAGE_CREATE"
    assert st.normalize_event_type("GROUP_AT_MESSAGE_CREATE") == "GROUP_AT_MESSAGE_CREATE"
    assert st.normalize_event_type("EventType.GROUP_MESSAGE_CREATE") == "GROUP_MESSAGE_CREATE"
    assert st.normalize_event_type("") == ""

    state = st.record(st.empty_state(), EventType.GROUP_MESSAGE_CREATE, at_me=True)
    assert st.mode_of(state) == st.MODE_FULL, "枚举形态也要能判成全量模式"


def test_state_file_roundtrip():
    st = _load_by_path("ai_group_state_rt", AI_DIR / "group_state.py")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "qq_group_state.json"
        assert st.load(path)["mode"] == st.MODE_NONE, "没有文件时是空状态"
        state = st.record(st.load(path), st.EVENT_FULL, at_me=True, now=time.time())
        assert st.save(path, state) is True
        back = st.load(path)
        assert back["mode"] == st.MODE_FULL and back["last_at_me"] is True
        path.write_text("{ 坏掉的 json", encoding="utf-8")
        assert st.load(path)["mode"] == st.MODE_NONE, "坏文件按空状态处理，不许抛"


def test_status_card_text():
    """卡片那一行 + 需要时补的那句"怎么开全量"。"""
    from qbotmanager.core import ai_config

    full = {"mode": "full", "last_ts": time.time()}
    at_only = {"mode": "at_only", "last_ts": time.time()}
    none = {"mode": "none"}

    assert ai_config.qq_group_state_text(full).startswith("全量模式")
    assert "最近" in ai_config.qq_group_state_text(full)
    assert ai_config.qq_group_state_text(at_only).startswith("仅 @ 消息")
    assert "还没收到过" in ai_config.qq_group_state_text(none)
    assert ai_config.qq_group_state_hint(full) == ""
    hint = ai_config.qq_group_state_hint(at_only)
    assert "接收所有消息" in hint and "q.qq.com" in hint


def test_reading_state_from_bot_dir():
    """客户端从机器人目录读那份状态（插件的 localstore 就写在这里）。"""
    from qbotmanager.core import ai_config
    from qbotmanager.core.settings import Settings

    tmp = Path(tempfile.mkdtemp(prefix="qbm_qqstate_"))
    s = Settings(tmp)
    s.ensure_dirs()
    path = ai_config.qq_group_state_file(s)
    assert path == s.bot_dir / "config" / "ai" / "qq_group_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mode": "at_only", "last_ts": time.time()}),
                    encoding="utf-8")
    state = ai_config.read_qq_group_state(s)
    assert state["mode"] == "at_only"
    assert ai_config.qq_group_state_text(state).startswith("仅 @ 消息")


def test_groups_are_remembered_for_the_ui_list():
    """"见过消息的群"要记下来，界面才能让用户勾选允许回话的群。"""
    st = _load_by_path("ai_group_state_groups", AI_DIR / "group_state.py")

    state = st.empty_state()
    state = st.record(state, st.EVENT_FULL, now=100.0, group_openid="群A")
    state = st.record(state, st.EVENT_FULL, now=200.0, group_openid="群B")
    state = st.record(state, st.EVENT_FULL, now=300.0, group_openid="群A")
    groups = state["groups"]
    assert set(groups) == {"群A", "群B"}
    assert groups["群A"]["count"] == 2 and groups["群A"]["last_ts"] == 300.0
    assert groups["群B"]["count"] == 1
    # 不带 group_openid 的记录不该往名单里塞空 key
    state = st.record(state, st.EVENT_FULL, now=400.0)
    assert "" not in state["groups"]


def test_group_list_is_capped():
    """群很多时只留最近活跃的若干个，别让状态文件无限长大。"""
    st = _load_by_path("ai_group_state_cap", AI_DIR / "group_state.py")
    state = st.empty_state()
    for i in range(st.MAX_GROUPS + 10):
        state = st.record(state, st.EVENT_FULL, now=float(i), group_openid=f"群{i}")
    assert len(state["groups"]) == st.MAX_GROUPS
    assert "群0" not in state["groups"], "最老的应当被挤掉"
    assert f"群{st.MAX_GROUPS + 9}" in state["groups"], "最新的必须留着"


def test_client_reads_group_list_and_saves_whitelist():
    """客户端：读群名单（按最近活跃排序）+ 存白名单（空 = 不限群）。"""
    import json
    import tempfile

    from qbotmanager.core import ai_config
    from qbotmanager.core.settings import Settings

    tmp = Path(tempfile.mkdtemp(prefix="qbm_grouplist_"))
    s = Settings(tmp)
    s.ensure_dirs()
    path = ai_config.qq_group_state_file(s)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "mode": "full", "last_ts": 300.0,
        "groups": {"群A": {"last_ts": 300.0, "count": 2},
                   "群B": {"last_ts": 100.0, "count": 1}},
    }, ensure_ascii=False), encoding="utf-8")

    rows = ai_config.known_groups(s)
    assert [r["openid"] for r in rows] == ["群A", "群B"], "最近的排前面"
    assert rows[0]["count"] == 2

    assert ai_config.allowed_groups(s) == [], "默认不限群"
    assert ai_config.save_allowed_groups(s, ["群B"]) is True
    assert ai_config.allowed_groups(s) == ["群B"]
    assert ai_config.save_allowed_groups(s, []) is True
    assert ai_config.allowed_groups(s) == [], "清空 = 回到不限群"


def test_whitelist_actually_gates_groups():
    """白名单非空时，只有名单里的群能回话（和插件的判定必须一致）。"""
    st = _load_by_path("ai_group_state_gate", AI_DIR / "group_state.py")
    policy = _load_by_path("ai_group_policy_gate", AI_DIR / "group_policy.py")
    assert policy.group_allowed({"enabled_groups": ["群B"]}, "群B") is True
    assert policy.group_allowed({"enabled_groups": ["群B"]}, "群A") is False
    assert policy.group_allowed({"enabled_groups": []}, "任何群") is True
    assert st.MAX_GROUPS >= 10


if __name__ == "__main__":
    test_record_and_mode()
    test_event_type_normalization()
    test_groups_are_remembered_for_the_ui_list()
    test_group_list_is_capped()
    test_client_reads_group_list_and_saves_whitelist()
    test_whitelist_actually_gates_groups()
    test_state_file_roundtrip()
    test_status_card_text()
    test_reading_state_from_bot_dir()
    print("OK qq group state")
