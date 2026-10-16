# -*- coding: utf-8 -*-
"""首页（内联样式）手机端点按区域补丁：把导航链接从 18px 撑到 ≥44px。

下载页用的是 space/space.css（已打过补丁），首页是 3 段内联 <style>，得单独补。
"""
import re
import shutil
import time
from pathlib import Path

PAGE = Path("/opt/qbot_website/index.html")
MARK = "手机端实测补丁"

BLOCK = """
/* ---- 手机端实测补丁（390×844 真机尺寸量出来的，2026-10-02）----
   导航链接原来只有 18px 高，手指点不准；Apple/Google 建议可点区域 ≥44px。
   只在小屏生效，不改排布与配色。 */
@media (max-width:640px){
  header.nav a, nav.links a, .nav a, header a{
    display:inline-flex; align-items:center; min-height:44px; padding:0 8px;
  }
  .cbtn, .pill, .cbtns button, .cbtn.primary{
    min-height:44px; display:inline-flex; align-items:center; justify-content:center;
  }
  footer a{min-height:44px; display:inline-flex; align-items:center}
}
"""


def main() -> None:
    raw = PAGE.read_text(encoding="utf-8")
    if MARK in raw:
        print("已经补过了，跳过")
        return
    blocks = list(re.finditer(r"</style>", raw))
    if not blocks:
        raise SystemExit("index.html 里没有 <style>，先看页面结构再改")
    stamp = time.strftime("%Y%m%d_%H%M")
    shutil.copy2(PAGE, PAGE.with_name(f"index.html.bak_{stamp}_mobiletap"))
    pos = blocks[-1].start()          # 插在最后一段样式的末尾
    new = raw[:pos] + BLOCK + raw[pos:]
    PAGE.write_text(new, encoding="utf-8")
    print("备份后缀:", stamp)
    print("新长度:", len(new), "（原", len(raw), "）")
    print("样式段数:", len(blocks))


if __name__ == "__main__":
    main()
