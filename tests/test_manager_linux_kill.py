# -*- coding: utf-8 -*-
r"""§3 Linux 等价实现单测（2026-10-11 Windows→Linux 同步）。

覆盖 `core/manager.py` 里那套「关窗快杀」在 Linux 上的分支：
  · `_terminate_pid`   → `os.kill(pid, SIGKILL)`（**绝不**走 ctypes.WinDLL）
  · `_kill_pid_tree`   → `os.killpg(os.getpgid(pid), SIGKILL)`，取不到组按
                          `/proc/<pid>/task/*/children` 递归
  · `_port_pids_netstat` / `_port_pids` → `ss -ltnp`（取 `pid=NNN`）→ `lsof` 兜底
  · `kill_all_fast()`  → 上面三者的组合（本用例只断言"调了谁"，不真杀进程）

**不真杀进程**：`os.kill` / `os.killpg` 全部被替换成记录器；PID 用假的；
`ss` / `lsof` 的输出用固定文本喂进解析函数。所以在 Windows 上也能跑（fake linux 分支）。
"""
import signal

import pytest

from qbotmanager.core import manager as m

# Windows 的 signal 模块**没有 SIGKILL**（Linux 上是 9）。本用例要在 Windows 上
# 覆盖 Linux 分支，所以取一个跨平台的等价常量来断言。
SIGKILL = getattr(signal, "SIGKILL", 9)


@pytest.fixture()
def fake_linux(monkeypatch):
    """把 manager 的平台判据切成 Linux，并把所有"真会动手"的系统调用换成记录器。"""
    calls = {"kill": [], "killpg": [], "getpgid": {}}

    monkeypatch.setattr(m, "_is_windows", lambda: False)

    def fake_kill(pid, sig=0):
        calls["kill"].append((int(pid), int(sig)))

    def fake_killpg(pgid, sig=0):
        calls["killpg"].append((int(pgid), int(sig)))

    def fake_getpgid(pid):
        pid = int(pid)
        if pid in calls["getpgid"]:          # 值可以是异常 → 模拟"取不到进程组"
            got = calls["getpgid"][pid]
            if isinstance(got, Exception):
                raise got
            return got
        return pid                            # 默认：自己是组长

    # raising=False：Windows 的 os 模块**没有** killpg/getpgid，这里临时挂上假实现，
    # 好让「Linux 分支」在 Windows 上也能被单测覆盖（不碰任何真进程）。
    monkeypatch.setattr(m.os, "kill", fake_kill, raising=False)
    monkeypatch.setattr(m.os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(m.os, "getpgid", fake_getpgid, raising=False)
    return calls


# ---------------------------------------------------------------- _terminate_pid
def test_terminate_pid_linux_uses_sigkill(fake_linux):
    assert m._terminate_pid(4321) is True
    assert fake_linux["kill"] == [(4321, SIGKILL)]
    assert fake_linux["killpg"] == []          # 单杀不走进程组


def test_terminate_pid_linux_dead_pid_returns_false(fake_linux, monkeypatch):
    def dead(pid, sig=0):
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr(m.os, "kill", dead)
    assert m._terminate_pid(999999) is False


def test_terminate_pid_linux_never_touches_windll(fake_linux, monkeypatch):
    """Linux 分支**不许**碰 ctypes.WinDLL —— Linux 上没有 kernel32.dll。"""
    import ctypes

    def boom(*a, **kw):                        # pragma: no cover - 命中即失败
        raise AssertionError("Linux 路径调用了 ctypes.WinDLL！")

    monkeypatch.setattr(ctypes, "WinDLL", boom, raising=False)
    assert m._terminate_pid(111) is True


# ---------------------------------------------------------------- _kill_pid_tree
def test_kill_pid_tree_linux_kills_process_group(fake_linux):
    m._kill_pid_tree(2000)
    assert fake_linux["killpg"] == [(2000, SIGKILL)]
    assert fake_linux["kill"] == []            # 整组杀成功后不再逐个杀


def test_kill_pid_tree_linux_falls_back_to_proc_children(fake_linux, monkeypatch):
    """getpgid 取不到进程组（权限/已退出）→ 按 /proc/<pid>/task/*/children 递归。"""
    fake_linux["getpgid"][2000] = ProcessLookupError(3, "No such process")
    tree = {2000: [2001, 2002], 2001: [2003], 2002: [], 2003: []}
    monkeypatch.setattr(m, "_linux_children", lambda pid: tree.get(int(pid), []))

    m._kill_pid_tree(2000)

    assert fake_linux["killpg"] == []
    killed = [pid for pid, _sig in fake_linux["kill"]]
    assert sorted(killed) == [2000, 2001, 2002, 2003]
    assert set(s for _pid, s in fake_linux["kill"]) == {SIGKILL}
    # 先叶子后父：父进程最后被杀（否则子进程会被 reparent 掉、再也找不到）
    assert killed[-1] == 2000


def test_linux_children_reads_proc_tree(monkeypatch, tmp_path):
    """`_linux_children` 真读 /proc 布局（用临时目录伪造一份）。"""
    task = tmp_path / "4321" / "task"
    (task / "4321").mkdir(parents=True)
    (task / "4321" / "children").write_text("5001 5002\n", encoding="utf-8")
    (task / "4322").mkdir()
    (task / "4322" / "children").write_text("5002\n", encoding="utf-8")

    real_path = m.Path

    def fake_path(p):
        if str(p).startswith("/proc/"):
            return real_path(str(p).replace("/proc/", str(tmp_path) + "/"))
        return real_path(p)

    monkeypatch.setattr(m, "Path", fake_path)
    assert m._linux_children(4321) == [5001, 5002]      # 去重 + 排序


def test_linux_children_missing_proc_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "Path", lambda p: tmp_path / "nope")
    assert m._linux_children(1) == []


# ---------------------------------------------------------------- 端口解析
SS_OUTPUT = """State  Recv-Q Send-Q Local Address:Port  Peer Address:Port Process
LISTEN 0      4096   127.0.0.53%lo:53         0.0.0.0:*         users:(("systemd-resolve",pid=749,fd=14))
LISTEN 0      2048         0.0.0.0:7860       0.0.0.0:*         users:(("python",pid=854758,fd=13))
LISTEN 0      511          0.0.0.0:80         0.0.0.0:*         users:(("nginx",pid=4190029,fd=6),("nginx",pid=4190028,fd=6))
LISTEN 0      2048            [::]:7860          [::]:*         users:(("python",pid=854759,fd=13))
LISTEN 0      100         127.0.0.1:18650      0.0.0.0:*         users:(("dsh",pid=900001,fd=7))
ESTAB  0      0           10.0.0.1:7860       10.0.0.2:51234
"""

LSOF_OUTPUT = """COMMAND    PID USER   FD   TYPE  DEVICE SIZE/OFF NODE NAME
python  854758 root   13u  IPv4 1234567      0t0  TCP *:7860 (LISTEN)
python  854759 root   13u  IPv6 1234568      0t0  TCP *:7860 (LISTEN)
"""


def test_parse_ss_pids_matches_port_and_v6():
    assert m._parse_ss_pids(SS_OUTPUT, 7860) == [854758, 854759]
    assert m._parse_ss_pids(SS_OUTPUT, 80) == [4190028, 4190029]
    assert m._parse_ss_pids(SS_OUTPUT, 18650) == [900001]


def test_parse_ss_pids_no_listener_is_empty_not_error():
    """空列表 = 确实没人监听（与 Windows 版语义一致，绝不能当成失败）。"""
    assert m._parse_ss_pids(SS_OUTPUT, 19999) == []


def test_parse_ss_pids_ignores_non_listen_states():
    """只有 ESTAB 行、没有 LISTEN 行时不许误报。"""
    only_estab = "\n".join(l for l in SS_OUTPUT.splitlines() if l.startswith("ESTAB"))
    assert m._parse_ss_pids(only_estab, 7860) == []


def test_parse_lsof_pids_skips_header():
    assert m._parse_lsof_pids(LSOF_OUTPUT) == [854758, 854759]
    assert m._parse_lsof_pids("") == []


class _FakeRun:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def test_port_pids_netstat_linux_uses_ss(monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return _FakeRun(0, SS_OUTPUT)

    monkeypatch.setattr(m, "_is_windows", lambda: False)
    monkeypatch.setattr(m.subprocess, "run", fake_run)
    mgr = object.__new__(m.Manager)

    assert mgr._port_pids_netstat(7860) == [854758, 854759]
    assert seen[0][:2] == ["ss", "-ltnp"]


def test_port_pids_netstat_linux_falls_back_to_lsof(monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        if cmd[0] == "ss":
            raise FileNotFoundError("ss: not found")
        return _FakeRun(0, LSOF_OUTPUT)

    monkeypatch.setattr(m, "_is_windows", lambda: False)
    monkeypatch.setattr(m.subprocess, "run", fake_run)
    mgr = object.__new__(m.Manager)

    assert mgr._port_pids_netstat(7860) == [854758, 854759]
    assert seen[0][0] == "ss" and seen[1][0] == "lsof"


def test_port_pids_netstat_linux_no_tools_returns_none(monkeypatch):
    """ss / lsof 都没有 → None（调用方据此知道"命令失败"，而不是"没人监听"）。"""
    def fake_run(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(m, "_is_windows", lambda: False)
    monkeypatch.setattr(m.subprocess, "run", fake_run)
    mgr = object.__new__(m.Manager)

    assert mgr._port_pids_netstat(7860) is None


def test_port_pids_linux_slow_path_uses_lsof(monkeypatch):
    def fake_run(cmd, **kw):
        assert cmd[0] == "lsof"
        return _FakeRun(0, LSOF_OUTPUT)

    monkeypatch.setattr(m, "_is_windows", lambda: False)
    monkeypatch.setattr(m.subprocess, "run", fake_run)
    mgr = object.__new__(m.Manager)

    assert mgr._port_pids(7860) == [854758, 854759]


def test_port_pids_linux_lsof_missing_returns_empty(monkeypatch):
    """`_port_pids` 契约是返回列表（旧行为），工具缺失也不能抛。"""
    def fake_run(cmd, **kw):
        raise FileNotFoundError("lsof")

    monkeypatch.setattr(m, "_is_windows", lambda: False)
    monkeypatch.setattr(m.subprocess, "run", fake_run)
    mgr = object.__new__(m.Manager)

    assert mgr._port_pids(7860) == []


# ---------------------------------------------------------------- kill_all_fast
class _FakeSettings:
    nonebot_port = 7860
    python_exe = "/opt/astroswarm/venv/bin/python"


def test_kill_all_fast_linux_uses_linux_paths(monkeypatch):
    """kill_all_fast 逻辑不用改：按 PID 快杀；没有 PID 才按端口兜底。"""
    monkeypatch.setattr(m, "_is_windows", lambda: False)
    killed = []
    monkeypatch.setattr(m, "_kill_fast", lambda pid: killed.append(("fast", int(pid))))

    mgr = object.__new__(m.Manager)
    mgr.settings = _FakeSettings()
    mgr.bot_proc = type("P", (), {"pid": 111})()
    mgr.dsh_proc = type("P", (), {"pid": 222})()

    out = mgr.kill_all_fast()

    assert killed == [("fast", 111), ("fast", 222)]
    assert out == {"killed": ["NoneBot:111", "dsh:222"]}
    assert mgr.bot_proc is None and mgr.dsh_proc is None


def test_kill_all_fast_no_pid_linux_port_fallback(monkeypatch):
    """已知 PID 一个都没有时，按端口兜底找残留监听（Linux 走 ss）。"""
    monkeypatch.setattr(m, "_is_windows", lambda: False)
    monkeypatch.setattr(m.subprocess, "run", lambda cmd, **kw: _FakeRun(0, SS_OUTPUT))
    killed = []
    monkeypatch.setattr(m, "_kill_fast", lambda pid: killed.append(int(pid)))

    mgr = object.__new__(m.Manager)
    mgr.settings = _FakeSettings()
    mgr.bot_proc = None
    mgr.dsh_proc = None
    monkeypatch.setattr(m.dsh_mod, "BRIDGE_PORT", 18650)

    out = mgr.kill_all_fast()

    assert sorted(killed) == [854758, 854759, 900001]
    assert sorted(out["killed"]) == ["dsh@18650:900001", "nonebot@7860:854758",
                                     "nonebot@7860:854759"]


def test_kill_all_fast_port_lookup_failure_is_swallowed(monkeypatch):
    """兜底查端口失败不能影响退出（关窗路径上它是"尽力而为"）。"""
    monkeypatch.setattr(m, "_is_windows", lambda: False)

    def boom(cmd, **kw):
        raise OSError("no ss, no lsof")

    monkeypatch.setattr(m.subprocess, "run", boom)
    mgr = object.__new__(m.Manager)
    mgr.settings = _FakeSettings()
    mgr.bot_proc = None
    mgr.dsh_proc = None

    assert mgr.kill_all_fast() == {"killed": []}


# ---------------------------------------------------------------- 平台判据
def test_creationflags_is_zero_on_posix(monkeypatch):
    """CREATE_NO_WINDOW 在 Linux 上不存在；取值必须是 0 而不是抛 AttributeError。"""
    monkeypatch.delattr(m.subprocess, "CREATE_NO_WINDOW", raising=False)
    assert m._creationflags() == 0


def test_creationflags_keeps_windows_value(monkeypatch):
    monkeypatch.setattr(m.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert m._creationflags() == 0x08000000


def test_proc_alive_linux(monkeypatch):
    monkeypatch.setattr(m, "_is_windows", lambda: False)
    monkeypatch.setattr(m.os, "kill", lambda pid, sig=0: None)
    assert m._proc_alive(4242) is True

    def dead(pid, sig=0):
        raise ProcessLookupError()

    monkeypatch.setattr(m.os, "kill", dead)
    assert m._proc_alive(4242) is False
    assert m._proc_alive(0) is False


def test_pid_alive_linux_uses_kill0(monkeypatch):
    """qq_channel._pid_alive 同样是 Linux 专用实现（原来在 Linux 上恒为 False）。"""
    from qbotmanager.core import qq_channel

    seen = []
    monkeypatch.setattr(qq_channel.sys, "platform", "linux")
    monkeypatch.setattr(qq_channel.os, "kill", lambda pid, sig=0: seen.append((pid, sig)))

    assert qq_channel._pid_alive(777) is True
    assert seen == [(777, 0)]


def test_pid_alive_linux_missing_process(monkeypatch):
    from qbotmanager.core import qq_channel

    monkeypatch.setattr(qq_channel.sys, "platform", "linux")

    def dead(pid, sig=0):
        raise ProcessLookupError()

    monkeypatch.setattr(qq_channel.os, "kill", dead)
    assert qq_channel._pid_alive(777) is False
