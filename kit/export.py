# -*- coding: utf-8 -*-
"""SVG → 印刷级单页 PDF / 预览 PNG。

关键技术选择（实测依据）：
  · PyMuPDF 直接渲染含 <foreignObject> 的 SVG → 文字变纯黑块（1.28.2 实测）
  · Chrome --print-to-pdf 能正确栅格化 foreignObject 里的 HTML 文字，且文字可选中
  所以 PDF 与预览图**都走 Chrome**，PyMuPDF 只用来做事后校验（页数/取文/尺寸）。
"""
from __future__ import annotations

import os
import re
import shutil

from .chrome import ChromeError, ChromeInfo, make_run_dir, print_to_pdf, screenshot

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRINT_TPL = os.path.join(KIT_ROOT, "templates", "print.html")

PX_PER_PT = 96.0 / 72.0          # 1pt = 96/72 px
# PDF 规范（PDF 1.7 §7.7.3.3）限制 MediaBox 单边不超过 14400pt（200 英寸）。
# 超过后 Chrome 仍能输出，但 Acrobat / 预览 / 多数阅读器会裁切或拒绝渲染，
# 所以超限时整幅等比缩小，保持单页 + 矢量清晰度不变。
MAX_PAGE_PT = 14400.0
_SIZE_RE = re.compile(r'<svg[^>]*\bwidth="([\d.]+)"[^>]*\bheight="([\d.]+)"')


def svg_size(svg: str) -> tuple[float, float]:
    m = _SIZE_RE.search(svg)
    if not m:
        raise ValueError("无法从 SVG 里读出 width/height")
    return float(m.group(1)), float(m.group(2))


def _split_svg(svg: str) -> tuple[str, str, str]:
    """把 SVG 拆成 (开标签, 内容, 闭标签)，便于注入 xmlns/样式。"""
    i = svg.find("<svg")
    j = svg.find(">", i)
    k = svg.rfind("</svg>")
    return svg[i:j + 1], svg[j + 1:k], svg[k:]


def _prepare_svg(svg: str, *, width: float, height: float,
                 ensure_xmlns: bool = True) -> str:
    head, body, tail = _split_svg(svg)
    if ensure_xmlns and "xmlns=" not in head:
        head = head.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
    if "xmlns:xlink" not in head:
        head = head.replace("<svg", '<svg xmlns:xlink="http://www.w3.org/1999/xlink"', 1)
    # 强制精确尺寸，避免 @page 与 svg 尺寸不一致导致多页
    head = re.sub(r'\bwidth="[\d.]+"', f'width="{width:g}"', head, count=1)
    head = re.sub(r'\bheight="[\d.]+"', f'height="{height:g}"', head, count=1)
    return head + body + tail


def _write_print_html(svg: str, width: float, height: float, paper: str,
                      title: str, run_dir: str,
                      page_w_pt: float | None = None,
                      page_h_pt: float | None = None) -> str:
    with open(PRINT_TPL, encoding="utf-8") as fh:
        tpl = fh.read()
    # @page 用 pt，内容用 px。scale 把 px 换算成 pt，同时承担「超限缩页」的系数：
    # 页面被缩到 page_w_pt 时，scale = page_w_pt * 96/72 / width。
    if page_w_pt is None:
        page_w_pt = width / PX_PER_PT
    if page_h_pt is None:
        page_h_pt = height / PX_PER_PT
    scale = (page_w_pt * PX_PER_PT / width) if width else (1 / PX_PER_PT)
    html = (tpl
            .replace("__TITLE__", title)
            .replace("__PW__", f"{page_w_pt:.4f}")
            .replace("__PH__", f"{page_h_pt:.4f}")
            .replace("__W__", f"{width:g}")
            .replace("__H__", f"{height:g}")
            .replace("__CW__", f"{width * scale:.4f}")
            .replace("__CH__", f"{height * scale:.4f}")
            .replace("__SCALE__", f"{scale:.6f}")
            .replace("__PAPER__", paper)
            .replace("__SVG__", svg))
    path = os.path.join(run_dir, "print.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path


def _file_url(path: str) -> str:
    return "file:///" + os.path.abspath(path).replace("\\", "/").lstrip("/")


def to_pdf(svg: str, out_pdf: str, *, chrome: ChromeInfo, paper: str = "#FFFFFF",
           title: str = "mindmap", budget_ms: int = 12000,
           timeout: int = 240, fit_page: bool = True) -> str:
    """把 SVG 打印成单页 PDF。返回最终 pdf 路径。

    fit_page=True 时，若页面任一边超过 PDF 规范上限，整幅等比缩小到刚好合规，
    仍是一页、仍是矢量，只是物理尺寸变小（超长章节必需，否则阅读器裁切）。
    """
    w, h = svg_size(svg)
    limit_pt = MAX_PAGE_PT
    w_pt, h_pt = w / PX_PER_PT, h / PX_PER_PT
    if fit_page and max(w_pt, h_pt) > limit_pt:
        # 留 0.2% 余量：内容高度与页高严格相等时，Chrome 会因浮点误差多印一页
        k = (limit_pt / max(w_pt, h_pt)) * 0.998
        w_pt, h_pt = w_pt * k, h_pt * k
    svg2 = _prepare_svg(svg, width=w, height=h)

    run_dir = make_run_dir("pdf")
    html = _write_print_html(svg2, w, h, paper, title, run_dir,
                             page_w_pt=w_pt, page_h_pt=h_pt)

    # 必须打到无空格路径，随后再搬回目标位置
    tmp_pdf = os.path.join(run_dir, "out.pdf")
    print_to_pdf(chrome, _file_url(html), tmp_pdf, budget_ms=budget_ms, timeout=timeout)

    out_pdf = os.path.abspath(out_pdf)
    os.makedirs(os.path.dirname(out_pdf), exist_ok=True)
    shutil.copy2(tmp_pdf, out_pdf)
    return out_pdf


def to_png(svg: str, out_png: str, *, chrome: ChromeInfo, paper: str = "#FFFFFF",
           scale: float = 1.0, max_px: int = 12000, title: str = "mindmap",
           budget_ms: int = 12000, timeout: int = 240) -> str:
    """整页截图预览。超大图自动降 scale（Chrome 单张截图有尺寸上限）。"""
    w, h = svg_size(svg)
    sc = scale
    while max(w, h) * sc > max_px and sc > 0.05:
        sc *= 0.85
    w2, h2 = int(round(w * sc)), int(round(h * sc))

    run_dir = make_run_dir("png")
    svg2 = _prepare_svg(svg, width=w, height=h)
    # 预览页：容器尺寸=目标像素，内部 svg 按 sc 缩放
    with open(PRINT_TPL, encoding="utf-8") as fh:
        tpl = fh.read()
    html = (tpl
            .replace("__TITLE__", title)
            .replace("@page { size: __PW__pt __PH__pt; margin: 0; }", "/* png preview */")
            .replace("__PW__", "0").replace("__PH__", "0")
            .replace("__W__", str(w2))
            .replace("__H__", str(h2))
            .replace("__CW__", str(w2))
            .replace("__CH__", str(h2))
            .replace("__SCALE__", f"{sc:.6f}")
            .replace("__PAPER__", paper)
            .replace("__SVG__", svg2))
    path = os.path.join(run_dir, "png.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)

    tmp_png = os.path.join(run_dir, "out.png")
    screenshot(chrome, _file_url(path), tmp_png, w2, h2,
               budget_ms=budget_ms, timeout=timeout)
    out_png = os.path.abspath(out_png)
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    shutil.copy2(tmp_png, out_png)
    return out_png


def verify_pdf(pdf_path: str, expect_pages: int = 1) -> dict:
    """事后校验：页数 / 页面尺寸 / 可提取文字长度。PyMuPDF 缺失时降级。"""
    info = {"path": pdf_path, "exists": os.path.isfile(pdf_path), "pages": None,
            "width_pt": None, "height_pt": None, "text_chars": None,
            "text_head": "", "ok": False, "engine": None}
    if not info["exists"]:
        info["error"] = "文件不存在"
        return info
    try:
        try:
            import pymupdf as pdf
        except ImportError:
            import fitz as pdf  # type: ignore
        info["engine"] = "pymupdf"
        doc = pdf.open(pdf_path)
        info["pages"] = doc.page_count
        page = doc[0]
        info["width_pt"] = round(page.rect.width, 2)
        info["height_pt"] = round(page.rect.height, 2)
        txt = page.get_text()
        info["text_chars"] = len(txt.strip())
        info["text_head"] = txt.strip()[:120].replace("\n", " ")
        info["ok"] = (doc.page_count == expect_pages) and (info["text_chars"] or 0) > 50
        doc.close()
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info