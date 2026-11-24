# -*- coding: utf-8 -*-
"""把 SMM 导出的 <foreignObject>（HTML 文字）拍平成原生 SVG <text>。

为什么必须这么做
----------------
simple-mind-map 的节点文字全在 <foreignObject> 里。Chrome 的
`--print-to-pdf` 打印这种结构时，多行节点只有第一行可见，其余行被
裁成一条细线（Chrome 158 + poppler 双向验证，非 PyMuPDF 误报）。
拍平成 <text>/<tspan> 后：
  · PDF 里文字必定完整、可选中、可搜索
  · 字号/字重/颜色由 SVG 属性直接控制，不受浏览器 printing 细节影响
  · 顺手把残留的 **粗体** 语法转成真粗体，`code` 转等宽
"""
from __future__ import annotations

import html as _html
import os
import re
from functools import lru_cache

_FO_RE = re.compile(r"<foreignObject\b([^>]*)>(.*?)</foreignObject>", re.S)
_ATTR_RE = re.compile(r'(\w[\w:-]*)="([^"]*)"')
_STYLE_RE = re.compile(r"([\w-]+)\s*:\s*([^;]+)")

# 微软雅黑实测度量（ascender 1.058 / descender -0.262，见 PDF span 数据）
_ASCENT = 1.058
_DESCENT = 0.262
_LINE_HEIGHT = 1.2
# PIL 量字宽与浏览器实测有 1~2% 偏差；顶满 max-width 的节点留这点余量
# 才不会在 PDF 里越过边框。
_WIDTH_SAFETY = 0.98
# 行数校准的放宽上限：PIL 对 ⚠/⇌/下标等符号的度量通常比浏览器宽 2~6%，
# 宋体、等线等字体的差异更大，故允许把断行上限放宽到盒宽的 1.6 倍。
_CALIB_MAX = 1.6

# 字体族 → 候选字体文件。键为「族名归一后的特征词」，主题里写什么字体都能量准。
_FAMILY_MAP: list[tuple[str, str, str]] = [
    # (族名关键词, 常规体候选, 粗体候选)；候选用 ; 分隔。
    # 注意必须按「由具体到泛化」的顺序匹配：'sans-serif' 里含 'serif'，
    # 若把 serif 放前面，微软雅黑会被误判成宋体（实测踩过）。
    ("kai",     "simkai.ttf;STKAITI.TTF",                      "simkai.ttf;STKAITI.TTF"),
    ("楷",       "simkai.ttf;STKAITI.TTF",                      "simkai.ttf;STKAITI.TTF"),
    ("deng",    "Deng.ttf;Dengl.ttf",                           "Dengb.ttf;Deng.ttf"),
    ("等线",     "Deng.ttf;Dengl.ttf",                           "Dengb.ttf;Deng.ttf"),
    ("hei",     "msyh.ttc;msyh.ttf;simhei.ttf",                 "msyhbd.ttc;msyhbd.ttf;simhei.ttf"),
    ("黑",       "msyh.ttc;msyh.ttf;simhei.ttf",                 "msyhbd.ttc;msyhbd.ttf;simhei.ttf"),
    ("yahei",   "msyh.ttc;msyh.ttf;simhei.ttf",                 "msyhbd.ttc;msyhbd.ttf;simhei.ttf"),
    ("雅黑",     "msyh.ttc;msyh.ttf;simhei.ttf",                 "msyhbd.ttc;msyhbd.ttf;simhei.ttf"),
    ("sans",    "msyh.ttc;msyh.ttf;simhei.ttf",                 "msyhbd.ttc;msyhbd.ttf;simhei.ttf"),
    ("song",    "simsun.ttc;STSONG.TTF;NotoSerifSC-VF.ttf",     "simsunb.ttf;simsun.ttc;NotoSerifSC-VF.ttf"),
    ("serif",   "simsun.ttc;STSONG.TTF;NotoSerifSC-VF.ttf",     "simsunb.ttf;simsun.ttc;NotoSerifSC-VF.ttf"),
    ("宋",       "simsun.ttc;STSONG.TTF;NotoSerifSC-VF.ttf",     "simsunb.ttf;simsun.ttc;NotoSerifSC-VF.ttf"),
]


_FONT_DIR = "C:/Windows/Fonts/"


def _family_match(family: str) -> tuple[str, str, str] | None:
    """按 _FAMILY_MAP 顺序找第一个命中的字体族。"""
    f = (family or "").lower()
    for key, regular, heavy in _FAMILY_MAP:
        if key in f:
            return key, regular, heavy
    return None


def _family_files(family: str, bold: bool) -> list[str]:
    """把 CSS font-family 串解析成候选字体文件绝对路径列表。"""
    hit = _family_match(family)
    if hit:
        names = hit[2] if bold else hit[1]
    else:                                  # 兜底：微软雅黑
        names = "msyhbd.ttc;msyhbd.ttf;msyh.ttc" if bold else "msyh.ttc;msyh.ttf;simhei.ttf"
    return [os.path.join(_FONT_DIR, n.strip()) for n in names.split(";") if n.strip()]


def _family_key(family: str) -> str:
    """归一字体族，作为缓存键。"""
    hit = _family_match(family)
    if not hit:
        return "default"
    # 同义关键词归一：sans/yahei/hei/黑/雅黑 → hei；song/serif/宋 → song；kai/楷 → kai
    k = hit[0]
    if k in ("hei", "yahei", "雅黑", "黑", "sans"):
        return "hei"
    if k in ("song", "serif", "宋"):
        return "song"
    return k


class _Measurer:
    """用 PIL 读同一套系统字体量字宽，与 Chrome 排版结果一致。"""

    def __init__(self) -> None:
        self._cache: dict[tuple[int, bool, str], object] = {}

    def _font(self, size: int, bold: bool, family: str = ""):
        key = (size, bold, _family_key(family))
        if key in self._cache:
            return self._cache[key]
        font = None
        try:
            from PIL import ImageFont
            for path in _family_files(family, bold):
                if os.path.isfile(path):
                    try:
                        font = ImageFont.truetype(path, size)
                        break
                    except OSError:
                        continue
        except ImportError:
            font = None
        self._cache[key] = font
        return font

    def width(self, text: str, size: int, bold: bool, family: str = "") -> float:
        if not text:
            return 0.0
        font = self._font(size, bold, family)
        if font is None:                       # 粗估：CJK 1em，ASCII 0.55em
            w = 0.0
            for ch in text:
                w += size * (1.0 if ord(ch) > 0x2E7F else 0.55)
            return w
        try:
            w = float(font.getlength(text))
        except (AttributeError, TypeError):
            w = float(font.getsize(text)[0])
        # 宋/楷 无独立粗体文件时，Chrome 会做合成加粗，字宽略增 → 补 2%
        if bold and _family_key(family) in ("kai", "song"):
            w *= 1.02
        return w


_MEASURE = _Measurer()


# ---------------------------------------------------------------- 文本解析 ----
def _parse_color(value: str, default: str = "#1f2733") -> str:
    v = (value or "").strip()
    if not v:
        return default
    m = re.match(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)", v)
    if m:
        return "#%02X%02X%02X" % tuple(int(round(float(x))) for x in m.groups())
    if v.startswith("#"):
        return v
    named = {"white": "#FFFFFF", "black": "#000000", "transparent": "none"}
    return named.get(v.lower(), default)


def _inner_text(inner: str) -> list[tuple[str, bool, bool]]:
    """把 foreignObject 内部 HTML 拆成 [(文本, 粗体, 等宽)] 段。"""
    s = inner
    # 段落/换行 → 显式换行标记
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</(p|div|li|h[1-6])>", "\n", s, flags=re.I)
    # 语义强调 → 标记
    s = re.sub(r"<(strong|b)\b[^>]*>", "\x01", s, flags=re.I)
    s = re.sub(r"</(strong|b)>", "\x02", s, flags=re.I)
    s = re.sub(r"<(code|kbd)\b[^>]*>", "\x03", s, flags=re.I)
    s = re.sub(r"</(code|kbd)>", "\x04", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s).replace("\u00a0", " ")
    # markdown 残留语法（源 md 里的 **粗体** / `code`）
    s = re.sub(r"\*\*(.+?)\*\*", "\x01\\1\x02", s, flags=re.S)
    s = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", "\x01\\1\x02", s)
    s = re.sub(r"`([^`\n]+?)`", "\x03\\1\x04", s)

    out: list[tuple[str, bool, bool]] = []
    bold = mono = False
    buf: list[str] = []

    def flush() -> None:
        if buf:
            out.append(("".join(buf), bold, mono))
            buf.clear()

    for ch in s:
        if ch == "\x01":
            flush(); bold = True
        elif ch == "\x02":
            flush(); bold = False
        elif ch == "\x03":
            flush(); mono = True
        elif ch == "\x04":
            flush(); mono = False
        else:
            buf.append(ch)
    flush()
    # 清掉纯空白段与多余空行
    cleaned = []
    for text, b, m in out:
        text = re.sub(r"[ \t]{2,}", " ", text)
        if text.strip():
            cleaned.append((text, b, m))
        elif text == "\n":
            cleaned.append(("\n", b, m))
    return _mark_anchors(cleaned)


# 图注/表注锚点。源教材正文里写「（图2-5）」「（表9-3）」，渲染层只在正文
# 没提过编号时才补「〔图2-5〕」。两种形态都要能认出来：14px 密集正文里
# 它们与普通文字同灰，读者扫不到，这里统一切成粗体 run。
_ANCHOR_RE = re.compile(
    r"([〔（(][图表]\s?[\d一二三四五六七八九十]+[-–—.][\d]+[〕）)]"
    r"|[图表]\s?[\d一二三四五六七八九十]+[-–—.][\d]+)")


def _mark_anchors(runs: list[tuple[str, bool, bool]]
                  ) -> list[tuple[str, bool, bool]]:
    res: list[tuple[str, bool, bool]] = []
    for text, bold, mono in runs:
        if "图" not in text and "表" not in text:
            res.append((text, bold, mono))
            continue
        for part in _ANCHOR_RE.split(text):
            if not part:
                continue
            res.append((part, bold or bool(_ANCHOR_RE.fullmatch(part)), mono))
    return res


# 行首禁则：这些字符不能出现在行首，必须粘在前一个字后面
_NO_LINE_START = set("，。、；：！？）】」》〉”’·…％‰℃,.;:!?)]}>%")
# 行尾禁则：这些字符不能留在行尾，必须把后一个字一起带走
_NO_LINE_END = set("（【「《〈“‘([{<")


def _is_wide(ch: str) -> bool:
    return ord(ch) > 0x2E7F


def _wrap(runs: list[tuple[str, bool, bool]], limit: float, size: int,
         family: str = ""
          ) -> list[list[tuple[str, bool, bool]]]:
    """按可用宽度断行，返回「行 -> 段」结构。

    做法：先把所有 run 摊平成单一字符流（保留每字的 bold/mono 标记），
    再按 \n 分段，每段标注「字符之间能否断行」，最后贪心填充并在超宽时
    回退到最近的合法断点。CJK 逐字可断但遵守行首/行尾禁则；ASCII 只在
    空格处断，超长不可断片段才硬断。
    """
    stream: list[tuple[str, bool, bool]] = []
    for text, bold, mono in runs:
        for ch in text:
            stream.append((ch, bold, mono))
    if not stream:
        return [[("", False, False)]]

    paras: list[list[tuple[str, bool, bool]]] = []
    para: list[tuple[str, bool, bool]] = []
    for item in stream:
        if item[0] == "\n":
            paras.append(para)
            para = []
        else:
            para.append(item)
    paras.append(para)

    out: list[list[tuple[str, bool, bool]]] = []
    for chars in paras:
        n = len(chars)
        if n == 0:
            out.append([])
            continue
        widths = [_MEASURE.width(c, size, b, family) for c, b, _m in chars]
        ws = [False] * (n + 1)
        for i in range(1, n):
            a, b = chars[i - 1][0], chars[i][0]
            if a == " ":
                ws[i] = True
            elif b in _NO_LINE_START or a in _NO_LINE_END:
                ws[i] = False
            elif _is_wide(a) or _is_wide(b):
                ws[i] = True
        # 锚点 `〔图2-5〕`/`图2-5` 内部禁断：整个记号必须完整落在一行里，否则
        # 会出现「行尾一个〔、行首图2-5〕」这种读者认不出的碎片。
        for am in _ANCHOR_RE.finditer("".join(c[0] for c in chars)):
            for j in range(am.start() + 1, am.end()):
                if j < n:
                    ws[j] = False
        start = 0
        while start < n:
            acc = 0.0
            last_legal = -1
            i = start
            while i < n:
                if ws[i] and i > start:
                    last_legal = i
                if acc + widths[i] > limit and i > start:
                    break
                acc += widths[i]
                i += 1
            end = i
            if end < n and last_legal > start:
                end = last_legal
            seg = chars[start:end]
            while seg and seg[-1][0] == " ":
                seg.pop()
            line: list[tuple[str, bool, bool]] = []
            for c, b, m in seg:
                if line and line[-1][1] == b and line[-1][2] == m:
                    line[-1] = (line[-1][0] + c, b, m)
                else:
                    line.append((c, b, m))
            out.append(line)
            start = end
            while start < n and chars[start][0] == " ":
                start += 1

    while out and not out[-1]:
        out.pop()
    return out or [[("", False, False)]]



def _style_dict(raw: str) -> dict[str, str]:
    """解析 style 属性。

    注意：浏览器导出的 style 里字体名带 &quot;，其分号会干扰朴素的正则切分
    （实测 font-family 被截成 `&quot`），所以先反转义再按顶层分号拆。
    """
    s = _html.unescape(raw or "")
    out: dict[str, str] = {}
    depth = 0
    buf: list[str] = []
    chunks: list[str] = []
    quote = ""
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == ";" and depth == 0:
            chunks.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        chunks.append("".join(buf))
    for chunk in chunks:
        if ":" not in chunk:
            continue
        k, v = chunk.split(":", 1)
        out[k.strip().lower()] = v.strip()
    return out


def _num(attrs: dict[str, str], key: str, default: float = 0.0) -> float:
    try:
        return float(str(attrs.get(key, default)).replace("px", "").strip())
    except (TypeError, ValueError):
        return default


def _max_width(inner: str) -> float:
    """取内层 div 的 max-width，用来判断 foreignObject 的 width 是「上限」还是「实测值」。"""
    m = re.search(r"max-width\s*:\s*([\d.]+)px", inner)
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except ValueError:
        return 0.0


def _foreign_object_to_text(match: re.Match, stats: dict) -> str:
    attr_s = match.group(1)
    inner = match.group(2)
    attrs = {k: v for k, v in _ATTR_RE.findall(attr_s)}
    style = _style_dict(attrs.get("style", ""))

    x = _num(attrs, "x")
    y = _num(attrs, "y")
    box_w = _num(attrs, "width", 0)
    box_h = _num(attrs, "height", 0)
    try:
        size = int(round(float(str(style.get("font-size", "16")).replace("px", ""))))
    except ValueError:
        size = 16
    bold_default = str(style.get("font-weight", "")).lower() in ("bold", "600", "700", "800", "900")
    color = _parse_color(style.get("color", ""))
    align = (style.get("text-align") or "left").strip().lower()
    family = style.get("font-family") or '"Microsoft YaHei", 微软雅黑, sans-serif'
    family = _html.unescape(family)

    runs = _inner_text(inner)
    if not runs:
        return ""
    # 宽度语义有两种，必须区分：
    #   · 节点文字长到顶住 max-width 时，foreignObject 的 width 是「上限」，
    #     真实内容比它略宽 —— 要用略窄的 limit 才会断出浏览器那几行；
    #   · 文字较短时，width 就是浏览器实测的精确内容宽 —— 用窄 limit 会
    #     把本来一行的话硬拆成两行。
    cap = _max_width(inner)
    if box_w > 2:
        if cap and box_w >= cap * 0.999:
            limit = box_w * _WIDTH_SAFETY
        else:
            limit = box_w * 1.02
    else:
        limit = 4000
    lh = size * _LINE_HEIGHT
    lines = _wrap(runs, limit, size, family)

    # 行数校准：foreignObject 的 height 是浏览器排版后的实测高，反推浏览器
    # 当时排了几行（比 width 的语义可靠得多）。若我排得更多，说明把字量宽了
    # —— 在 [box_w, box_w * _CALIB_MAX] 上二分出最小的 limit 使行数降到
    # expect。上限给到 1.6 倍是因为 PIL 对 ⚠ / ⇌ / 下标等符号的度量通常比
    # 浏览器宽 2~6%，个别字体（宋体/等线）差异更大；超过 1.6 倍仍排不下来，
    # 说明是真溢出，保留原结果并计入 overflowH 由门禁兜住。
    # width_scale 记录「校准后的断行宽度 / 盒宽」。PIL 的度量若比浏览器宽，
    # 这个值会 >1；溢出判定必须用同一个比例，否则会把度量差异误报成溢出。
    width_scale = 1.0
    if box_h > 2 and box_w > 2:
        expect = max(1, int(round(box_h / lh)))
        if len(lines) > expect:
            lo, hi = box_w, box_w * _CALIB_MAX
            best = None
            best_mid = box_w
            for _ in range(7):
                mid = (lo + hi) / 2.0
                cand = _wrap(runs, mid, size, family)
                if len(cand) <= expect:
                    best = cand
                    best_mid = mid
                    hi = mid
                else:
                    lo = mid
            if best is not None:
                lines = best
                width_scale = best_mid / box_w

    total = lh * len(lines)
    # 溢出检测（供阶段 4 质量门禁用）：容差按比例给，避免把 1~3px 的
    # PIL/浏览器度量差异误报成溢出。
    if box_w > 2:
        box_w_eff = box_w * width_scale
        tol_w = max(4.0, box_w_eff * 0.02)
        for line in lines:
            lw = sum(_MEASURE.width(t, size, b or bold_default, family) for t, b, _m in line)
            if lw > box_w_eff + tol_w:
                stats["overflow"] = stats.get("overflow", 0) + 1
                break
    if box_h > 2 and total > box_h * 1.25 + 2.0:
        stats["overflowH"] = stats.get("overflowH", 0) + 1
    top = y + max(0.0, (box_h - total) / 2.0) if box_h > 2 else y
    baseline0 = top + lh / 2.0 + size * (_ASCENT - _DESCENT) / 2.0 - size * _DESCENT

    out = [f'<text x="{x:g}" y="0" font-family="{_html.escape(family)}" '
           f'font-size="{size}" fill="{color}" xml:space="preserve">']
    for i, line in enumerate(lines):
        by = baseline0 + i * lh
        width = sum(_MEASURE.width(t, size, b or bold_default, family) for t, b, _m in line)
        if align == "center" and box_w > 2:
            lx = x + max(0.0, (box_w - width) / 2.0)
        elif align == "right" and box_w > 2:
            lx = x + max(0.0, box_w - width)
        else:
            lx = x
        spans = []
        for text, bold, mono in line:
            if not text:
                continue
            esc = _html.escape(text)
            fw = "bold" if (bold or bold_default) else "normal"
            extra = ''
            if mono:
                extra = ' font-family="Consolas, monospace"'
            spans.append(f'<tspan x="{lx:.2f}" y="{by:.2f}" font-weight="{fw}"'
                         f'{extra}>{esc}</tspan>')
            lx += _MEASURE.width(text, size, bold or bold_default, family)
        out.extend(spans)
    out.append("</text>")
    stats["objects"] += 1
    stats["lines"] += len(lines)
    stats["boldRuns"] += sum(1 for line in lines for t, b, _m in line if b)
    return "".join(out)


def flatten(svg: str) -> tuple[str, dict]:
    """把 SVG 里全部 <foreignObject> 换成原生 <text>。返回 (新 svg, 统计)。"""
    stats = {"objects": 0, "lines": 0, "boldRuns": 0, "overflow": 0, "overflowH": 0}
    new_svg, n = _FO_RE.subn(lambda m: _foreign_object_to_text(m, stats), svg)
    stats["replaced"] = n
    return new_svg, stats


def count_foreign_objects(svg: str) -> int:
    return len(_FO_RE.findall(svg))