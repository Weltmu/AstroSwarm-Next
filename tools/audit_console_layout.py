# -*- coding: utf-8 -*-
"""桌面端 ↔ 控制台：**页面分区对比**（比功能对齐更细，管版式与文案）。

功能对齐看 `test_console_parity_matrix.py`；这份工具看"每个页面上有哪些区块、
什么顺序、标题叫什么"——老板截图说"ui没对齐"，指的就是这个。

桌面端：读 ui/pages/*.py 里的可见文案（QLabel / QGroupBox / QPushButton / 标题），按出现顺序。
控制台：读 console/src/App.jsx 里每个页面函数返回的 JSX 标题（<h2>/<h3>/card__title/rows 的 key…），按出现顺序。

只读对比，不改任何文件。
"""
import re
import sys
from pathlib import Path

ROOT = Path(r"D:\ai\QBotManager")
PAGES = ROOT / "src" / "qbotmanager" / "ui" / "pages"
APP_JSX = Path(r"D:\ai\AstroSwarm-public\console\src\App.jsx")

# 桌面页面文件 → 控制台页面函数（同一个界面对齐单位）
PAIRS = {
    "home_page.py": "Overview",
    "access_page.py": "AccessPage",
    "channel_panels.py": "QQBody",       # 通道细节：QQ 卡的实现
    "plugins_page.py": "Plugins",
    "workshop_page.py": "WorkshopPage",
    "brain_page.py": "AiBrain",
    "services_page.py": "ServicesPage",
    "message_center_page.py": "MessageCenter",
    "logs_page.py": "Logs",
    "deps_page.py": "Deps",
    "settings_page.py": "Settings",
}

VISIBLE = re.compile(
    r'(?:QGroupBox|QLabel|QPushButton|QCheckBox|QRadioButton|addTab)\("([^"]{2,40})"'
)


def desktop_sections(path: Path, limit: int = 26) -> list:
    txt = path.read_text(encoding="utf-8", errors="replace")
    out = []
    for m in VISIBLE.finditer(txt):
        text = m.group(1).strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def console_sections(func_name: str, limit: int = 26) -> list:
    """抠出控制台某个页面组件里的 JSX 文案（标题/按钮/说明）。

    组件可能不在 App.jsx（WorkshopPage.jsx / MarketPanel.jsx 等分文件），
    所以把所有 .jsx 都扫一遍找它。
    """
    if not APP_JSX.exists():
        return []
    src = ""
    for path in [APP_JSX] + sorted(APP_JSX.parent.glob("*.jsx")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(rf"(?:function|const)\s+{func_name}\b", text):
            src = text
            break
    if not src:
        return []
    m = re.search(rf"(?:function|const)\s+{func_name}\b", src)
    if not m:
        return []
    start = m.start()
    nxt = re.search(r"\n(?:function|const)\s+[A-Z]\w*\s*[=(]", src[start + 10:])
    body = src[start:start + 10 + (nxt.start() if nxt else len(src) - start - 10)]
    out = []
    for pat in (r"<h[1-4][^>]*>([^<>{]{2,40})</h[1-4]>",
                r'className="[^"]*card__title[^"]*"[^>]*>([^<>{]{2,40})<',
                r">\s*([\u4e00-\u9fff][^<>{]{1,30})\s*<"):
        for mm in re.finditer(pat, body):
            text = mm.group(1).strip()
            if text and text not in out:
                out.append(text)
            if len(out) >= limit:
                return out
    return out


def main() -> None:
    print("页面分区对比（桌面端 → 控制台）\n")
    for py_name, js_name in PAIRS.items():
        path = PAGES / py_name
        if not path.exists():
            continue
        desk = desktop_sections(path)
        cons = console_sections(js_name) if js_name else []
        print(f"=== {py_name}" + (f"  ↔  {js_name}" if js_name else "（并入接入页）"))
        print(f"  桌面端 {len(desk)} 项：{ ' / '.join(desk[:14]) }")
        if js_name:
            print(f"  控制台 {len(cons)} 项：{ ' / '.join(cons[:14]) }")
            only_desk = [x for x in desk if x not in cons]
            print(f"  只在桌面端有：{ ' / '.join(only_desk[:10]) }")
        print()


if __name__ == "__main__":
    sys.exit(main())
