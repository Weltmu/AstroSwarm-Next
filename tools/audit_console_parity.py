# -*- coding: utf-8 -*-
"""桌面端 vs Linux 控制台：功能差距清单（不猜，按代码列）。

产出：桌面端每个页面的功能点，对应控制台有没有后端接口 / 前端页面。
"""
import re
from pathlib import Path

ROOT = Path(r"D:\ai\QBotManager")
CONSOLE = Path(r"D:\ai\AstroSwarm-public\console\src")
UI = ROOT / "src" / "qbotmanager" / "ui" / "pages"
EXT = ROOT / "src" / "astroswarm_linux" / "console_ext.py"
API = ROOT / "src" / "astroswarm_linux" / "api.py"


def console_endpoints() -> list:
    out = []
    for path in (EXT, API):
        if not path.exists():
            continue
        txt = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'@app\.(get|post|put|delete)\("([^"]+)"', txt):
            out.append((m.group(1).upper(), m.group(2)))
    return sorted(set(out), key=lambda x: x[1])


def desktop_pages() -> dict:
    out = {}
    for path in sorted(UI.glob("*.py")):
        txt = path.read_text(encoding="utf-8", errors="replace")
        # 页面里的可见按钮/开关，作为"功能点"的近似
        feats = set()
        for m in re.finditer(r'(?:QPushButton|QCheckBox|QRadioButton)\("([^"]{2,40})"', txt):
            feats.add(m.group(1))
        out[path.name] = sorted(feats)
    return out


def console_components() -> list:
    return sorted(p.name for p in CONSOLE.glob("*.jsx"))


def main() -> None:
    eps = console_endpoints()
    pages = desktop_pages()
    comps = console_components()

    print(f"控制台后端接口 {len(eps)} 个；桌面端页面 {len(pages)} 个；控制台组件 {len(comps)} 个\n")

    print("== 桌面端各页面的功能点（按钮/开关）数量 ==")
    for name, feats in sorted(pages.items(), key=lambda kv: -len(kv[1])):
        print(f"  {name:28} {len(feats):3} 项")

    print("\n== 控制台接口清单 ==")
    for method, path in eps:
        print(f"  {method:6} {path}")

    print("\n== 控制台组件 ==")
    print("  " + ", ".join(comps))

    # 粗判：桌面端某功能关键词有没有对应接口
    keywords = {
        "飞书": ("feishu",),
        "纸飞机": ("telegram",),
        "QQ 官方/OneBot": ("qq",),
        "微信": ("wechat", "wx"),
        "日志保留": ("log", "keep"),
        "依赖安装": ("dep",),
        "插件市场": ("market", "plugin"),
        "工坊": ("workshop",),
        "知识库": ("knowledge",),
        "记忆": ("memory",),
        "消息中心": ("message",),
        "账号": ("account", "auth"),
        "开机自启": ("autostart",),
        "端口": ("port",),
        "更新/升级": ("update",),
        "视频工具": ("video",),
        "人格": ("persona",),
        "模型列表": ("model",),
    }
    print("\n== 按功能关键词看接口覆盖 ==")
    for label, keys in keywords.items():
        hits = [p for _m, p in eps if any(k in p.lower() for k in keys)]
        print(f"  {label:14} 接口 {len(hits):2} 个  {hits[:4]}")


if __name__ == "__main__":
    main()
