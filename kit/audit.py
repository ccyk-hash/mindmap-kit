# -*- coding: utf-8 -*-
"""质量门禁：对产出做不依赖肉眼的量化检查。

设计原则
--------
1) **只读产出，不信构建自述**。build 的 report.json 说自己没溢出不算数，
   这里从最终 SVG 里重新量文字宽度，和 `data-width` 比。
2) 阈值写死在 `THRESHOLDS`，不达标给 FAIL，可疑给 WARN，两者都不静默。
3) 门禁分级：FAIL 阻断交付，WARN 记入报告交人判。
4) 快照比对只看**归一化指标**（不比对 SVG 字节），否则改一个主题色就全红。

阈值来源见 `方案B_思维导图流水线_制作计划.md` 第七节。
"""
from __future__ import annotations

import json
import os
import re
import statistics

from . import svgtext as _st

# --------------------------------------------------------------- 阈值 ----
# 依据：计划书 §七「质量门禁（写死在 audit.py 里，不达标直接 FAIL）」
THRESHOLDS = {
    "pdf_pages": 1,              # PDF 页数必须为 1
    "pdf_max_pt": 14400.0,       # PDF 规范 MediaBox 上限 200 英寸 = 14400pt
    "contrast_body": 4.5,        # WCAG 2.1 AA 正文
    "contrast_large": 3.0,       # WCAG AA 大字号（≥18pt≈24px bold）
    "large_px": 24.0,            # 判定「大字号」的像素门槛
    "size_levels_max": 5,        # 字号档位数上限
    "size_gap_min": 2.0,         # 相邻档位最小级差
    "line_levels_min": 3,        # 连线粗细至少 3 级
    "line_len_warn": 20.0,       # 单行字数（em 当量）建议上限
    "line_len_fail": 32.0,       # 单行字数硬上限（再长视觉上就是一条线）
    "coverage_h2": 100.0,        # 二级标题覆盖率
    "coverage_h3": 95.0,         # 三级标题覆盖率
    "overflow": 0,               # 文字溢出硬伤
}

# 注：`line_len` 与计划书原文的「≤20 字」有偏差，见 README 说明——
# 20 字是第9章 v2 手工精简标签后的结果；从教材 md 自动抽取时，叶子节点
# 常是「实测顺序：Asp Thr Ser ...」这类必须整句的行，20 字以内会把知识
# 切碎。故 20em 记 WARN、32em 记 FAIL，并在报告里给出 p50/p95/max 供人判。

OK, WARN, FAIL = "OK", "WARN", "FAIL"

# ------------------------------------------------------------- 颜色工具 ----
_HEX3 = re.compile(r"^#([0-9a-fA-F])([0-9a-fA-F])([0-9a-fA-F])$")
_HEX6 = re.compile(r"^#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$")
_RGBF = re.compile(r"rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)")


def parse_color(val: str) -> tuple[int, int, int] | None:
    """把 #rgb / #rrggbb / rgb() 解析成 (r,g,b)；transparent/none 返回 None。"""
    if not val:
        return None
    v = val.strip()
    if v in ("transparent", "none", "inherit"):
        return None
    m = _HEX6.match(v)
    if m:
        return tuple(int(x, 16) for x in m.groups())  # type: ignore[return-value]
    m = _HEX3.match(v)
    if m:
        return tuple(int(x * 2, 16) for x in m.groups())  # type: ignore[return-value]
    m = _RGBF.match(v)
    if m:
        return tuple(int(round(float(x))) for x in m.groups())  # type: ignore[return-value]
    return None


def _rel_lum(rgb: tuple[int, int, int]) -> float:
    def ch(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast_ratio(fg: str, bg: str) -> float | None:
    a, b = parse_color(fg), parse_color(bg)
    if a is None or b is None:
        return None
    la, lb = _rel_lum(a), _rel_lum(b)
    if la < lb:
        la, lb = lb, la
    return (la + 0.05) / (lb + 0.05)


def _gradients(svg: str) -> dict[str, str]:
    """把 SVG 里所有渐变定义折算成「最不利的那个色标」。

    对比度必须按最坏情况算：文字压在同一渐变的任何位置都要读得清，
    所以取与文字色对比度最低的色标作为该渐变的代表色。
    """
    out: dict[str, str] = {}
    for m in _GRAD_DEF_RE.finditer(svg):
        gid = m.group(2)
        stops = [a or b for a, b in
                 ((s.group(1), s.group(4)) for s in _GRAD_STOP_RE.finditer(m.group(3)))]
        stops = [s for s in stops if s]
        if stops:
            out[gid] = stops[0] if len(stops) == 1 else "|".join(stops)
    return out


def _resolve_bg(fill: str, grads: dict[str, str], paper: str) -> str:
    """把 fill 解析成可算对比度的实色。渐变取色标里最暗/最亮的极端值。"""
    if not fill:
        return paper
    m = _URL_FILL_RE.search(fill)
    if m:
        spec = grads.get(m.group(1))
        if not spec:
            return paper
        cands = [c for c in spec.split("|") if c]
        rgbs = [(c, parse_color(c)) for c in cands]
        rgbs = [(c, r) for c, r in rgbs if r]
        if not rgbs:
            return paper
        # 极端值：取相对亮度最大与最小的两个，交给调用方逐一对文字算对比度
        rgbs.sort(key=lambda x: _rel_lum(x[1]))
        return rgbs[0][0] if len(rgbs) == 1 else "|".join([rgbs[0][0], rgbs[-1][0]])
    if fill in ("transparent", "none", "inherit", ""):
        return paper
    return fill


# --------------------------------------------------------- SVG 结构提取 ----
_SVG_SIZE_RE = re.compile(r'<svg[^>]*\bwidth="([\d.]+)"[^>]*\bheight="([\d.]+)"')
_LINE_CONT_RE = re.compile(r'<g class="smm-line-container">(.*?)</g>', re.S)
_LINE_PATH_RE = re.compile(r'<path[^>]*\bstroke-width="([\d.]+)"[^>]*\bstroke="([^"]+)"')
_NODE_SPLIT_RE = re.compile(
    r'<g class="smm-node".*?(?=<g class="smm-node"|<g class="mmk-decor"|</svg>)', re.S)
_SHAPE_FILL_RE = re.compile(r'class="smm-node-shape"[^>]*?\bfill="([^"]+)"')
_TEXT_OPEN_RE = re.compile(r"<text[^>]*>")
_ATTR_FONT_SIZE = re.compile(r'\bfont-size="([\d.]+)"')
_ATTR_FILL = re.compile(r'\bfill="([^"]+)"')
_ATTR_FAMILY = re.compile(r'\bfont-family="([^"]*)"')
_ATTR_BOLD = re.compile(r'\bfont-weight="(bold|[6-9]00)"')
_DATA_W = re.compile(r'data-width="([\d.]+)"')
_DATA_H = re.compile(r'data-height="([\d.]+)"')
# 节点填充可能是渐变（`fill="url(#SvgjsLinearGradient1001)"`）。SMM 给一级/二级节点
# 用渐变时，直接把 url() 当透明会让对比度检查拿 paper 底色去比，误报成 1.08:1。
_GRAD_DEF_RE = re.compile(
    r'<(linear|radial)Gradient[^>]*\bid="([^"]+)"[^>]*>(.*?)</\1Gradient>', re.S)
_GRAD_STOP_RE = re.compile(r'stop-color="([^"]+)"[^>]*offset="([\d.]+)"'
                           r'|offset="([\d.]+)"[^>]*stop-color="([^"]+)"')
_URL_FILL_RE = re.compile(r'url\(\s*#([^)\s]+)\s*\)')
_TSPAN_RE = re.compile(r"<tspan[^>]*>(.*?)</tspan>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
# 主题字体（等线/雅黑/宋体）缺字形的码位：上下标、数学符号、箭头、连字。
# 浏览器与 PIL 会各自 fallback 到不同字体，PIL 量宽常偏大 2~6%，
# 使「文字刚好占满内宽」的节点被误判成溢出（实测 5px 级）。
_FALLBACK_CHARS = set(
    "₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ"
    "½⅓⅔¼¾⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞"
    "×÷±≈≠≤≥∞∑∏√∫∂∆∇→←↑↓↔⇌⟶⇄·•‰°′″"
    "αβγδεζηθικλμνξοπρστυφχψω"
    "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"
)


def svg_size(svg: str) -> tuple[float, float]:
    m = _SVG_SIZE_RE.search(svg)
    return (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)


def _unescape(s: str) -> str:
    import html as _h
    return _h.unescape(s)


def iter_nodes(svg: str) -> list[dict]:
    """把每个节点块解析成字典：bg / fg / size / family / bold / box / lines。"""
    out = []
    for block in _NODE_SPLIT_RE.finditer(svg):
        b = block.group(0)
        shape = _SHAPE_FILL_RE.search(b)
        bg = shape.group(1) if shape else "transparent"
        t = _TEXT_OPEN_RE.search(b)
        head = t.group(0) if t else ""
        fs = _ATTR_FONT_SIZE.search(head)
        fc = _ATTR_FILL.search(head)
        fm = _ATTR_FAMILY.search(head)
        dw = _DATA_W.search(b)
        dh = _DATA_H.search(b)
        lines = [_unescape(_TAG_RE.sub("", x)).strip() for x in _TSPAN_RE.findall(b)]
        lines = [x for x in lines if x]
        out.append({
            "bg": bg,
            "fg": fc.group(1) if fc else "",
            "size": float(fs.group(1)) if fs else 0.0,
            "family": _unescape(fm.group(1)) if fm else "",
            "bold": bool(_ATTR_BOLD.search(b)),
            "box_w": float(dw.group(1)) if dw else 0.0,
            "box_h": float(dh.group(1)) if dh else 0.0,
            "lines": lines,
            "raw_len": sum(len(x) for x in lines),
        })
    return out


# ------------------------------------------------------------- 各项检查 ----
def check_size_levels(nodes: list[dict]) -> dict:
    sizes = sorted({round(n["size"], 2) for n in nodes if n["size"] > 0})
    gaps = [round(b - a, 2) for a, b in zip(sizes, sizes[1:])]
    bad_gap = [g for g in gaps if g < THRESHOLDS["size_gap_min"]]
    status = OK
    if len(sizes) > THRESHOLDS["size_levels_max"] or bad_gap:
        status = FAIL
    return {
        "name": "字号档位",
        "status": status,
        "detail": f"{len(sizes)} 档 {sizes} · 级差 {gaps}",
        "levels": sizes,
        "gaps": gaps,
        "too_many": len(sizes) > THRESHOLDS["size_levels_max"],
        "tight_gaps": bad_gap,
    }


def check_line_levels(svg: str) -> dict:
    m = _LINE_CONT_RE.search(svg)
    widths = []
    if m:
        widths = [float(w) for w, _c in _LINE_PATH_RE.findall(m.group(1))]
    from collections import Counter
    dist = Counter(widths)
    levels = sorted(dist)
    status = OK if len(levels) >= THRESHOLDS["line_levels_min"] else FAIL
    return {
        "name": "连线阶梯",
        "status": status,
        "detail": f"{len(levels)} 级 {levels} · 分布 "
                  f"{ {k: v for k, v in sorted(dist.items())} }",
        "levels": levels,
        "count": len(widths),
    }


def check_line_length(nodes: list[dict]) -> dict:
    """单行长度用 em 当量（实测字宽 ÷ 字号），比数汉字更贴近真实视觉宽度。

    计划书「节点单行字数 ≤20 字」的物理含义是「一行不超过 20 个汉字宽」= 20em，
    所以 em 阈值本身就是那条规则的等价表达（`→`/空格等窄字符按实际宽度计，
    数「字符个数」会把 `纯化 → 拆链 → 专一` 这类行误判成超长）。

    分两层判，因为两类节点的角色不同：
      · 分支标签（size ≥20，即 depth1/2/3 的标题）—— 扫读靠它，超 20em 直接 FAIL；
      · 叶子描述（size <20，整句知识点）—— 内容本身装不进 20 字，只记 WARN。
    """
    BRANCH_MIN_PX = 20.0
    branch, leaf = [], []
    for n in nodes:
        if not n["size"]:
            continue
        for ln in n["lines"]:
            w = _st._MEASURE.width(ln, n["size"], n["bold"], n["family"])
            em = w / n["size"]
            rec = (round(em, 1), ln[:48])
            (branch if n["size"] >= BRANCH_MIN_PX else leaf).append(rec)
    if not branch and not leaf:
        return {"name": "单行长度", "status": WARN, "detail": "无文字"}

    warn_em = THRESHOLDS["line_len_warn"]
    fail_em = THRESHOLDS["line_len_fail"]
    b_over = sorted([x for x in branch if x[0] > warn_em], reverse=True)
    b_fail = [x for x in b_over if x[0] > fail_em]
    l_over = sorted([x for x in leaf if x[0] > warn_em], reverse=True)

    all_em = sorted([x[0] for x in branch + leaf])
    p50 = statistics.median(all_em)
    p95 = all_em[min(len(all_em) - 1, int(len(all_em) * 0.95))]
    mx = all_em[-1]

    status = FAIL if b_fail else (WARN if b_over else OK)
    return {
        "name": "单行长度",
        "status": status,
        "detail": f"标签 {len(branch)} 行 / 描述 {len(leaf)} 行 · "
                  f"p50 {p50:.1f}em / p95 {p95:.1f}em / max {mx:.1f}em · "
                  f"标签超 {warn_em:.0f}em {len(b_over)} 行"
                  + (f"（硬超 {fail_em:.0f}em {len(b_fail)} 行）" if b_fail else "")
                  + f" · 描述超 {warn_em:.0f}em {len(l_over)} 行（仅提示）",
        "p50": round(p50, 2),
        "p95": round(p95, 2),
        "max": round(mx, 2),
        "branch_over": len(b_over),
        "leaf_over": len(l_over),
        "over_fail": len(b_fail),
        "worstText": (b_over + l_over)[:8],
    }


def check_contrast(nodes: list[dict], paper: str,
                   grads: dict[str, str] | None = None) -> dict:
    """逐节点算 WCAG 对比度。

    背景按最不利原则确定：透明底用画布 paper；渐变填充取色标里对文字最不利的
    那一端（渐变跨越两个极端色时，只有最差的那一端才算「读不清」）。
    """
    grads = grads or {}
    fails, warns, ratios = [], [], []
    buckets: dict[tuple[str, str, float], int] = {}
    for n in nodes:
        bg_spec = _resolve_bg(n["bg"], grads, paper)
        cands = [c for c in bg_spec.split("|") if c] or [paper]
        need = (THRESHOLDS["contrast_large"] if n["size"] >= THRESHOLDS["large_px"]
                else THRESHOLDS["contrast_body"])
        rs = [(c, contrast_ratio(n["fg"], c)) for c in cands]
        rs = [(c, r) for c, r in rs if r is not None]
        if not rs:
            continue
        bg, r = min(rs, key=lambda x: x[1])
        ratios.append(r)
        key = (bg, n["fg"], n["size"])
        buckets[key] = buckets.get(key, 0) + 1
        if r < need - 1e-9:
            rec = (round(r, 2), need, bg, n["fg"], n["size"],
                   (n["lines"][0][:30] if n["lines"] else ""))
            (fails if r < need * 0.95 else warns).append(rec)
    worst = min(ratios) if ratios else 0.0
    status = FAIL if fails else (WARN if warns else OK)
    top = sorted(((round(contrast_ratio(b, g) or 0, 2), b, g, s, c)
                  for (b, g, s), c in buckets.items()))
    return {
        "name": "WCAG 对比度",
        "status": status,
        "detail": f"{len(ratios)} 个节点 · 最低 {worst:.2f}:1 · "
                  f"不足 {len(fails) + len(warns)} 处",
        "worstRatio": round(worst, 2),
        "fails": [list(x) for x in fails[:8]],
        "warns": [list(x) for x in warns[:8]],
        "lowest_pairs": [list(x) for x in top[:6]],
    }


def check_overflow(nodes: list[dict]) -> dict:
    """独立复算：每行实测宽度 vs 节点 data-width。不复用 build 的结论。

    data-width 有两种语义（见 svgtext 的同一处洞察）：
      · 文字没顶满换行宽度时 → 它是**精确内容宽**，超一点就是真溢出；
      · 文字顶满 textAutoWrapWidth 时 → 它是**上限**，浏览器已经保证不溢出，
        PIL 对破折号/省略号的字宽估计偏大 2~4%，此时宽容差，只抓明显越界。
    判别方式：同一字号下若有 ≥5 个节点共用该 box_w 值，即认为该值是上限。
    """
    hist: dict[tuple[float, float], dict[float, int]] = {}
    for n in nodes:
        if not n["box_w"] or not n["size"]:
            continue
        hist.setdefault((n["size"], n["bold"]), {}).setdefault(n["box_w"], 0)
        hist[(n["size"], n["bold"])][n["box_w"]] += 1
    cap_w: set[float] = set()
    for counts in hist.values():
        for w, c in counts.items():
            if c >= 5:
                cap_w.add(w)

    bad = []
    total = 0
    for n in nodes:
        if not n["box_w"] or not n["size"]:
            continue
        is_cap = n["box_w"] in cap_w
        for ln in n["lines"]:
            total += 1
            w = _st._MEASURE.width(ln, n["size"], n["bold"], n["family"])
            # 精确内容宽：2% + 4px（PIL 与浏览器 hinting 的固有差异）
            # 上限宽：5% + 6px，因为该行已被浏览器折行保证不越界
            tol = max(6.0 if is_cap else 4.0,
                      n["box_w"] * (0.05 if is_cap else 0.02))
            # 含上下标/分数/数学符号的行额外放宽：这些码位在主题字体（等线/雅黑/宋体）
            # 里没有字形，浏览器与 PIL 各自 fallback 到不同字体，整行累计偏差可达 5%。
            # 实测 `中性 AA：(pK₁+pK₂)/2，如 Gly ½(2.34+9.60)=5.97` 差 15px，但该节点
            # 形状宽 345 = data-width 321 + 2×12px 内边距 —— 浏览器侧根本没有溢出。
            # cap 分支（顶满换行宽）放宽到 5%+4px 是安全的：真溢出（如 68px 那条）
            # 都发生在 cap 分支，且幅度远超 5%。
            if _FALLBACK_CHARS & set(ln):
                tol = max(tol, n["box_w"] * 0.05 + 4.0)
            if w > n["box_w"] + tol:
                bad.append({
                    "overflow_px": round(w - n["box_w"], 1),
                    "box_w": round(n["box_w"], 1),
                    "size": n["size"],
                    "cap": is_cap,
                    "text": ln[:46],
                })
    bad.sort(key=lambda x: -x["overflow_px"])
    status = OK if len(bad) <= THRESHOLDS["overflow"] else FAIL
    return {
        "name": "文字溢出",
        "status": status,
        "detail": f"{total} 行 · 溢出 {len(bad)} 处",
        "count": len(bad),
        "worst": bad[:8],
    }


def check_pdf(pdf_path: str) -> dict:
    from .export import verify_pdf
    v = verify_pdf(pdf_path, expect_pages=THRESHOLDS["pdf_pages"])
    reasons = []
    if not v["exists"]:
        reasons.append("文件不存在")
    else:
        if v["pages"] != THRESHOLDS["pdf_pages"]:
            reasons.append(f"页数 {v['pages']} ≠ {THRESHOLDS['pdf_pages']}")
        h = v.get("height_pt") or 0
        if h > THRESHOLDS["pdf_max_pt"]:
            reasons.append(f"高 {h:.0f}pt 超 PDF 上限 {THRESHOLDS['pdf_max_pt']:.0f}pt")
        if (v.get("text_chars") or 0) < 50:
            reasons.append(f"可提取文字仅 {v.get('text_chars')} 字符")
    status = OK if not reasons else FAIL
    return {
        "name": "PDF 交付",
        "status": status,
        "detail": (f"pages={v['pages']} {v.get('width_pt')}×{v.get('height_pt')}pt "
                   f"文字 {v.get('text_chars')} 字符"
                   + ("  ⚠ " + "；".join(reasons) if reasons else "")),
        "pages": v["pages"],
        "width_pt": v.get("width_pt"),
        "height_pt": v.get("height_pt"),
        "text_chars": v.get("text_chars"),
        "reasons": reasons,
    }


def _md_headings(md_path: str) -> tuple[list[str], list[str]]:
    h2, h3 = [], []
    with open(md_path, encoding="utf-8") as fh:
        for line in fh:
            s = line.rstrip("\n")
            if s.startswith("### "):
                h3.append(s[4:].strip())
            elif s.startswith("## "):
                h2.append(s[3:].strip())
    return h2, h3


def check_coverage(nodes: list[dict], md_path: str, sizes: dict) -> dict:
    """H2/H3 覆盖率：把 md 标题与对应层级的节点文字做包含匹配。"""
    from .decorate import _depth_of
    h2, h3 = _md_headings(md_path)
    by_depth: dict[int, list[str]] = {}
    for n in nodes:
        d = _depth_of(n["size"], sizes)
        # 折行会把一句话切成多行，join 之后中间多出空格；比对前统一去掉空白，
        # 否则「08 蛋白质的组成、分类、分 子大小和结构层次」这类会被判成缺失。
        by_depth.setdefault(d, []).append(re.sub(r"\s+", "", " ".join(n["lines"])))
    got1 = by_depth.get(1, [])
    got2 = by_depth.get(2, [])

    def cover(want: list[str], got: list[str]) -> tuple[float, list[str]]:
        if not want:
            return 100.0, []
        keys = [re.sub(r"\s+", "", w) for w in want]
        miss = [w for w, k in zip(want, keys)
                if not any(k in g or g in k for g in got)]
        return round(100.0 * (len(want) - len(miss)) / len(want), 1), miss

    c2, m2 = cover(h2, got1)
    c3, m3 = cover(h3, got2)
    status = OK
    if c2 < THRESHOLDS["coverage_h2"]:
        status = FAIL
    elif c3 < THRESHOLDS["coverage_h3"]:
        status = WARN
    return {
        "name": "标题覆盖率",
        "status": status,
        "detail": f"H2 {c2}%（{len(h2) - len(m2)}/{len(h2)}）· "
                  f"H3 {c3}%（{len(h3) - len(m3)}/{len(h3)}）",
        "h2": c2, "h3": c3,
        "h2_missing": m2[:6], "h3_missing": m3[:6],
    }


# --------------------------------------------------------------- 快照 ----
def snapshot(result: dict) -> dict:
    """归一化成可比较的指标集合（不含路径、耗时等易变项）。"""
    rows = {r["name"]: r for r in result["rows"]}
    snap = {"verdict": result["verdict"], "metrics": {}}
    for key, row in rows.items():
        m = {"status": row["status"]}
        for f in ("levels", "gaps", "p50", "p95", "max", "worst", "count",
                  "worstRatio", "pages", "width_pt", "height_pt", "text_chars",
                  "h2", "h3"):
            if f in row:
                m[f] = row[f]
        snap["metrics"][key] = m
    return snap


def diff_snapshots(old: dict, new: dict) -> list[str]:
    """返回人类可读的差异行；空列表表示完全一致。"""
    out = []
    if old.get("verdict") != new.get("verdict"):
        out.append(f"verdict: {old.get('verdict')} → {new.get('verdict')}")
    om, nm = old.get("metrics", {}), new.get("metrics", {})
    for key in sorted(set(om) | set(nm)):
        if key not in om:
            out.append(f"+ 新增指标 {key}")
            continue
        if key not in nm:
            out.append(f"- 消失指标 {key}")
            continue
        for f in sorted(set(om[key]) | set(nm[key])):
            a, b = om[key].get(f), nm[key].get(f)
            if a == b:
                continue
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                delta = b - a
                out.append(f"{key}.{f}: {a} → {b}（{delta:+.2f}）")
            else:
                out.append(f"{key}.{f}: {a} → {b}")
    return out


# ---------------------------------------------------------------- 主流程 ----
def _notes(row: dict) -> list[str]:
    """把各检查的结构化明细渲染成人类可读的若干行。"""
    out: list[str] = []
    for w in row.get("worst", [])[:3]:
        if isinstance(w, dict):
            out.append(f"+{w['overflow_px']}px（框宽 {w['box_w']}px @ {w['size']:g}px）"
                       f" {w['text']}")
        elif isinstance(w, (list, tuple)):
            if len(w) >= 6:
                out.append(f"{w[0]}:1（需 {w[1]}:1）bg {w[2]} fg {w[3]} "
                           f"@ {w[4]:g}px  「{w[5]}」")
            elif len(w) >= 2:
                out.append(f"{w[0]}:1  {w[1]}")
    for t in row.get("worstText", [])[:3]:
        out.append(f"{t[0]:.1f}em  {t[1]}")
    for p in row.get("lowest_pairs", [])[:3]:
        out.append(f"最低比对 {p[0]}:1  bg {p[1]} fg {p[2]} @ {p[3]:g}px ×{p[4]}")
    for m in row.get("h2_missing", [])[:3]:
        out.append(f"H2 缺：{m}")
    for m in row.get("h3_missing", [])[:3]:
        out.append(f"H3 缺：{m}")
    for r in row.get("reasons", [])[:3]:
        out.append(f"× {r}")
    return out


def run(target: str, *, source: str | None = None, theme: str | None = None,
        paper: str | None = None, sizes: dict | None = None) -> dict:
    """审计一个产出目录（或直接给 svg/pdf 路径）。"""
    target = os.path.abspath(target)
    svg_path = pdf_path = md_path = None
    if os.path.isdir(target):
        for fn in sorted(os.listdir(target)):
            p = os.path.join(target, fn)
            if fn.endswith(".svg") and svg_path is None:
                svg_path = p
            elif fn.endswith(".pdf") and ".preview" not in fn and pdf_path is None:
                pdf_path = p
        rp = os.path.join(target, "report.json")
        if os.path.isfile(rp):
            try:
                with open(rp, encoding="utf-8") as fh:
                    rep = json.load(fh)
                md_path = rep.get("source")
                theme = theme or rep.get("theme")
            except (OSError, ValueError):
                pass
    elif target.endswith(".svg"):
        svg_path = target
    elif target.endswith(".pdf"):
        pdf_path = target
        # 只给 pdf 时，顺手找同目录同名的 svg —— 大部分门禁项要读 svg 才有数据
        sib = os.path.splitext(target)[0] + ".svg"
        if os.path.isfile(sib):
            svg_path = sib
            rp = os.path.join(os.path.dirname(target), "report.json")
            if os.path.isfile(rp):
                try:
                    with open(rp, encoding="utf-8") as fh:
                        rep = json.load(fh)
                    md_path = rep.get("source")
                    theme = theme or rep.get("theme")
                except (OSError, ValueError):
                    pass
    if source:
        md_path = source

    info: dict = {"target": target, "svg": svg_path, "pdf": pdf_path,
                  "source": md_path, "theme": theme, "rows": []}
    if not svg_path or not os.path.isfile(svg_path):
        if pdf_path and os.path.isfile(pdf_path) and target.endswith(".pdf"):
            hint = "只给 PDF 无法审计（八项里七项要读 SVG）；改传产出目录或同名 .svg"
        else:
            hint = f"找不到 SVG：{target}"
        info["rows"].append({"name": "SVG", "status": FAIL, "detail": hint})
        info["verdict"] = FAIL
        info["counts"] = {FAIL: 1, WARN: 0, OK: 0}
        return info

    with open(svg_path, encoding="utf-8") as fh:
        svg = fh.read()
    w, h = svg_size(svg)
    nodes = iter_nodes(svg)

    if paper is None or sizes is None:
        try:
            from .render import load_theme
            th = load_theme(theme or "morandi")
            paper = paper or th.get("paper", "#FFFFFF")
            sizes = sizes or th.get("sizes", {})
        except Exception:  # noqa: BLE001
            paper = paper or "#FFFFFF"
            sizes = sizes or {"root": 44, "second": 28, "node": 20,
                              "leaf": 16, "tag": 14}

    info["size"] = {"width": w, "height": h}
    info["nodeCount"] = len(nodes)
    info["rows"].append({
        "name": "画布规模", "status": OK,
        "detail": f"{w:.0f}×{h:.0f}px · {len(nodes)} 个节点",
        "width": w, "height": h, "nodes": len(nodes),
    })
    info["rows"].append(check_size_levels(nodes))
    info["rows"].append(check_line_levels(svg))
    info["rows"].append(check_line_length(nodes))
    info["rows"].append(check_contrast(nodes, paper, _gradients(svg)))
    info["rows"].append(check_overflow(nodes))
    if pdf_path and os.path.isfile(pdf_path):
        info["rows"].append(check_pdf(pdf_path))
    if md_path and os.path.isfile(md_path):
        info["rows"].append(check_coverage(nodes, md_path, sizes))

    order = {FAIL: 0, WARN: 1, OK: 2}
    info["verdict"] = min((r["status"] for r in info["rows"]),
                          key=lambda s: order[s])
    info["counts"] = {s: sum(1 for r in info["rows"] if r["status"] == s)
                      for s in (OK, WARN, FAIL)}
    return info


def print_report(info: dict) -> None:
    name_w = max(len(r["name"]) for r in info["rows"]) + 2
    print("mindmap-kit audit")
    print("─" * 78)
    print(f"目标  {info['target']}")
    if info.get("source"):
        print(f"源文  {os.path.basename(info['source'])}")
    print("─" * 78)
    for r in info["rows"]:
        mark = {OK: "[OK]  ", WARN: "[WARN]", FAIL: "[FAIL]"}[r["status"]]
        print(f"{mark} {r['name'].ljust(name_w)} {r['detail']}")
        for note in r.get("notes", [])[:4]:
            print(f"        · {note}")
    print("─" * 78)
    c = info["counts"]
    print(f"VERDICT: {info['verdict']}   OK {c[OK]} / WARN {c[WARN]} / FAIL {c[FAIL]}")


def _merge_report_json(target: str, info: dict) -> None:
    """把审计结论并入产出目录的 report.json（保留 build 写下的其它字段）。

    只在 target 是目录且已有 report.json 时动手；单文件 target 或目录不存在
    报告文件时静默跳过，避免在别人的目录里凭空造文件。
    """
    if not os.path.isdir(target):
        return
    rp = os.path.join(target, "report.json")
    if not os.path.isfile(rp):
        return
    try:
        with open(rp, encoding="utf-8") as fh:
            rep = json.load(fh)
    except (OSError, ValueError):
        return
    rep["audit"] = {
        "verdict": info.get("verdict"),
        "counts": info.get("counts"),
        "rows": [{k: r[k] for k in ("name", "status", "detail") if k in r}
                 for r in info.get("rows", [])],
        "thresholds": dict(THRESHOLDS),
    }
    try:
        with open(rp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="mmk audit", description="质量门禁")
    ap.add_argument("target", help="产出目录，或 svg/pdf 路径")
    ap.add_argument("--source", default=None, help="源 markdown（算覆盖率）")
    ap.add_argument("--theme", default=None)
    ap.add_argument("--json", default=None, help="把结构化结果写到这个路径")
    ap.add_argument("--save-golden", nargs="?", const="", default=None,
                    help="存为 golden/<name>.json（省略值则用目标目录名）")
    ap.add_argument("--compare-golden", nargs="?", const="", default=None,
                    help="与 golden/<name>.json 比对（省略值则用目标目录名）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    info = run(args.target, source=args.source, theme=args.theme)
    if not args.quiet:
        print_report(info)
    # 计划书要求「出 report.json」：审计结论必须落在产出目录里，否则报告只活在
    # 终端里，阶段5 的 skill 与下游 CI 都读不到。只写 audit 键，不动 build 写的字段。
    _merge_report_json(args.target, info)
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(info, fh, ensure_ascii=False, indent=2)
        print(f"· json     {args.json}")

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    gdir = os.path.join(root, "golden")
    # 省略值时用目标目录名（`--save-golden` 裸写即可，避免每章手敲一遍章名）
    _default_name = os.path.basename(os.path.normpath(args.target))
    if args.save_golden is not None:
        name = args.save_golden or _default_name
        os.makedirs(gdir, exist_ok=True)
        p = os.path.join(gdir, f"{name}.json")
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(snapshot(info), fh, ensure_ascii=False, indent=2)
        print(f"· golden  已存基准 {p}")
    if args.compare_golden is not None:
        name = args.compare_golden or _default_name
        p = os.path.join(gdir, f"{name}.json")
        if not os.path.isfile(p):
            print(f"· golden  × 找不到基准 {p}")
            return 2
        with open(p, encoding="utf-8") as fh:
            old = json.load(fh)
        diffs = diff_snapshots(old, snapshot(info))
        if diffs:
            print(f"· golden  与基准有 {len(diffs)} 处差异：")
            for d in diffs:
                print(f"          {d}")
        else:
            print("· golden  与基准完全一致")
    return 0 if info["verdict"] != FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())