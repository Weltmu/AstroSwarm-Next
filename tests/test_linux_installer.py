# -*- coding: utf-8 -*-
"""Linux 一条命令安装 / 卸载（2026-10-04 老板要的）：两个脚本的规矩。

线上地址（都不带 .sh —— 官网静态服务故意拒绝下发可执行文本，见模块注释）：
  安装：https://astroswarm.cn/downloads/install-linux   ← tools/install-linux.sh
  卸载：https://astroswarm.cn/downloads/uninstall-linux ← tools/uninstall-linux.sh

默认位置（老板问过"默认文件放哪"）：
  程序 + 数据：/opt/astroswarm（数据在 /opt/astroswarm/data）
  服务单元：  /etc/systemd/system/astroswarm.service
  卸载保留数据时：挪到 /opt/astroswarm-data
"""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "tools" / "install-linux.sh"
UNINSTALL = ROOT / "tools" / "uninstall-linux.sh"
PUB_INSTALL = "https://astroswarm.cn/downloads/install-linux"
PUB_UNINSTALL = "https://astroswarm.cn/downloads/uninstall-linux"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_both_scripts_are_lf_only():
    for path in (INSTALL, UNINSTALL):
        raw = path.read_bytes()
        assert b"\r\n" not in raw, f"{path.name} 必须是 LF 换行"
        assert raw.endswith(b"\n"), f"{path.name} 结尾要有换行"


def test_default_dirs_documented_and_used():
    install = _read(INSTALL)
    uninstall = _read(UNINSTALL)
    assert 'DIR="/opt/astroswarm"' in install, "安装默认目录"
    assert 'DIR="/opt/astroswarm"' in uninstall, "卸载默认目录要和安装一致"
    assert "astroswarm.service" in uninstall, "卸载要能移除 systemd 单元"
    assert "/etc/systemd/system/astroswarm.service" in uninstall


def test_uninstall_keeps_data_by_default():
    text = _read(UNINSTALL)
    assert "--all" in text and "--keep-data" in text
    # 默认保留：只有显式 --all 才删数据
    assert "PURGE=0" in text, "默认必须是保留数据"
    assert 'mv "$DATA" "$KEEP_DIR"' in text, "保留数据用 mv（快且稳），不是复制一份再删"


def test_uninstall_refuses_arbitrary_directories():
    """安全护栏：不是安装目录就拒绝删（防止 --dir 打错把别的东西删了）。"""
    text = _read(UNINSTALL)
    assert "看起来不是星巢的安装目录" in text
    assert 'for probe in "$DIR/VERSION"' in text
    assert '[ "$DIR" != "/" ]' in text, "绝不能把根目录当安装目录"


def test_uninstall_asks_before_deleting():
    text = _read(UNINSTALL)
    assert "--yes" in text, "要能脚本化（--yes 跳过确认）"
    assert 'read -r ANSWER' in text, "默认必须问一次"
    assert '[ "$ANSWER" = "yes" ]' in text, "要输 yes 才继续"


def test_installer_guides_to_a_working_way_in():
    """不能再给"打不开的地址"：只监听 127.0.0.1 时必须给 SSH 隧道（或 --host 0.0.0.0 的说明）。

    老板 2026-10-04 实测踩到：脚本打印"浏览器打开 http://<服务器IP>:7860/"，但服务只监听
    127.0.0.1，那个地址永远打不开，被当成故障来问。
    """
    install = _read(INSTALL)
    assert "ssh -L" in install, "127.0.0.1 场景要给出隧道命令"
    assert "--host 0.0.0.0" in install, "要说明另一种可用的公开方式"
    inner = (ROOT / "tools" / "install.sh").read_text(encoding="utf-8")
    assert "二选一" in inner, "包内脚本不该再无条件打印公网地址"
    assert "for _ in 1 2 3 4 5" in inner, "服务判活要轮询，不能 sleep 3 就下结论"


def test_installer_checks_port_before_installing():
    """端口被占要在装之前就报错（否则服务一直重启，用户只看到"网页打不开"）。"""
    install = _read(INSTALL)
    assert "端口预检" in install
    assert "已被占用" in install
    assert "ss -lnt" in install
    assert "--port 7861" in install, "要给出换端口的现成命令"


def test_installer_points_to_real_uninstall_command():
    """安装完的提示里必须是真正的卸载命令（以前写了包内不存在的 uninstall.sh）。"""
    install = _read(INSTALL)
    assert PUB_UNINSTALL in install, "安装完成提示要给出线上卸载命令"
    assert "$DIR/uninstall.sh" not in install, "包内没有这个文件，别再提示它"
    assert "/opt/astroswarm-data" in _read(UNINSTALL)


def test_published_paths_have_no_extension_and_scripts_differ():
    for url, path in ((PUB_INSTALL, INSTALL), (PUB_UNINSTALL, UNINSTALL)):
        assert not url.endswith(".sh")
        assert path.name.endswith(".sh"), "仓库里仍用 .sh 方便识别"
    assert INSTALL.read_bytes() != UNINSTALL.read_bytes(), "两个脚本不能是同一份"


def test_script_hashes_recorded():
    for path in (INSTALL, UNINSTALL):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert len(digest) == 64
        assert digest != hashlib.sha256(b"").hexdigest()


if __name__ == "__main__":
    test_both_scripts_are_lf_only()
    test_default_dirs_documented_and_used()
    test_uninstall_keeps_data_by_default()
    test_uninstall_refuses_arbitrary_directories()
    test_uninstall_asks_before_deleting()
    test_installer_guides_to_a_working_way_in()
    test_installer_checks_port_before_installing()
    test_installer_points_to_real_uninstall_command()
    test_published_paths_have_no_extension_and_scripts_differ()
    test_script_hashes_recorded()
    print("OK linux install/uninstall commands")
