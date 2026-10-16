# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_all

# QML 与 Qt 插件必须跟「当前构建环境」里的 PySide6 走。
# 这里曾经写死 `.build_venv/Lib/site-packages/PySide6/...`，而那个 venv 是 PySide6 6.8.3
# 的残骸（连 python.exe 都没有），于是 6.8 的 QML / 多媒体插件被混进 6.11 的包里，
# 打出来的 exe 一启动就 "DLL load failed while importing QtCore: 找不到指定的程序"。
_PYSIDE = os.path.dirname(__import__("PySide6").__file__)

datas = [
    ('src/qbotmanager/ui/bg.qml', 'qbotmanager/ui'),
    ('src/qbotmanager/ui/bg.qml', '.'),
      ('src/qbotmanager/assets/fonts', 'qbotmanager/assets/fonts'),
      ('src/qbotmanager/assets/agreements', 'qbotmanager/assets/agreements'),
      ('src/qbotmanager/assets/plans.json', 'qbotmanager/assets'),
      ('src/qbotmanager/assets/wizard_bg.jpg', 'qbotmanager/assets'),
    ('src/qbotmanager/assets/plugins/nonebot_adapter_ilink', 'qbotmanager/assets/plugins/nonebot_adapter_ilink'),
    ('src/qbotmanager/assets/plugins/ai', 'qbotmanager/assets/plugins/ai'),
    ('src/qbotmanager/assets/plugins/liqinghan', 'qbotmanager/assets/plugins/liqinghan'),
    ('src/qbotmanager/assets/plugins/qbm_bridge_client', 'qbotmanager/assets/plugins/qbm_bridge_client'),
    # 随软件自带的能力包（连续对话包等）：<机器人根>/tool_packs 由 ensure_* 同步过去
    ('src/qbotmanager/assets/tool_packs', 'qbotmanager/assets/tool_packs'),
    ('src/qbotmanager/core/agent', 'qbotmanager/core/agent'),
    # 打包版 AgentRuntime 源码副本（qbotmanager 模块在 PYZ，无法从磁盘读；放 appr/ 下由 ensure_agent_runtime 拷贝）
    # ⚠ 2026-10-15：这里以前是**逐个文件点名**，于是每新增一个 core 模块就漏一次，
    #    每次症状都是"网关连上了却不回消息"：
    #      v1.2.9 漏 core/agent_profile.py；
    #      2026-10-04 漏 core/settings.py 与 core/workshop（skill_forge 自学要用）；
    #      2026-10-11 漏 core/memory（装机版记忆三档静默失效）；
    #      2026-10-12 漏 assets/plugins/ai（跨平台发送报「找不到 qbotmanager.assets」）。
    #    病根是"得记得加一行"。现改成**整目录复制**，与 ensure_agent_runtime() 的
    #    整目录同步逻辑对齐：以后 core/ 下新增任何模块都自动带上，不必再动这里。
    ('src/qbotmanager/__init__.py', 'appr/qbotmanager'),
    ('src/qbotmanager/core', 'appr/qbotmanager/core'),
    # runtime 会 import 插件的 cross_platform/skill_forge，整目录带上
    ('src/qbotmanager/assets/plugins/ai', 'appr/qbotmanager/assets/plugins/ai'),
    ('src/qbotmanager/assets/dsh_plugins', 'qbotmanager/assets/dsh_plugins'),
    ('src/qbotmanager/assets/llbot_tutorial', 'qbotmanager/assets/llbot_tutorial'),
    ('src/qbotmanager/assets/persona_template', 'qbotmanager/assets/persona_template'),
    (os.path.join(_PYSIDE, 'qml', 'Qt5Compat'), 'PySide6/qml/Qt5Compat'),
    (os.path.join(_PYSIDE, 'qml', 'QtMultimedia'), 'PySide6/qml/QtMultimedia'),
    (os.path.join(_PYSIDE, 'qml', 'QtQuick'), 'PySide6/qml/QtQuick'),
    (os.path.join(_PYSIDE, 'qml', 'QtQml'), 'PySide6/qml/QtQml'),
    (os.path.join(_PYSIDE, 'plugins', 'multimedia'), 'PySide6/plugins/multimedia'),
]
binaries = []
hiddenimports = ['_cffi_backend']

for _pkg in (
    'PySide6.QtMultimedia',
    'PySide6.QtMultimediaWidgets',
    'PySide6.QtQml',
    'PySide6.QtQuick',
    'pynacl',
    'nacl',
    'cffi',
):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

a = Analysis(
    ['src/main.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# ---- 剔掉「构建机污染」的 DLL（这次发版踩的坑就在这里）----
# PyInstaller 会顺着 PATH 把构建机上装的东西卷进包里。这台机器上 Codex 运行时缓存
# （构建机上装着的 Codex 运行时缓存里的 poppler 与 libheif）就被卷进来过，后果是包里多出一个
# 顶层的 icuuc.dll（ICU 78，poppler 带来的）+ icudt78.dll（33MB）。
# 而 PySide6 的 Qt6Core.dll 要的是 Windows 自带的 icuuc.dll，被顶层这个顶掉之后，
# 用户机一启动就报 "ImportError: DLL load failed while importing QtCore: 找不到指定的程序"。
# 这些 DLL 跟星群没有任何关系，另外 ucrtbase.dll / api-ms-win-*.dll 这类系统垫片也一律不进包。
def _is_host_junk(dest, src):
    s = str(src or "").replace("/", "\\").lower()
    base = str(dest or "").replace("/", "\\").lower().rsplit("\\", 1)[-1]
    if "codex-runtimes" in s:
        return True
    if base == "ucrtbase.dll" or base.startswith("api-ms-win-"):
        return True
    return False


_keep, _junk = [], []
for _t in a.binaries:
    (_junk if _is_host_junk(_t[0], _t[1]) else _keep).append(_t)
a.binaries = _keep
if _junk:
    print("[spec] 剔除构建机污染 DLL %d 个: %s" % (len(_junk), sorted({t[0] for t in _junk})))

pyz = PYZ(a.pure)

# ---- v1.2.16：onefile → onedir（冷启动 22~30 秒 → 2 秒上下）----
# onefile 每次启动都要把 ~451 MB 解压到 %TEMP%\_MEIxxxxxx，冷启动实测 22~30 秒，
# 被任务管理器强杀还会在用户盘上留 450 MB 残留。改 onedir 后 exe 直接从
# dist\AstroSwarm\_internal\ 加载，不再解压。
# 用户侧安装体验不变：Inno 把 dist\AstroSwarm\ 整目录铺进 {app}，
# 还是下载一个 AstroSwarm_Setup.exe 双击安装（便携版从单个 exe 变成一个文件夹）。
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AstroSwarm',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='D:/ai/QBotManager/assets/logo.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AstroSwarm',
)
