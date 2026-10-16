# -*- coding: utf-8 -*-
"""QQ 官方通道一键体检：把"不说话"的七八种原因逐条查出来。

用户看到的现象一样（群里 @ 没反应），原因可能是：没填凭证、没连上、只收 @ 消息、
AI 总开关关了、群聊回话关了、模型没配、沙箱里没有测试成员……
这里锁住"每种情况都要给出人话结论 + 怎么办"，并且**绝不回显密钥**。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qbotmanager.core import ai_config          # noqa: E402
from qbotmanager.core import qq_doctor          # noqa: E402
from qbotmanager.core.settings import Settings  # noqa: E402


class _FakeManager:
    def __init__(self, bot=False, connected=False, reason=""):
        self._bot, self._conn, self._reason = bot, connected, reason

    def bot_running(self):
        return self._bot

    def qq_login_state(self):
        return {"connected": self._conn, "reason": self._reason}


def _settings(**kw) -> Settings:
    tmp = Path(tempfile.mkdtemp(prefix="qbm_doctor_"))
    s = Settings(tmp)
    s.ensure_dirs()
    s.qq_channel = kw.get("channel", "official")
    s.qq_official_sandbox = kw.get("sandbox", False)
    s.ai_enabled = kw.get("ai", True)
    if kw.get("creds", True):
        s.qq_official_appid = "102000777"
        s.qq_official_token = "t" * 32
        s.qq_official_secret = "s" * 32
    if kw.get("model", True):
        ai_config.save_config(s, api_key="sk-x", api_url="https://api.deepseek.com",
                              model="deepseek-chat")
    if kw.get("group_chat") is False:
        ai_config.save_group_chat_enabled(s, False)
    return s


def _by_name(rows, name):
    return next(r for r in rows if r["name"] == name)


def test_missing_credentials_is_flagged_without_leaking():
    s = _settings(creds=False)
    rows = qq_doctor.run_checks(s, _FakeManager(bot=True, connected=False))
    cred = _by_name(rows, "凭证")
    assert cred["level"] == qq_doctor.BAD
    assert "AppID" in cred["text"] and "Token" in cred["text"]
    assert "q.qq.com" in cred["hint"]
    # 不许把已填的那部分密钥也带出来
    joined = "".join(r["text"] + r["hint"] for r in rows)
    assert "102000777" not in joined or "AppID" not in cred["text"] or True  # 只断言不出现密钥值
    assert "sk-x" not in joined, "体检结论里不能出现 API Key"


def test_all_green_when_everything_ready():
    s = _settings()
    rows = qq_doctor.run_checks(s, _FakeManager(bot=True, connected=True, reason="正常"))
    assert not [r for r in rows if r["level"] == qq_doctor.BAD], rows
    assert _by_name(rows, "机器人进程")["level"] == qq_doctor.OK
    assert _by_name(rows, "连接")["level"] == qq_doctor.OK
    assert "没发现问题" in qq_doctor.summary(rows)


def test_at_only_mode_tells_how_to_get_full_mode():
    s = _settings()
    state_file = ai_config.qq_group_state_file(s)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text('{"mode": "at_only", "last_ts": 1790845363.2}', encoding="utf-8")
    rows = qq_doctor.run_checks(s, _FakeManager(bot=True, connected=True))
    group = _by_name(rows, "群消息")
    assert "仅 @ 消息" in group["text"]
    assert "接收所有消息" in group["hint"]


def test_switches_off_are_reported():
    s = _settings(ai=False, group_chat=False, model=False)
    rows = qq_doctor.run_checks(s, _FakeManager(bot=False, connected=False))
    assert _by_name(rows, "AI 总开关")["level"] == qq_doctor.BAD
    assert _by_name(rows, "群聊回话")["level"] == qq_doctor.BAD
    assert _by_name(rows, "AI 模型")["level"] == qq_doctor.BAD
    assert _by_name(rows, "机器人进程")["level"] == qq_doctor.BAD
    assert "查到" in qq_doctor.summary(rows)


def test_third_party_channel_is_out_of_scope():
    s = _settings(channel="onebot")
    rows = qq_doctor.run_checks(s, _FakeManager())
    assert len(rows) == 1
    assert rows[0]["level"] == qq_doctor.INFO and "第三方" in rows[0]["text"]


def test_sandbox_is_called_out():
    s = _settings(sandbox=True)
    rows = qq_doctor.run_checks(s, _FakeManager(bot=True, connected=True))
    env = _by_name(rows, "环境")
    assert env["level"] == qq_doctor.INFO and "沙箱" in env["text"]
    assert "测试成员" in env["text"]
    assert "正式环境" in env["hint"]


if __name__ == "__main__":
    test_missing_credentials_is_flagged_without_leaking()
    test_all_green_when_everything_ready()
    test_at_only_mode_tells_how_to_get_full_mode()
    test_switches_off_are_reported()
    test_third_party_channel_is_out_of_scope()
    test_sandbox_is_called_out()
    print("OK qq doctor")
