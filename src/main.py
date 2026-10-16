# 高分屏：不再关闭 Qt 高分缩放（旧补丁只治标，反而导致高分屏文字发糊）。
# 坐标与边缘拖拽已由 app.py 的 PassThrough 缩放策略 + nativeEvent DPR 换算处理。

# 更新保护（2026-10-12 老板实机踩到）：安装器还在覆盖文件时把程序拉起来，
# 会 "DLL load failed while importing QtWidgets" 弹窗。启动时只要发现安装器
# 还在跑（且不是 --cli 调用），提示一句就退出；装完由安装脚本把程序拉起来。
import sys as _sys

if "--cli" not in _sys.argv and "/cli" not in _sys.argv:
    try:
        from qbotmanager.core import updater as _updater

        if _updater.update_pending():
            _updater.warn_update_pending()
            _sys.exit(0)
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 —— 防护本身绝不能挡住正常启动
        pass

from qbotmanager.app import main

if __name__ == "__main__":
    main()
