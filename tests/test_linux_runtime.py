import sys

from astroswarm_linux import runtime


def test_find_python3(monkeypatch):
    monkeypatch.setattr(
        runtime.shutil,
        "which",
        lambda name: "/usr/bin/python3" if name == "python3" else None,
    )
    monkeypatch.setattr(runtime, "_works", lambda py: True)   # 探测单独测，这里只测选择顺序
    assert runtime.find_python3() == "/usr/bin/python3"


def test_find_python3_skips_broken_store_alias(monkeypatch):
    """Windows 的 Store 执行别名（python3.EXE，运行即退 9009）不能被选中。

    2026-10-16：以前 `which("python3")` 拿啥用啥 —— 别名一命中，后面建 venv 必炸。
    现在每个候选先试跑（`_works`），坏的跳过、回退真解释器。
    """
    monkeypatch.setattr(
        runtime.shutil, "which",
        lambda name: "/stub/python3.EXE" if name == "python3" else None,
    )
    monkeypatch.setattr(runtime, "_works", lambda py: py != "/stub/python3.EXE")
    picked = runtime.find_python3()
    assert picked != "/stub/python3.EXE"
    assert picked == sys.executable


def test_ensure_venv_creates_python(tmp_path):
    py = runtime.find_python3()
    venv_dir = tmp_path / "venv"
    result = runtime.ensure_venv(py, venv_dir, lambda m: None)
    expected = (
        venv_dir / "bin" / "python"
        if sys.platform != "win32"
        else venv_dir / "Scripts" / "python.exe"
    )
    assert result == expected and result.exists()
