# -*- coding: utf-8 -*-
"""忘记控制台密码的救援命令（老板 2026-10-10 要求"在服务器上输指令来解决"）。

规矩：网页上**不提供**任何找回通道，只有能在服务器上执行命令的人能重置。
这条用例锁住：清密码、设新密码、作废所有会话、没密码时的友好提示。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from astroswarm_linux import cli, console_session as cs  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_data(monkeypatch, tmp_path):
    """把密码/会话文件钉在临时目录，别碰真机上的。"""
    monkeypatch.setenv("ASTROSWARM_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    yield


def test_reset_without_password_clears_and_revokes(capsys):
    assert cs.set_password("old-password-123")
    tok = cs.create_session(remember=True, device_label="测试")["token"]
    assert cs.verify(tok)

    rc = cli.main(["console-reset-password", "--yes"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "已清除" in out
    assert cs.is_password_set() is False
    assert cs.verify(tok) is None, "重置后旧会话必须失效"
    assert cs.verify_password_for_login("old-password-123") is False


def test_reset_with_new_password_sets_it(capsys):
    assert cs.set_password("old-password-123")
    rc = cli.main(["console-reset-password", "--password", "brand-new-456789", "--yes"])
    assert rc == 0
    assert "新密码已设置" in capsys.readouterr().out
    assert cs.is_password_set() is True
    assert cs.verify_password_for_login("brand-new-456789") is True
    assert cs.verify_password_for_login("old-password-123") is False


def test_reset_rejects_weak_new_password(capsys):
    assert cs.set_password("old-password-123")
    rc = cli.main(["console-reset-password", "--password", "123", "--yes"])
    out = capsys.readouterr().out
    assert rc == 2 and "重置失败" in out
    # 失败后旧密码仍在（不能把用户锁在门外）
    assert cs.verify_password_for_login("old-password-123") is True


def test_reset_on_fresh_server_is_friendly(capsys):
    rc = cli.main(["console-reset-password", "--yes"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "还没有设置过控制台密码" in out


def test_reset_asks_before_doing_anything(monkeypatch, capsys):
    assert cs.set_password("old-password-123")
    monkeypatch.setattr("builtins.input", lambda *a, **k: "no")
    rc = cli.main(["console-reset-password"])
    out = capsys.readouterr().out
    assert rc == 1 and "已取消" in out
    assert cs.verify_password_for_login("old-password-123") is True
    assert cs.is_password_set() is True


def test_revoke_all_counts():
    assert cs.set_password("old-password-123")
    cs.create_session(remember=False, device_label="a")
    cs.create_session(remember=False, device_label="b")
    assert cs.revoke_all() == 2
    assert cs.revoke_all() == 0


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
