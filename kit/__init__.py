# -*- coding: utf-8 -*-
"""mindmap-kit：Markdown → 主题化思维导图 → SVG / 印刷级 PDF。

设计三条硬约束（见计划书第〇节实测）：
  1. 渲染交给无头 Chrome 里的 simple-mind-map（不做自己算坐标）
  2. PDF 只能由 Chrome --print-to-pdf 生成（PyMuPDF 会丢 foreignObject 文字）
  3. 所有 Chrome 的打印/导出操作走无空格临时目录
"""
__version__ = "0.1.0"

__all__ = ["__version__"]