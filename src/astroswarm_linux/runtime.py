"""Python 运行时探测与虚拟环境创建。"""
import shutil
import subprocess
import sys
from pathlib import Path


def _works(python: str) -> bool:
    """这个解释器真能跑吗（2026-10-16）。

    Windows 上 `shutil.which("python3")` 可能命中 Microsoft Store 的**应用执行别名**
    （`WindowsApps\\python3.EXE`）—— 它不是真解释器，一运行就退 9009。以前直接把它
    返回出去，后面 `python -m venv` 必炸（本机测试与真机部署都是这个坑）。
    """
    try:
        proc = subprocess.run(
            [python, "-c", "import sys; print(sys.version_info[0])"],
            capture_output=True, timeout=15)
        return proc.returncode == 0
    except Exception:  # noqa: BLE001 —— 探测失败（异常/超时）就当不能用
        return False


def find_python3() -> str:
    """找一个**真能用**的 python3：PATH 里的 python3/python，其次当前解释器。

    一律先试跑一下（见 `_works`）——Store 别名、损坏的软链都会被跳过。
    全部不可用时回退 `sys.executable`（至少它是正在运行的这个解释器）。
    """
    candidates = []
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found and found not in candidates:
            candidates.append(found)
    if sys.executable and sys.executable not in candidates:
        candidates.append(sys.executable)
    for cand in candidates:
        if _works(cand):
            return cand
    return sys.executable


def ensure_venv(python: str, venv_dir: Path, log) -> Path:
    venv_dir = Path(venv_dir)
    bin_py = (
        venv_dir / "bin" / "python"
        if sys.platform != "win32"
        else venv_dir / "Scripts" / "python.exe"
    )
    if bin_py.exists():
        return bin_py
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    log(f"creating venv at {venv_dir}")
    subprocess.run(
        [python, "-m", "venv", str(venv_dir)],
        check=True,
        capture_output=True,
    )
    return bin_py
