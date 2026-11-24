# -*- coding: utf-8 -*-
"""装饰层：在 SMM 导出的 SVG 上追加印刷级装饰元素。

设计原则
--------
1. **不碰节点几何**。SMM 的自动布局已经算好每个节点的位置和尺寸，
   任何试图"挪一挪"的操作都会让连线和节点错位。装饰只往**空白区**长：
   右侧索引栏（画布向右扩展）与底部数据卡（画布向下扩展）。
2. **只产出原生 <text>**。装饰文字不经过 foreignObject，因此不受
   Chrome 打印裁剪问题影响，进 PDF 后必定完整可选中。
3. **主题无关**。所有颜色从主题令牌的 paper / ink / branches 派生，
   浅色与深色主题用同一套算法自动翻转明暗方向。
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field

from . import svgtext as _st

# ── 画布外扩尺寸 ───────────────────────────────────────────────
INDEX_W = 356          # 右侧分区索引栏宽度
INDEX_GAP = 46         # 索引栏与主图之间的留白
FOOT_H = 272           # 底部数据卡区域高度（含一行图例）
FRAME_INSET = 22       # 外框内缩距离

# ── 正则 ──────────────────────────────────────────────────────
_CONTAINER_RE = re.compile(
    r'<g class="smm-container" transform="matrix\(1,0,0,1,(-?[\d.]+),(-?[\d.]+)\)"')
_NODE_RE = re.compile(
    r'<g class="smm-node" transform="matrix\(1,0,0,1,(-?[\d.]+),(-?[\d.]+)\)"')
_HOVER_RE = re.compile(
    r'<rect width="([\d.]+)" height="([\d.]+)" x="(-?[\d.]+)" y="(-?[\d.]+)" class="smm-hover-node"')
_FS_RE = re.compile(r'font-size="([\d.]+)"')
_FO_RE = re.compile(r'<foreignObject\b[^>]*>(.*?)</foreignObject>', re.S)
_FULLRECT_RE = re.compile(
    r'<rect width="([\d.]+)" height="([\d.]+)"([^>]*)></rect>')


# ── 颜色工具 ──────────────────────────────────────────────────
def _rgb(c: str) -> tuple[int, int, int]:
    c = (c or "").strip()
    if c.startswith("#"):
        c = c[1:]
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        if len(c) >= 6:
            try:
                return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
            except ValueError:
                pass
    return (128, 128, 128)


def _hex(t: tuple[float, float, float]) -> str:
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(v)))) for v in t)


def _mix(a: str, b: str, t: float) -> str:
    ra, rb = _rgb(a), _rgb(b)
    return _hex(tuple(ra[i] + (rb[i] - ra[i]) * t for i in range(3)))


def _luminance(c: str) -> float:
    def lin(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = _rgb(c)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _is_dark(c: str) -> bool:
    return _luminance(c) < 0.42


def _readable_on(bg: str, light: str = "#FFFFFF", dark: str = "#111111") -> str:
    l1, l2 = _luminance(light), _luminance(dark)
    lb = _luminance(bg)
    c1 = (max(l1, lb) + 0.05) / (min(l1, lb) + 0.05)
    c2 = (max(l2, lb) + 0.05) / (min(l2, lb) + 0.05)
    return light if c1 >= c2 else dark


def _esc(t: str) -> str:
    return _html.escape(t or "", quote=True)


# ── 节点扫描 ──────────────────────────────────────────────────
@dataclass
class SNode:
    x: float
    y: float
    w: float
    h: float
    size: float
    text: str = ""
    seg_end: int = 0

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0


def _first_line(markup: str) -> str:
    """取 foreignObject 内文本的第一行（已去掉 markdown 粗体标记）。"""
    m = _FO_RE.search(markup)
    if not m:
        m2 = re.search(r'<text\b[^>]*>(.*?)</text>', markup, re.S)
        if not m2:
            return ""
        raw = re.sub(r'<[^>]+>', '', m2.group(1))
        first = _html.unescape(raw).strip().split("\n")[0]
        return first
    runs = _st._inner_text(m.group(1))
    buf = []
    for text, _b, _m in runs:
        for ch in text:
            if ch == "\n":
                return "".join(buf).strip()
            buf.append(ch)
    return "".join(buf).strip()


def scan_nodes(svg: str) -> tuple[list[SNode], tuple[float, float]]:
    """返回 (按文档顺序排列的节点列表, 容器平移量)。

    坐标已换算成画布绝对坐标（容器 transform 已叠加）。
    """
    m = _CONTAINER_RE.search(svg)
    tx, ty = (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)

    hits = list(_NODE_RE.finditer(svg))
    out: list[SNode] = []
    for i, h in enumerate(hits):
        seg_end = hits[i + 1].start() if i + 1 < len(hits) else len(svg)
        seg = svg[h.end():seg_end]
        hr = _HOVER_RE.search(seg)
        if not hr:
            continue
        fs = _FS_RE.search(seg)
        out.append(SNode(
            x=tx + float(h.group(1)),
            y=ty + float(h.group(2)),
            w=float(hr.group(1)) - 4.0,
            h=float(hr.group(2)) - 4.0,
            size=float(fs.group(1)) if fs else 16.0,
            text=_first_line(seg),
            seg_end=seg_end,
        ))
    return out, (tx, ty)


def _depth_of(size: float, sizes: dict) -> int:
    """用字号反推层级。paint() 给每层固定字号，字号即层级的镜像。

    根节点单独判为 0：它的字号（44）与 second（28）差距远大于层内抖动，
    若混进一级分支列表，索引栏会多出一行"第2章 …"。
    """
    root_sz = int(sizes.get("root", 44))
    if abs(size - root_sz) <= 1.5:
        return 0
    table = [
        (int(sizes.get("second", 28)), 1),
        (int(sizes.get("node", 20)), 2),
        (int(sizes.get("leaf", 16)), 3),
        (int(sizes.get("tag", 14)), 4),
    ]
    best, dist = 4, 1e9
    for sz, d in table:
        dd = abs(size - sz)
        if dd < dist:
            best, dist = d, dd
    return best


def _fit(text: str, size: float, limit: float, family: str = "") -> str:
    """按像素宽度截断并补省略号，装饰文本不允许溢出。"""
    if not text:
        return ""
    if _st._MEASURE.width(text, int(round(size)), False, family) <= limit:
        return text
    ell = "…"
    ew = _st._MEASURE.width(ell, int(round(size)), False, family)
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _st._MEASURE.width(text[:mid], int(round(size)), False, family) + ew <= limit:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + ell


# ── 装饰主函数 ────────────────────────────────────────────────
@dataclass
class DecorateResult:
    svg: str
    width: float
    height: float
    sections: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def decorate(svg: str, theme: dict, *, info: dict | None = None,
             index_bar: bool = True, footer: bool = True,
             badges: bool = True, frame: bool = True) -> DecorateResult:
    """在 SVG 上叠加装饰层，返回扩展后的画布尺寸。

    info 可用键：title / subtitle / meta_line / stats
    """
    info = info or {}
    sizes = theme.get("sizes") or {}
    paper = theme.get("paper") or "#FFFFFF"
    ink = theme.get("ink") or "#111111"
    ink_soft = theme.get("inkSoft") or _mix(paper, ink, 0.62)
    fam = (theme.get("fonts") or {}).get("node") or theme.get("fontFamily") or ""
    dark = _is_dark(paper)
    branches = theme.get("branches") or [{"main": ink, "deep": ink, "pale": paper, "mist": paper, "edge": ink}]

    nodes, (_tx, _ty) = scan_nodes(svg)
    m_size = re.search(r'<svg[^>]*\bwidth="([\d.]+)"[^>]*\bheight="([\d.]+)"', svg)
    W = float(m_size.group(1)) if m_size else 0.0
    H = float(m_size.group(2)) if m_size else 0.0

    # 一级分支：按文档顺序取，色环按同序轮转（与 paint() 的分配顺序一致）
    firsts = [n for n in nodes if _depth_of(n.size, sizes) == 1]
    sections: list[dict] = []
    for i, n in enumerate(firsts):
        pal = branches[i % len(branches)]
        num = info.get("numbers", {}).get(n.text) or "%02d" % i
        sections.append({"index": i, "num": num, "title": n.text, "pal": pal, "node": n})

    newW = W + (INDEX_W + INDEX_GAP if index_bar else 0)
    newH = H + (FOOT_H if footer else 0)

    parts: list[str] = []

    # ── 1. 编号徽标：一级分支节点左侧的站点头 ──────────────────
    if badges:
        r = 21.0
        for s in sections:
            n, pal = s["node"], s["pal"]
            cx, cy = n.x - r - 13.0, n.cy
            parts.append(
                '<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" stroke="%s" stroke-width="1.2"></circle>'
                % (cx, cy, r, paper, pal.get("main", ink)))
            parts.append(
                '<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s"></circle>'
                % (cx, cy, r - 3.4, pal.get("main", ink)))
            fs = 17
            parts.append(
                '<text x="%.1f" y="%.1f" font-family="%s" font-size="%d" font-weight="bold" '
                'fill="%s" text-anchor="middle">%s</text>'
                % (cx, cy + fs * 0.36, _esc(fam), fs,
                   _readable_on(pal.get("main", ink)), _esc(s["num"])))

    # ── 2. 右侧分区索引栏 ──────────────────────────────────────
    if index_bar and sections:
        bx = W + INDEX_GAP
        col_pale = _mix(paper, "#FFFFFF" if dark else ink, 0.06 if dark else 0.035)
        col_line = _mix(paper, ink, 0.22)
        parts.append(
            '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="14" fill="%s" '
            'stroke="%s" stroke-width="1"></rect>'
            % (bx, 60.0, float(INDEX_W), H - 120.0, col_pale, col_line))

        head = "分区索引"
        parts.append(
            '<text x="%.1f" y="%.1f" font-family="%s" font-size="26" font-weight="bold" '
            'fill="%s">%s</text>'
            % (bx + 30, 120.0, _esc(fam), ink, _esc(head)))
        parts.append(
            '<text x="%.1f" y="%.1f" font-family="%s" font-size="15" letter-spacing="2.4" '
            'fill="%s">%s</text>'
            % (bx + 30, 148.0, _esc(fam), ink_soft, "SECTIONS"))
        parts.append(
            '<path d="M %.1f,172 L %.1f,172" stroke="%s" stroke-width="1.4"></path>'
            % (bx + 30, bx + INDEX_W - 30, col_line))

        row_top, row_bottom = 196.0, H - 96.0
        n_rows = len(sections)
        rh = min(58.0, (row_bottom - row_top) / max(1, n_rows))
        for j, s in enumerate(sections):
            ry = row_top + rh * j + rh / 2.0
            chip = 30.0
            parts.append(
                '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="7" fill="%s"></rect>'
                % (bx + 26, ry - chip / 2.0, chip, chip, s["pal"].get("main", ink)))
            parts.append(
                '<text x="%.1f" y="%.1f" font-family="%s" font-size="15" font-weight="bold" '
                'fill="%s" text-anchor="middle">%s</text>'
                % (bx + 26 + chip / 2.0, ry + 5.4, _esc(fam),
                   _readable_on(s["pal"].get("main", ink)), _esc(s["num"])))
            label = _fit(s["title"], 17, INDEX_W - 96.0, fam)
            parts.append(
                '<text x="%.1f" y="%.1f" font-family="%s" font-size="17" fill="%s">%s</text>'
                % (bx + 68, ry + 6.0, _esc(fam), ink, _esc(label)))
            if j < n_rows - 1:
                parts.append(
                    '<path d="M %.1f,%.1f L %.1f,%.1f" stroke="%s" stroke-width="1" '
                    'stroke-opacity="0.55"></path>'
                    % (bx + 26, ry + rh / 2.0, bx + INDEX_W - 26, ry + rh / 2.0, col_line))

    # ── 3. 底部数据卡 ──────────────────────────────────────────
    if footer:
        fy = H + 34.0
        col_card = _mix(paper, "#FFFFFF" if dark else ink, 0.07 if dark else 0.03)
        col_line = _mix(paper, ink, 0.20)
        stats = info.get("stats") or {}
        title = info.get("title") or ""
        subtitle = info.get("subtitle") or ""
        meta_line = info.get("meta_line") or ""

        parts.append(
            '<text x="%.1f" y="%.1f" font-family="%s" font-size="34" font-weight="bold" '
            'fill="%s">%s</text>'
            % (FRAME_INSET + 14, fy + 52, _esc(fam), ink, _esc(_fit(title, 34, 900, fam))))
        if subtitle:
            parts.append(
                '<text x="%.1f" y="%.1f" font-family="%s" font-size="19" fill="%s">%s</text>'
                % (FRAME_INSET + 14, fy + 86, _esc(fam), ink_soft,
                   _esc(_fit(subtitle, 19, 900, fam))))
        if meta_line:
            parts.append(
                '<text x="%.1f" y="%.1f" font-family="%s" font-size="15" letter-spacing="1.2" '
                'fill="%s">%s</text>'
                % (FRAME_INSET + 14, fy + 116, _esc(fam), ink_soft,
                   _esc(_fit(meta_line, 15, 900, fam))))

        cards = info.get("cards") or []
        if cards:
            cw = 210.0
            ch = 108.0
            gap = 18.0
            total = cw * len(cards) + gap * (len(cards) - 1)
            x0 = newW - FRAME_INSET - 14 - total
            cy0 = fy + 26.0
            for k, c in enumerate(cards):
                cx0 = x0 + (cw + gap) * k
                accent = c.get("color") or _mix(paper, ink, 0.5)
                parts.append(
                    '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="10" fill="%s" '
                    'stroke="%s" stroke-width="1"></rect>'
                    % (cx0, cy0, cw, ch, col_card, col_line))
                parts.append(
                    '<rect x="%.1f" y="%.1f" width="5" height="%.1f" rx="2.5" fill="%s"></rect>'
                    % (cx0, cy0 + 12, ch - 24, accent))
                parts.append(
                    '<text x="%.1f" y="%.1f" font-family="%s" font-size="34" font-weight="bold" '
                    'fill="%s">%s</text>'
                    % (cx0 + 20, cy0 + 56, _esc(fam), ink, _esc(str(c.get("value", "")))))
                parts.append(
                    '<text x="%.1f" y="%.1f" font-family="%s" font-size="15" fill="%s">%s</text>'
                    % (cx0 + 20, cy0 + 84, _esc(fam), ink_soft,
                       _esc(_fit(str(c.get("label", "")), 15, cw - 34, fam))))

    # 3b. 图例：把「⚠ / 〔图x-x〕 / 〔表x-x〕」三种行内符号翻译成人话
        legend = info.get("legend") or {}
        if legend:
            ly = fy + 176.0
            lx = FRAME_INSET + 14.0
            # 图例上方一条发丝分隔线，与数据卡区分开
            parts.append(
                '<path d="M %.1f,%.1f L %.1f,%.1f" stroke="%s" stroke-width="1" '
                'stroke-opacity="0.45"></path>'
                % (lx, ly - 30.0, newW - FRAME_INSET - 14.0, ly - 30.0, col_line))
            items = [
                ("warn", "易错警示", legend.get("warn", 0)),
                ("fig", "结构插图锚点", legend.get("fig", 0)),
                ("tab", "数据表格锚点", legend.get("tab", 0)),
            ]
            for kind, label, count in items:
                if kind == "warn":
                    # 三角警示图标（矢量，不依赖 emoji 彩色字形）
                    parts.append(
                        '<path d="M %.1f,%.1f L %.1f,%.1f L %.1f,%.1f Z" fill="none" '
                        'stroke="%s" stroke-width="1.6" stroke-linejoin="round"></path>'
                        % (lx + 11, ly - 11, lx + 22, ly + 8, lx, ly + 8,
                           _mix(paper, "#C8892B", 0.85)))
                    parts.append(
                        '<text x="%.1f" y="%.1f" font-family="%s" font-size="11" '
                        'font-weight="bold" fill="%s" text-anchor="middle">!</text>'
                        % (lx + 11, ly + 5.2, _esc(fam),
                           _mix(paper, "#C8892B", 0.85)))
                elif kind == "fig":
                    parts.append(
                        '<rect x="%.1f" y="%.1f" width="20" height="15" rx="3" fill="none" '
                        'stroke="%s" stroke-width="1.4"></rect>'
                        % (lx + 1, ly - 10, _mix(paper, ink, 0.55)))
                    parts.append(
                        '<path d="M %.1f,%.1f L %.1f,%.1f L %.1f,%.1f" fill="%s" '
                        'stroke="none"></path>'
                        % (lx + 4, ly + 2, lx + 9, ly - 5, lx + 14, ly + 2,
                           _mix(paper, ink, 0.55)))
                else:
                    for k in range(3):
                        parts.append(
                            '<path d="M %.1f,%.1f L %.1f,%.1f" stroke="%s" '
                            'stroke-width="1.4" stroke-opacity="%s"></path>'
                            % (lx + 2, ly - 6 + k * 6, lx + 21, ly - 6 + k * 6,
                               _mix(paper, ink, 0.55), 1.0 if k == 0 else 0.5))
                parts.append(
                    '<text x="%.1f" y="%.1f" font-family="%s" font-size="15" fill="%s">%s</text>'
                    % (lx + 32, ly + 2, _esc(fam), ink_soft,
                       _esc(_fit("%s · %s 处" % (label, count), 15, 260, fam))))
                lx += _st._MEASURE.width("%s · %s 处" % (label, count),
                                        15, False, fam) + 74

    # ── 4. 细外框 ─────────────────────────────────────────────
    if frame:
        parts.append(
            '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="10" fill="none" '
            'stroke="%s" stroke-width="1.2" stroke-opacity="0.75"></rect>'
            % (FRAME_INSET, FRAME_INSET, newW - FRAME_INSET * 2,
               newH - FRAME_INSET * 2, _mix(paper, ink, 0.28)))

    out = svg

    # 背景铺满新画布：把两张与旧尺寸完全相同的 rect 放大
    def _grow(m: re.Match) -> str:
        w, h, rest = m.group(1), m.group(2), m.group(3)
        if abs(float(w) - W) < 0.5 and abs(float(h) - H) < 0.5:
            return '<rect width="%.0f" height="%.0f"%s></rect>' % (newW, newH, rest)
        return m.group(0)

    out = _FULLRECT_RE.sub(_grow, out, count=6)

    # 根元素尺寸
    out = re.sub(r'(<svg\b[^>]*?)\bwidth="[\d.]+"',
                 r'\1width="%.0f"' % newW, out, count=1)
    out = re.sub(r'(<svg\b[^>]*?)\bheight="[\d.]+"',
                 r'\1height="%.0f"' % newH, out, count=1)

    # 装饰元素插在 </svg> 之前（位于内容之上）
    if parts:
        idx = out.rfind("</svg>")
        if idx < 0:
            idx = len(out)
        out = out[:idx] + '<g class="mmk-decor">' + "".join(parts) + "</g>" + out[idx:]

    return DecorateResult(svg=out, width=newW, height=newH,
                          sections=[{"num": s["num"], "title": s["title"]} for s in sections],
                          meta={"indexBar": index_bar, "footer": footer,
                                "badges": badges, "frame": frame,
                                "sections": len(sections)})