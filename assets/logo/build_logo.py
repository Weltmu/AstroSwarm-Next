# -*- coding: utf-8 -*-
"""星巢 Nest 新 logo：去水印 + 蓝底→白底（洪水填充 + 边缘去蓝），导出 1024/512/256 与多尺寸 ICO。

用法：python build_logo.py
输入：assets/logo/nest-logo-source.png（原图只读副本）
输出：assets/logo/nest-logo.png / -512.png / -256.png、assets/logo.ico、对比图
"""
import sys
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.stdout.reconfigure(encoding="utf-8")

LOGO_DIR = Path(r"D:\ai\QBotManager\assets\logo")
SRC = LOGO_DIR / "nest-logo-source.png"
OUT1024 = LOGO_DIR / "nest-logo.png"
OUT512 = LOGO_DIR / "nest-logo-512.png"
OUT256 = LOGO_DIR / "nest-logo-256.png"
ICO = Path(r"D:\ai\QBotManager\assets\logo.ico")
COMPARE = Path(r"D:\Deepseek harness\workspace") / "星巢logo-白底对比.png"

# 参数（写下理由，方便以后调）：
#  · 背景是极均匀的深蓝 #233569（四角 40x40 均值 (36,52,105)）
#    实测"距背景色的欧氏距离"是干净的双峰：80.5% 的像素 d<20（背景，散布±5），
#    枝条从 d>=76 起步，中间 20~76 只有零星抗锯齿像素（合计 0.8%）。
#    → 用**全局距离阈值**切背景，比逐像素链式 BFS 稳得多：
#      链式容差会顺着抗锯齿梯度一格格爬进画面，实测 tol=45 会吃掉 99.5%（把 logo 也吃了）。
#      （2026-10-10 踩过，写在这里免得重蹈。）
BG_CAP = 20.0       # d<=20 判为背景（含孤立小口袋，不依赖连通性）
DEBLUE_MAX = 120.0  # 边缘去蓝：距背景色多近才允许"去蓝"
DEBLUE_BAND = 6     # 背景外沿多少层像素参与去蓝（实测过渡带 1~3 px，取 6 留余量）
DEBLUE_T0 = 45.0    # 比这更近 → 直接刷白（几乎是背景色，只是没被阈值抓到）
DEBLUE_T1 = 120.0   # T0~T1 之间：按"有多像背景蓝"线性朝白混，最远处只混 8%
WM_BOX = (8, 950, 130, 1012)   # 水印扫描区：实测笔画 x17..112 / y966..997，四周各留约 9~15px
WM_DARK_DIST = 20.0  # 水印区里距背景色超过这个距离 = 水印笔画
WM_GUARD_X = 170     # 安全线：logo 最左的枝条在 x>=170（实测 y>=940 区域里 x<200 无暖色像素），
                     # 水印区域整个在 x<=130，绝不重叠 —— 所以这里可以放心整块刷白。


def srgb_dist(a, b):
    """sRGB 里的欧氏距离，够用且直观。"""
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def build_mask(im, bg, cap):
    """背景掩码：与背景色的欧氏距离 <= cap。背景在整幅图里是连通的，
    但这里不依赖连通性 —— 枝条缝隙里的"洞"也一并算背景，正是我们要的。"""
    W, H = im.size
    px = im.load()
    mask = bytearray(W * H)
    for y in range(H):
        row = y * W
        for x in range(W):
            if srgb_dist(px[x, y], bg) <= cap:
                mask[row + x] = 1
    return mask


def main():
    im = Image.open(SRC).convert("RGB")
    W, H = im.size
    px = im.load()

    # 背景参考色 = 四角 40x40 的均值
    acc = [0, 0, 0]
    cnt = 0
    for bx, by in ((0, 0), (W - 40, 0), (0, H - 40), (W - 40, H - 40)):
        for y in range(by, by + 40):
            for x in range(bx, bx + 40):
                r, g, b = px[x, y]
                acc[0] += r; acc[1] += g; acc[2] += b; cnt += 1
    bg = tuple(round(v / cnt) for v in acc)
    print(f"[bg] 四角均值 = {bg}  (全局距离阈值 BG_CAP={BG_CAP})")

    # ---- 1) 背景掩码 ----
    mask = build_mask(im, bg, BG_CAP)
    n_bg = sum(mask)
    print(f"[fill] 背景 {n_bg} px ({n_bg * 100.0 / (W * H):.2f}%)")

    # ---- 2) 水印：矩形内凡是"不像背景"的一律并进背景掩码 ----
    wx0, wy0, wx1, wy1 = WM_BOX
    wm_px_before = []
    for y in range(wy0, wy1):
        for x in range(wx0, wx1):
            c = px[x, y]
            if srgb_dist(c, bg) > WM_DARK_DIST:
                wm_px_before.append((x, y, c))
            mask[y * W + x] = 1          # 整块矩形直接刷白
    # 外扩 2px，保证不留半透明残影
    for x, y, _ in list(wm_px_before):
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                nx, ny = x + dx, y + dy
                if 0 <= nx < W and 0 <= ny < H:
                    mask[ny * W + nx] = 1
    print(f"[watermark] 扫描区 {WM_BOX}，区内非背景像素 {len(wm_px_before)} px，矩形+外扩 2px 全部刷白")
    if wm_px_before:
        xs = [x for x, _, _ in wm_px_before]
        ys = [y for _, y, _ in wm_px_before]
        print(f"[watermark] 实测笔画范围 x {min(xs)}..{max(xs)}  y {min(ys)}..{max(ys)}"
              f"（安全线 x<{WM_GUARD_X}，"
              f"{'不越界 OK' if max(xs) < WM_GUARD_X else '越界！会碰到枝条！'}）")
        print(f"[watermark] 最深笔画色 {min(wm_px_before, key=lambda t: sum(t[2]))[2]}")

    # 安全校验：水印矩形里不能有暖色（枝条）像素，否则刷白会啃掉枝条
    warm_in_box = 0
    for y in range(wy0, wy1):
        for x in range(wx0, wx1):
            r, g, b = px[x, y]
            if r > b:                     # 枝条是暖棕，r>b；背景蓝是 b>r
                warm_in_box += 1
    print(f"[watermark] 安全校验：矩形内暖色(枝条特征)像素 {warm_in_box} 个"
          f" {'（OK，纯背景+水印）' if warm_in_box == 0 else '（警告！这里可能有枝条）'}")

    n_bg2 = sum(mask)
    print(f"[fill] 并水印后掩码 {n_bg2} px ({n_bg2 * 100.0 / (W * H):.2f}%)")

    # ---- 3) 建新图：背景 → 纯白 ----
    out = Image.new("RGB", (W, H), (255, 255, 255))
    op = out.load()
    for y in range(H):
        row = y * W
        for x in range(W):
            if not mask[row + x]:
                op[x, y] = px[x, y]

    # ---- 4) 边缘去蓝：背景外沿 DEBLUE_BAND 层里"仍偏蓝"的像素朝白混 ----
    dist = bytearray(W * H)          # 距背景的步数，0 = 背景本身
    q = deque()
    for y in range(H):
        row = y * W
        for x in range(W):
            if mask[row + x]:
                q.append((x, y))
    while q:
        x, y = q.popleft()
        d = dist[y * W + x]
        if d >= DEBLUE_BAND:
            continue
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H:
                i = ny * W + nx
                if mask[i] or dist[i]:
                    continue
                dist[i] = d + 1
                q.append((nx, ny))
    blended = 0
    for y in range(H):
        row = y * W
        for x in range(W):
            i = row + x
            if mask[i] or not dist[i]:
                continue
            c = op[x, y]
            r, g, b = c
            if not (b > r and b > g):        # 不偏蓝：是枝条本身，别动
                continue
            dd = srgb_dist(c, bg)
            if dd > DEBLUE_MAX:
                continue
            if dd <= DEBLUE_T0:
                t = 1.0
            else:
                t = 0.08 + 0.92 * (DEBLUE_T1 - dd) / (DEBLUE_T1 - DEBLUE_T0)
            op[x, y] = (
                round(r + (255 - r) * t),
                round(g + (255 - g) * t),
                round(b + (255 - b) * t),
            )
            blended += 1
    print(f"[deblue] 边缘去蓝像素 {blended} px"
          f"（band={DEBLUE_BAND}, T0={DEBLUE_T0} 刷白, T0~{DEBLUE_T1} 渐混）")

    # ---- 4b) 碎块清理 + 残留蓝灰中性化 ----
    # 蓝色分量的"纯蓝怪物"不会出现在这个地方（枝条是暖棕），所以这里可以放心地
    # 对"偏蓝"像素做保亮度中性化：把最大的通道值当亮度，压掉色偏（藏在枝缝里的
    # 深蓝阴影 (29,38,61) 会变成中性灰 (38,38,38)，白底上看着就是普通暗部）。
    # 另外：8 邻接下 <= SPECK_MAX 的偏蓝碎块直接刷白 —— 实测 382 块里 341 块只有 1px，
    # 留在白底上就是一层灰蒙蒙的沙点。
    SPECK_MAX = 200

    def is_blueish(c):
        return max(c) - min(c) >= 10 and c[2] == max(c) and srgb_dist(c, bg) < 120

    seen = bytearray(W * H)
    filled_total = 0
    neutral_total = 0
    for y0 in range(H):
        for x0 in range(W):
            i0 = y0 * W + x0
            if mask[i0] or seen[i0] or not is_blueish(op[x0, y0]):
                continue
            # 收集这一块
            stack = [(x0, y0)]
            seen[i0] = 1
            comp = []
            while stack:
                cx, cy = stack.pop()
                comp.append((cx, cy))
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < W and 0 <= ny < H:
                            ni = ny * W + nx
                            if not mask[ni] and not seen[ni] and is_blueish(op[nx, ny]):
                                seen[ni] = 1
                                stack.append((nx, ny))
            if len(comp) <= SPECK_MAX:
                for cx, cy in comp:
                    op[cx, cy] = (255, 255, 255)
                filled_total += len(comp)
            else:
                for cx, cy in comp:
                    c = op[cx, cy]
                    neutral_total += 1
                    m = max(c)
                    op[cx, cy] = tuple(round(v + (m - v) * 0.85) for v in c)
    print(f"[清理] 偏蓝碎块（<= {SPECK_MAX}px）刷白 {filled_total} px；"
          f"大块偏蓝像素中性化 {neutral_total} px")

    # ---- 4c) 残留蓝灰检查：白底上不该再有明显偏蓝的像素 ----
    residual = []
    for y in range(H):
        row = y * W
        for x in range(W):
            if mask[row + x]:
                continue
            r, g, b = op[x, y]
            if b > max(r, g) + 4 and srgb_dist((r, g, b), bg) < 120:
                residual.append((x, y, (r, g, b)))
    print(f"[残蓝] 白底上仍明显偏蓝的像素 {len(residual)} 个"
          + (f"（最蓝 {max(residual, key=lambda t: t[2][2]-max(t[2][0], t[2][1]))}）"
             if residual else ""))

    # ---- 5) 导出 ----
    out.save(OUT1024, "PNG", optimize=True)
    out.resize((512, 512), Image.LANCZOS).save(OUT512, "PNG", optimize=True)
    out.resize((256, 256), Image.LANCZOS).save(OUT256, "PNG", optimize=True)

    sizes = [16, 24, 32, 48, 64, 128, 256]
    out.save(ICO, format="ICO", sizes=[(s, s) for s in sizes])

    # ---- 6) 自证：采样点 ----
    def corner_means(img):
        p = img.load()
        w, h = img.size
        vals = []
        for bx, by in ((0, 0), (w - 40, 0), (0, h - 40), (w - 40, h - 40)):
            a = [0, 0, 0]
            n = 0
            for y in range(by, by + 40):
                for x in range(bx, bx + 40):
                    c = p[x, y]
                    a[0] += c[0]; a[1] += c[1]; a[2] += c[2]; n += 1
            vals.append((bx, by, tuple(round(v / n) for v in a)))
        return vals

    print("\n================ 自证 ================")
    print("[原图] 四角 40x40 均值：")
    for bx, by, c in corner_means(im):
        print(f"    ({bx},{by}) -> {c}")
    print("[新图] 四角 40x40 均值：")
    for bx, by, c in corner_means(out):
        print(f"    ({bx},{by}) -> {c}")

    op2 = out.load()
    print("\n[采样点] 原图 -> 新图：")
    for name, (x, y) in {
        "左上(2,2)": (2, 2), "右上(1021,2)": (W - 3, 2),
        "左下(2,1021)": (2, H - 3), "右下(1021,1021)": (W - 3, H - 3),
        "左上偏内(30,30)": (30, 30), "右上偏内(993,30)": (W - 31, 30),
        "左下偏内(30,993)": (30, H - 31), "右下偏内(993,993)": (W - 31, H - 31),
        "巢中心(512,512)": (512, 512), "枝条(300,300)": (300, 300),
        "背景中(200,200)": (200, 200), "背景中(400,150)": (400, 150),
        "水印区(40,972)": (40, 972), "水印区(60,975)": (60, 975),
        "水印区(90,985)": (90, 985), "水印区(100,960)": (100, 960),
    }.items():
        print(f"    {name}: {px[x, y]} -> {op2[x, y]}")

    # 水印区整块扫描
    region = out.crop(WM_BOX)
    rp = region.load()
    nonwhite = []
    for yy in range(region.size[1]):
        for xx in range(region.size[0]):
            c = rp[xx, yy]
            if c != (255, 255, 255):
                nonwhite.append((xx + wx0, yy + wy0, c))
    print(f"\n[水印区] 扫描区 {WM_BOX}，非纯白像素 {len(nonwhite)} 个")
    print(f"[水印区] 处理后区内最暗像素 {min((c for _, _, c in nonwhite), key=sum) if nonwhite else '无（全白）'}")

    # 枝条完好性：新图里"明显不是白色"的像素数（应≈原图非背景像素数）
    dark_out = 0
    for y in range(0, H, 2):
        for x in range(0, W, 2):
            c = op2[x, y]
            if sum(c) < 700:
                dark_out += 1
    print(f"[保真] 新图非白像素（隔行采样）{dark_out} 个（原图非背景 {(W*H - n_bg)//4} 个量级）")

    print("\n[文件]")
    for p in (SRC, OUT1024, OUT512, OUT256, ICO):
        print(f"    {p}  {p.stat().st_size} bytes  {p.stat().st_size/1024:.1f} KB")

    # ---- 7) 对比图：左原图 右新图 ----
    cell = 460
    pad, top = 24, 52
    canvas = Image.new("RGB", (pad * 3 + cell * 2, top + cell + pad), (245, 246, 248))
    a = im.resize((cell, cell), Image.LANCZOS)
    b = out.resize((cell, cell), Image.LANCZOS)
    canvas.paste(a, (pad, top))
    canvas.paste(b, (pad * 2 + cell, top))
    d = ImageDraw.Draw(canvas)
    font = None
    for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
               r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"):
        if Path(fp).exists():
            try:
                font = ImageFont.truetype(fp, 26)
                break
            except Exception:
                pass
    if font is None:
        font = ImageFont.load_default()
    d.rectangle([pad - 2, top - 2, pad + cell + 1, top + cell + 1], outline=(200, 204, 210))
    d.rectangle([pad * 2 + cell - 2, top - 2, pad * 2 + cell * 2 + 1, top + cell + 1],
                outline=(200, 204, 210))
    d.text((pad + 4, 12), "原图：深蓝底 + 左下角水印", fill=(30, 34, 42), font=font)
    d.text((pad * 2 + cell + 4, 12), "新图：白底 + 无水印", fill=(30, 34, 42), font=font)
    COMPARE.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(COMPARE)
    print(f"    {COMPARE}  {COMPARE.stat().st_size} bytes")


if __name__ == "__main__":
    main()
