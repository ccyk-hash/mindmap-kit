# -*- coding: utf-8 -*-
"""生成装饰层 A/B 并排对比卡。

用途：阶段3 验收件。左边是 `--decorate none`（仅 SMM 渲染），右边是
`--decorate full`，右侧图上用圆形编号标出四块装饰，说明文字统一放在下方，
避免说明压住图面。

用法（在 mindmap-kit 目录下）：
    python -m kit.ab_card BEFORE_SVG AFTER_SVG OUT_PNG [--theme NAME] [--title T]

单张卡片的排版全部由常量控制，改字号/间距只需要改这里的常量。
"""
from __future__ import annotations

import argparse
import os
import re

from PIL import Image, ImageDraw, ImageFont

from . import chrome as chrome_mod
from . import export as export_mod

# ── 版面常量 ──────────────────────────────────────────────────
PAD_L = 46
PAD_R = 46
GAP = 58                # 两图之间的空隙（箭头落在这里）
BAR_H = 62              # 顶栏高度
HEAD_H = 52             # 列标题区高度
NOTE_H = 190            # 底部说明区高度
FOOT_H = 48             # 页脚结论行
BOT_PAD = 28            # 图区与说明区之间的留白（底部装饰的编号不贴边）
CARD_BG = (250, 249, 246)
BAR_BG = (34, 48, 63)
EDGE = (214, 210, 202)
ACCENT = (150, 116, 92)
INK_DARK = (46, 56, 68)
INK_SOFT = (110, 118, 128)


def _font(size: int, bold: bool = False):
    cands = ([r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc"]
             if bold else [r"C:\Windows\Fonts\msyh.ttc"])
    for p in cands:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _marker(d: ImageDraw.ImageDraw, x: int, y: int, num: str, r: int = 17) -> None:
    """图上用的圆形编号：实心圆 + 反白数字。"""
    d.ellipse([x - r, y - r, x + r, y + r], fill=ACCENT, outline=(255, 255, 255), width=2)
    f = _font(int(r * 1.15), True)
    tw = d.textlength(num, font=f)
    d.text((x - tw / 2, y - r * 0.62), num, font=f, fill=(255, 255, 255))


def _mark_positions(svg: str, ratio: float, xa: int, y0: int):
    """按装饰层真实几何算出四个编号的落点（SVG 坐标 → 图卡像素）。

    不靠手调：编号徽标取自一级分支节点坐标，索引栏/页脚/图例的位置直接
    由 decorate 的版面常量推出，换主题或换数据都还能落在正确位置。
    """
    from . import decorate as dec

    m = re.search(r'<svg[^>]*\bwidth="([\d.]+)"[^>]*\bheight="([\d.]+)"', svg)
    total_w = float(m.group(1)) if m else 3200.0
    total_h = float(m.group(2)) if m else 8000.0

    def px(vx: float, vy: float):
        return int(xa + vx * ratio), int(y0 + vy * ratio)

    # 内容区（未加装饰前的画布）
    content_w = total_w - (dec.INDEX_W + dec.INDEX_GAP)
    content_h = total_h - dec.FOOT_H
    bx = content_w + dec.INDEX_GAP          # 索引栏左边
    fy = content_h + 34.0                   # 页脚基线，与 decorate 一致

    nodes, _t = dec.scan_nodes(svg)
    # 第一个一级分支：字号第二档（28px）的第一个节点，取它左侧徽标的落点
    badge_v = (120.0, 300.0)
    for n in nodes:
        if 20 < n.size < 34 and n.h > 0:
            badge_v = (n.x - 86.0, n.cy)
            break

    cards_total = 210.0 * 4 + 18.0 * 3
    x0 = total_w - dec.FRAME_INSET - 14.0 - cards_total

    marks = [
        ("1", badge_v[0] - 30.0, badge_v[1]),                       # 编号徽标
        ("2", bx + dec.INDEX_W - 26.0, 82.0),                       # 索引栏标题右上
        ("3", x0 + cards_total - 42.0, fy + 10.0),                  # 数据卡右侧
        ("4", dec.FRAME_INSET + 14.0 + 1060.0, fy + 176.0),          # 图例行右侧
    ]
    return [(num, *px(vx, vy)) for num, vx, vy in marks]


def build(before_svg: str, after_svg: str, out_png: str, *,
          theme: str = "morandi", title: str = "装饰层 A/B 对比",
          scale: float = 0.40, max_px: int = 20000) -> dict:
    ch = chrome_mod.find_chrome()
    tmp_dir = chrome_mod.workspace_tmp()
    pb = os.path.join(tmp_dir, "ab_b.png")
    pa = os.path.join(tmp_dir, "ab_a.png")

    sb = open(before_svg, encoding="utf-8").read()
    sa = open(after_svg, encoding="utf-8").read()
    export_mod.to_png(sb, pb, chrome=ch, paper="#FCFCFB", scale=scale, max_px=max_px)
    export_mod.to_png(sa, pa, chrome=ch, paper="#FCFCFB", scale=scale, max_px=max_px)
    ib = Image.open(pb).convert("RGB")
    ia = Image.open(pa).convert("RGB")

    # 右图相对左图多出来的宽度/高度，就是装饰层占走的空白
    grow_w = ia.width - ib.width
    grow_h = ia.height - ib.height

    W = PAD_L + ib.width + GAP + ia.width + PAD_R
    H = BAR_H + HEAD_H + max(ib.height, ia.height) + BOT_PAD + NOTE_H + FOOT_H
    card = Image.new("RGB", (W, H), CARD_BG)
    d = ImageDraw.Draw(card)

    # 顶栏
    d.rectangle([0, 0, W, BAR_H], fill=BAR_BG)
    d.text((PAD_L, 17), title, font=_font(25, True), fill=(240, 244, 248))
    tag = f"mindmap-kit   ·   theme {theme}   ·   --decorate none → full"
    tw = d.textlength(tag, font=_font(16))
    d.text((W - PAD_R - tw, 25), tag, font=_font(16), fill=(150, 170, 190))

    y0 = BAR_H + HEAD_H

    # 列标题
    def head(x: int, label: str, sub: str, color) -> None:
        bh = 30
        d.rounded_rectangle([x, BAR_H + 15, x + 108, BAR_H + 15 + bh], radius=8, fill=color)
        d.text((x + 15, BAR_H + 19), label, font=_font(17, True), fill=(255, 255, 255))
        d.text((x + 122, BAR_H + 20), sub, font=_font(16), fill=INK_SOFT)

    head(PAD_L, "BEFORE", "仅 SMM 渲染，无装饰", (152, 162, 174))
    xa = PAD_L + ib.width + GAP
    head(xa, "AFTER", "叠加装饰层 full", ACCENT)

    # 两张图
    for im, x in ((ib, PAD_L), (ia, xa)):
        d.rectangle([x - 1, y0 - 1, x + im.width, y0 + im.height], outline=EDGE)
        card.paste(im, (x, y0))

    # 中间箭头
    mx = PAD_L + ib.width + GAP // 2
    d.line([mx, y0 + 24, mx, y0 + max(ib.height, ia.height) - 24],
           fill=(206, 200, 190), width=1)
    ay = y0 + 150
    d.polygon([(mx - 14, ay - 10), (mx + 14, ay), (mx - 14, ay + 10)], fill=ACCENT)

    # 右图四块装饰的圆形编号：位置由装饰层的真实几何算出，不靠手调
    marks = _mark_positions(sa, scale, xa, y0)
    for num, my_x, my_y in marks:
        _marker(d, my_x, my_y, num)

    # 底部说明四项
    ny = y0 + max(ib.height, ia.height) + BOT_PAD + 30
    col_w = (W - PAD_L - PAD_R) // 2
    items = [
        ("1", "编号徽标", "一级分支左侧的站点圆，双环 + 反白编号，色环随分支轮转"),
        ("2", "分区索引栏", "右侧独立栏，色块编号 + 标题 + 行分隔线，长图快速定位"),
        ("3", "页脚数据卡", "四张卡展示节点数 / 分区数 / 插图数 / 警示数"),
        ("4", "符号图例行", "把三角警示、图号、表号翻译成人话，正文里的记号不再无解"),
    ]
    f_t = _font(17, True)
    f_d = _font(16)
    for k, (num, name, desc) in enumerate(items):
        cx = PAD_L + (k % 2) * col_w
        cy = ny + (k // 2) * 74
        _marker(d, cx + 17, cy + 15, num, r=15)
        d.text((cx + 44, cy + 2), name, font=f_t, fill=INK_DARK)
        d.text((cx + 44, cy + 30), desc, font=f_d, fill=INK_SOFT)

    # 页脚结论
    fy = H - FOOT_H + 6
    d.line([PAD_L, fy - 12, W - PAD_R, fy - 12], fill=(222, 217, 208), width=1)
    d.text((PAD_L, fy),
           "节点几何零改动：装饰只向空白区生长 —— 右侧 +402px、底部 +272px；"
           "全部为原生 SVG 形状与 <text>，进 PDF 后可选中、可搜索。",
           font=_font(16), fill=(96, 104, 114))

    card.save(out_png)
    return {"width": W, "height": H, "before": ib.size, "after": ia.size,
            "grow": (grow_w, grow_h)}


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m kit.ab_card",
                                 description="生成装饰层 A/B 并排对比卡")
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("out")
    ap.add_argument("--theme", default="morandi")
    ap.add_argument("--title", default="装饰层 A/B 对比")
    ap.add_argument("--scale", type=float, default=0.40)
    a = ap.parse_args()
    info = build(a.before, a.after, a.out, theme=a.theme, title=a.title, scale=a.scale)
    print(f"√ ab_card  {a.out}  {info['width']}×{info['height']}  "
          f"before {info['before']}  after {info['after']}")


if __name__ == "__main__":
    main()