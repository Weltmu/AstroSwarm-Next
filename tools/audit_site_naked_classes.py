# -*- coding: utf-8 -*-
"""全站体检：HTML 里用了、但任何 CSS 都没定义的 class（"裸标签"）。

为什么做这个：2026-10-04 一天内被同一类问题咬了两次 ——
  ① 导航头像 .navav：脚本生成了 <img>，space.css 里没有尺寸 → 头像按 512 原图撑爆；
  ② 个人中心 .me__av：页面写了整套 me__* 类，任何地方都没定义 → 1024 头像撑爆整页。
两者都是"标签有、样式没有"，静态扫一遍就能提前发现。
"""
import re
from pathlib import Path

ROOT = Path("/opt/qbot_website")
SKIP_DIRS = {"app", "assets", "downloads", ".coord", "space"}


def collect_classes(html: str) -> dict:
    out = {}
    for m in re.finditer(r'class="([^"]+)"', html):
        for cls in m.group(1).split():
            out.setdefault(cls, 0)
            out[cls] += 1
    # JS 里动态加的类（classList.add / innerHTML 模板）也算"被用到"
    for m in re.finditer(r'classList\.add\(([^)]*)\)', html):
        for lit in re.findall(r'"([^"]+)"', m.group(1)):
            out.setdefault(lit, 0)
            out[lit] += 1
    return out


def defined_selectors(css: str) -> set:
    names = set()
    for m in re.finditer(r'\.([A-Za-z_][\w-]*)', css):
        names.add(m.group(1))
    return names


def main() -> None:
    css_all = []
    for css_file in list((ROOT / "space").glob("*.css")):
        css_all.append(css_file.read_text(encoding="utf-8", errors="replace"))
    shared = defined_selectors("\n".join(css_all))

    pages = [p for p in sorted(ROOT.glob("*.html")) if p.name not in SKIP_DIRS]
    report = []
    for page in pages:
        html = page.read_text(encoding="utf-8", errors="replace")
        # 页面自己的内联样式
        inline = "\n".join(re.findall(r"(?is)<style[^>]*>(.*?)</style>", html))
        local = defined_selectors(inline)
        # 页面引用的 js 里也可能注入类名（space.js 等）
        used = collect_classes(html)
        missing = {c: n for c, n in used.items()
                   if c not in shared and c not in local}
        if missing:
            report.append((page.name, missing))

    print(f"扫描 {len(pages)} 个页面，公共 CSS 定义 {len(shared)} 个类\n")
    for name, missing in report:
        items = ", ".join(f"{c}({n})" for c, n in sorted(missing.items(), key=lambda kv: -kv[1]))
        print(f"== {name}：{len(missing)} 个没定义的类")
        print("   " + items[:400])
    if not report:
        print("没有发现裸标签 ✓")


if __name__ == "__main__":
    main()
