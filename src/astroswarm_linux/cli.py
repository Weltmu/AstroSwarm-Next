"""Headless CLI：status / serve 占位。"""
import argparse
import sys
from pathlib import Path

from . import headless_config, platform_info


def _pin_data_home() -> None:
    """把账号/头像/许可证的目录钉在程序自己的数据目录里（<data>/astroswarm）。

    没有它，core 里那套按 %APPDATA% 算路径的逻辑在 Linux 上会写进
    /root/AppData/Roaming/QBotManager（备份会漏）。要在 import 业务模块之前调用。
    """
    import os

    if os.name == "nt" or os.environ.get("ASTROSWARM_DATA_HOME"):
        return
    try:
        from . import platform_info

        os.environ["ASTROSWARM_DATA_HOME"] = str(platform_info.data_home() / "astroswarm")
    except Exception:  # noqa: BLE001 —— 设不上就退回默认，不阻塞启动
        pass


def _describe() -> str:
    """--help 顶部那句（软件名 = 星巢 Nest；「星群」是网站品牌，别写到这里）。"""
    try:
        from . import __version__

        ver = f"（v{__version__}）"
    except Exception:  # noqa: BLE001 —— 版本读不到不影响用法说明
        ver = ""
    return f"星巢 Nest{ver} · Linux 无头端：在本机跑控制 API（网页控制台 + 机器人管理）"


def main(argv=None) -> int:
    _pin_data_home()
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="astroswarm",  # ⚠ 可执行文件名不变（bin/astroswarm），只改给用户看的说明
        description=_describe(),
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("status", help="显示平台信息（星巢 Nest 无头端）")
    serve = sub.add_parser("serve", help="启动控制 API（网页控制台，默认端口 7860）")
    serve.add_argument("--port", type=int, default=7860)
    serve.add_argument("--host", default="127.0.0.1")
    install = sub.add_parser("install", help="初始化配置（可选安装 systemd 自启）")
    install.add_argument("--port", type=int, default=7860)
    install.add_argument("--systemd", action="store_true")
    sub.add_parser("deploy", help="部署机器人运行时（venv + 依赖）")
    reset = sub.add_parser(
        "console-reset-password",
        help="忘记控制台密码时用：清掉密码与所有登录会话（只有能登服务器的人能做到）",
    )
    reset.add_argument(
        "--password",
        default="",
        help="可选：直接设成这个新密码（不给就只清除，下次打开控制台会让你重新设置）",
    )
    reset.add_argument(
        "--yes",
        action="store_true",
        help="不再确认，直接执行",
    )
    args = parser.parse_args(argv)
    if args.cmd == "status":
        print(
            f"星巢 Nest · machine_id={platform_info.machine_id()}"
        )
        return 0
    if args.cmd == "serve":
        import uvicorn

        from .api import app

        print(f"星巢 Nest（AstroSwarm 无头端）控制 API：http://{args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return 0
    if args.cmd == "install":
        cfg = headless_config.load()
        cfg["port"] = args.port
        headless_config.save(cfg)
        print(f"配置已写入 {headless_config.config_path()}")
        if args.systemd:
            from .autostart import install as sys_install
            from .autostart import unit_text

            sys_install(
                "astroswarm.service",
                unit_text(Path("/opt/astroswarm"), args.port),
                Path("/etc/systemd/system"),
                enable=True,
            )
            print("systemd 自启已安装：astroswarm.service（星巢 Nest 无头端）")
        return 0
    if args.cmd == "deploy":
        from .deploy import ensure_bot

        ensure_bot(log=print)
        print("机器人运行时部署完成")
        return 0
    if args.cmd == "console-reset-password":
        return _reset_console_password(args.password, assume_yes=args.yes)
    parser.print_help()
    return 0


def _reset_console_password(new_password: str, *, assume_yes: bool = False) -> int:
    """忘记控制台密码时的救援入口。

    老板 2026-10-10 问"用不用做个忘记密码？在本地服务器上输指令来解决？"——
    对，就该这么做：**只有能登录服务器（SSH/本机）的人才能重置**，
    网页上不提供任何"找回"通道，免得被人从公网重置。
    """
    from . import console_session

    if not console_session.is_password_set() and not new_password:
        print("这台服务器还没有设置过控制台密码，直接打开控制台设置即可。")
        return 0

    if not assume_yes:
        tip = (
            f"将把控制台密码设成你给的新密码，并作废所有已登录的设备。"
            if new_password
            else "将清除控制台密码，并作废所有已登录的设备（下次打开控制台会让你重新设置）。"
        )
        print(tip)
        try:
            answer = input("确定继续？输入 yes 回车：").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes", "是", "确定"):
            print("已取消，什么都没改。")
            return 1

    # 先校验新密码，**再**动手清旧的 —— 否则给了个弱密码就会先把旧密码清掉、
    # 然后设置失败，人就被锁在门外了（这条是实测发现的）。
    if new_password:
        try:
            problem = console_session.password_rules(new_password)
        except Exception as exc:  # noqa: BLE001
            problem = str(exc)
        if problem:
            print(f"重置失败：{problem}（旧密码未改动）")
            return 2

    try:
        if new_password:
            # 救援场景：旧密码已经不知道了，所以先清掉再设新的
            console_session.clear_password()
            console_session.set_password(new_password)
            print("新密码已设置 ✓")
        else:
            console_session.clear_password()
            print("控制台密码已清除 ✓")
        removed = console_session.revoke_all()
        print(f"已作废 {removed} 个登录会话（所有设备都需要重新登录）。")
    except Exception as exc:  # noqa: BLE001
        print(f"重置失败：{exc}")
        return 2

    if new_password:
        print("现在可以直接用新密码打开控制台。")
    else:
        print("现在打开控制台，会让你重新设置密码。")
        print("提示：想直接设一个新密码，用 "
              "astroswarm console-reset-password --password 你的新密码")
    return 0


if __name__ == "__main__":
    sys.exit(main())
