# -*- coding: utf-8 -*-
"""环境自检：浏览器、字体、临时目录、vendor 版本、Python 依赖。"""
from __future__ import annotations

import json
import os
import platform
import sys

from . import __version__
from .chrome import ChromeError, check_fonts, find_chrome, workspace_tmp

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"


def _row(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


def run(verbose: bool = True) -> dict:
    rows: list[dict] = []
    rows.append(_row("python", OK, f"{platform.python_version()} / {platform.system()} {platform.machine()}"))
    rows.append(_row("mindmap-kit", OK, __version__))

    # --- vendor ---
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = os.path.join(root, "vendor", "smm", "simpleMindMap.umd.min.js")
    ver_file = os.path.join(root, "vendor", "smm", "VERSION.txt")
    if os.path.isfile(js):
        size = os.path.getsize(js)
        ver = ""
        if os.path.isfile(ver_file):
            ver = open(ver_file, encoding="utf-8").read().splitlines()[0]
        rows.append(_row("vendor/smm", OK, f"{ver} · {size/1048576:.1f} MB"))
    else:
        rows.append(_row("vendor/smm", FAIL, f"缺少 {js}，请重新拉取 simple-mind-map"))

    # --- chrome ---
    try:
        info = find_chrome()
        major = int(info.version.split(".")[0]) if info.version[:1].isdigit() else 0
        status = OK if major >= 100 else WARN
        rows.append(_row("browser", status,
                         f"{info.kind} {info.version} · {info.path}"))
    except FileNotFoundError as exc:
        rows.append(_row("browser", FAIL, str(exc).splitlines()[0]))

    # --- tmp dir ---
    try:
        tmp = workspace_tmp()
        has_space = " " in tmp
        rows.append(_row("tmp-dir", WARN if has_space else OK, tmp))
    except Exception as exc:  # noqa: BLE001
        rows.append(_row("tmp-dir", FAIL, f"{type(exc).__name__}: {exc}"))

    # --- fonts ---
    fonts = check_fonts()
    if fonts["fallback_ok"]:
        rows.append(_row("cjk-fonts", OK, "、".join(fonts["cjk_fonts"][:6])))
    else:
        rows.append(_row("cjk-fonts", WARN, "未检测到中文字体，中文可能显示为方框"))

    # --- pymupdf（PDF 校验用，可降级） ---
    try:
        import pymupdf  # noqa: F401
        from pymupdf import version as _v  # type: ignore
        rows.append(_row("pymupdf", OK, str(_v[0])))
    except Exception:  # noqa: BLE001
        try:
            import fitz  # noqa: F401
            rows.append(_row("pymupdf", WARN, "仅有旧版 fitz，校验可用"))
        except Exception:  # noqa: BLE001
            rows.append(_row("pymupdf", WARN, "未安装，PDF 页数校验将跳过（SVG 不受影响）"))

    # --- 主题目录 ---
    tdir = os.path.join(root, "kit", "themes")
    n_themes = len([f for f in os.listdir(tdir) if f.endswith(".json")]) if os.path.isdir(tdir) else 0
    rows.append(_row("themes", OK if n_themes else WARN, f"{n_themes} 套主题"))

    verdict = FAIL if any(r["status"] == FAIL for r in rows) else (
        WARN if any(r["status"] == WARN for r in rows) else OK)

    result = {"verdict": verdict, "rows": rows}

    if verbose:
        width = max(len(r["name"]) for r in rows) + 2
        print(f"mindmap-kit doctor  ·  {__version__}")
        print("─" * 72)
        for r in rows:
            mark = {"OK": "[OK]  ", "WARN": "[WARN]", "FAIL": "[FAIL]"}[r["status"]]
            print(f"{mark} {r['name'].ljust(width)} {r['detail']}")
        print("─" * 72)
        print(f"VERDICT: {verdict}")
    return result


if __name__ == "__main__":
    sys.exit(0 if run()["verdict"] != FAIL else 1)