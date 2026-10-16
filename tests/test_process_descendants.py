# -*- coding: utf-8 -*-
"""退出作业的后代扫描：父子关系图里有环也必须能走完。

2026-10-01 老板报「软件卡死无反应」，py-spy 抓到的堆栈是：
    job-sweep 线程 → _sweep → _descendants （100% 占一个核，界面被 GIL 拖死）

根因：Windows 的 PID 会被立刻复用，Toolhelp 快照里出现
「A 的父是 B、B 的父是 A」这种环是常态；原来的遍历只判父子、不记访问过谁，
遇到环就永远走不完（而外层 5 秒 deadline 在 _descendants 里面根本轮不到）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qbotmanager.core.process import _collect_descendants  # noqa: E402


def test_plain_tree():
    """普通三层树：孙进程也要收进来。"""
    parent_map = {1: 0, 2: 1, 3: 1, 4: 2, 99: 0}
    got = sorted(_collect_descendants(parent_map, 1))
    assert got == [2, 3, 4], got


def test_cycle_between_siblings_terminates():
    """环：2 的父是 3、3 的父是 2（PID 复用后的典型形态）—— 必须能结束。"""
    parent_map = {1: 0, 2: 3, 3: 2, 4: 1}
    got = sorted(_collect_descendants(parent_map, 1))
    assert got == [4], got


def test_self_parent_does_not_hang():
    """自己当自己父亲的脏数据：不许把调用方拖死，也不许把它自己当成后代。"""
    parent_map = {1: 1, 2: 1, 3: 2}
    got = sorted(_collect_descendants(parent_map, 1))
    assert got == [2, 3], got


def test_real_world_killer_pid_zero():
    """真实复现：进程表里 pid 0 的父是自己（实测存在），老逻辑从这里出发无限循环。"""
    parent_map = {0: 0, 5: 0, 6: 5}
    got = sorted(_collect_descendants(parent_map, 0))
    assert got == [5, 6], got


def test_cycle_reachable_from_target():
    """目标自己就在环里：也要有结果、不挂。"""
    parent_map = {1: 2, 2: 1}
    got = _collect_descendants(parent_map, 1)
    assert sorted(got) == [2], got


if __name__ == "__main__":
    test_plain_tree()
    test_cycle_between_siblings_terminates()
    test_self_parent_does_not_hang()
    test_real_world_killer_pid_zero()
    test_cycle_reachable_from_target()
    print("OK process descendants")
