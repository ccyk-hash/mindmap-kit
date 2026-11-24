# -*- coding: utf-8 -*-
"""mmk —— 思维导图流水线命令行入口。

  mmk doctor
  mmk build <src.md> [--theme morandi] [--layout tree] [--font-scale 1.0]
            [-o out/ch02] [--no-pdf] [--no-png] [--png-scale 0.3]
  mmk themes list
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

from . import __version__
from . import decorate as decorate_mod
from . import doctor as doctor_mod
from . import export as export_mod
from . import parse as parse_mod
from . import render as render_mod
from . import svgtext as svgtext_mod
from .chrome import ChromeError, find_chrome, workspace_tmp, list_run_dirs, purge_run_dirs

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(KIT_ROOT, "out")


# ---------------------------------------------------------------- doctor ----
def cmd_doctor(args) -> int:
    res = doctor_mod.run(verbose=True)
    return 0 if res["verdict"] != "FAIL" else 1


# ---------------------------------------------------------------- themes ----
def cmd_themes(args) -> int:
    if args.action == "list":
        rows = render_mod.list_themes()
        if not rows:
            print("  (no themes yet — 阶段 2 会补齐 6 套)")
            return 0
        print(f"  {'name':<12}{'label':<20}{'branches':>8}  description")
        for r in rows:
            print(f"  {r['name']:<12}{r['label']:<20}{r['branches']:>8}  {r['description']}")
        return 0
    if args.action == "show":
        th = render_mod.load_theme(args.name)
        print(json.dumps(th, ensure_ascii=False, indent=2)[:4000])
        return 0
    print("unsupported action", file=sys.stderr)
    return 2


# ----------------------------------------------------------------- build ----
def _apply_font_scale(theme: dict, scale: float) -> dict:
    """只允许整体缩放，不加字号档位（计划书硬约束）。"""
    th = json.loads(json.dumps(theme))          # deep copy
    for k in th.get("sizes", {}):
        th["sizes"][k] = max(9, int(round(th["sizes"][k] * scale)))
    for sect in ("root", "second", "node", "leaf"):
        node = th.get("smm", {}).get(sect)
        if isinstance(node, dict) and "fontSize" in node:
            node["fontSize"] = max(9, int(round(node["fontSize"] * scale)))
    th.setdefault("smm", {})
    th["smm"]["_fontScale"] = scale
    return th


def _auto_cards(rstats: dict, pstats: dict) -> list[dict]:
    """页脚四张数据卡：节点数 / 一级分区 / 插图 / 警示。颜色取自莫兰迪色环。"""
    branches = []
    try:
        branches = render_mod.load_theme("morandi").get("branches") or []
    except Exception:
        pass
    cols = [b.get("main") for b in branches[:4]] or ["#33507A", "#9E4A45", "#48796A", "#8A621F"]
    nodes = rstats.get("nodes", pstats.get("node_count", 0))
    return [
        {"value": f"{nodes}", "label": "知识节点", "color": cols[0]},
        {"value": f"{pstats.get('headings', 0)}", "label": "标题层级",
         "color": cols[1]},
        {"value": f"{pstats.get('figs', 0)}", "label": "结构插图", "color": cols[2]},
        {"value": f"{pstats.get('warns', 0)}", "label": "易错警示", "color": cols[3]},
    ]


def cmd_build(args) -> int:
    t0 = time.time()
    src = os.path.abspath(args.src)
    if not os.path.isfile(src):
        print(f"× 源文件不存在：{src}", file=sys.stderr)
        return 2

    stem = os.path.splitext(os.path.basename(src))[0]
    # 去掉冗长的书名后缀
    for tail in ("_思维导图_完整版", "_思维导图", "_完整版"):
        if stem.endswith(tail):
            stem = stem[: -len(tail)]
            break
    out_dir = os.path.abspath(args.out or os.path.join(DEFAULT_OUT, stem))
    os.makedirs(out_dir, exist_ok=True)

    chrome = find_chrome()
    theme = render_mod.load_theme(args.theme)
    scale = max(0.85, min(1.15, float(args.font_scale)))
    theme = _apply_font_scale(theme, scale)

    # 1) 解析
    root, stats = parse_mod.load(src)
    tree = parse_mod.to_smm(root)
    parse_mod.save_json(root, stats, os.path.join(out_dir, f"{stem}.data.json"),
                        meta={"source": src, "theme": args.theme,
                              "layout": args.layout, "fontScale": scale})
    print(f"· parse   {stats['node_count']:>4} 节点 / 深度 {stats['max_level']}"
          f" / 警示 {stats['warns']} / 图 {stats['figs']} / 表 {stats['tabs']}")

    # 2) 渲染 SVG
    t1 = time.time()
    res = render_mod.render(tree, theme, chrome=chrome, layout=args.layout,
                            font_scale=scale)
    # 2.5) 把 foreignObject 里的 HTML 文字拍平成原生 <text>
    #      必须做：Chrome 158 的 --print-to-pdf 对 foreignObject 内多行 HTML
    #      只保住第一行，其余被裁成细线。拍平后文字完整、可选中、可搜索。
    flat_svg, flat_stats = svgtext_mod.flatten(res.svg)
    dec_meta = {}
    if args.decorate != "none":
        pick = args.decorate
        info = {
            "title": f"{stem} · 全章思维导图",
            "subtitle": f"{theme.get('label', args.theme)} · "
                        f"{res.stats.get('maxLevel', 0)} 层结构",
            "meta_line": args.meta_line,
            "cards": _auto_cards(res.stats, stats),
            "legend": {"warn": stats.get("warns", 0),
                       "fig": stats.get("figs", 0),
                       "tab": stats.get("tabs", 0)},
        }
        dres = decorate_mod.decorate(
            flat_svg, theme, info=info,
            index_bar=pick in ("index", "full"),
            footer=pick in ("footer", "full"),
            badges=pick in ("badges", "full"),
            frame=True)
        flat_svg = dres.svg
        dec_meta = {"mode": pick, **dres.meta, "sections": dres.sections,
                    "size": {"width": dres.width, "height": dres.height}}
    svg_path = os.path.join(out_dir, f"{stem}.svg")
    _atomic_write_text(svg_path, flat_svg)
    print(f"· render  {res.width:.0f}×{res.height:.0f} px  ({time.time() - t1:.1f}s)"
          f"  svg {len(flat_svg) / 1024:.0f} kB"
          f"  文字拍平 {flat_stats['objects']} 处 / {flat_stats['lines']} 行")
    if dec_meta:
        print(f"· decorate  装饰后 {dec_meta['size']['width']:.0f}×"
              f"{dec_meta['size']['height']:.0f} px"
              f"  分区 {dec_meta['sections'] and len(dec_meta['sections'])} 个"
              f"  [{args.decorate}]")

    report = {
        "source": src, "theme": args.theme, "layout": args.layout,
        "fontScale": scale, "version": __version__,
        "svg": os.path.basename(svg_path),
        "size": {"width": res.width, "height": res.height},
        "stats": res.stats, "parseStats": stats, "textFlatten": flat_stats,
    }
    if dec_meta:
        report["decorate"] = dec_meta

    # 3) PDF
    if not args.no_pdf:
        t2 = time.time()
        pdf_path = os.path.join(out_dir, f"{stem}.pdf")
        export_mod.to_pdf(flat_svg, pdf_path, chrome=chrome,
                          paper=theme["paper"], title=f"{stem} · {theme['label']}")
        v = export_mod.verify_pdf(pdf_path, expect_pages=1)
        report["pdf"] = {**v, "path": os.path.basename(pdf_path),
                         "bytes": os.path.getsize(pdf_path),
                         "seconds": round(time.time() - t2, 1)}
        flag = "√" if v["ok"] else "×"
        print(f"· pdf     {flag} pages={v['pages']} "
              f"{v['width_pt']:.0f}×{v['height_pt']:.0f}pt "
              f"文字 {v['text_chars']} 字符  ({time.time() - t2:.1f}s)")

    # 4) PNG 预览
    if not args.no_png:
        t3 = time.time()
        png_path = os.path.join(out_dir, f"{stem}.preview.png")
        export_mod.to_png(flat_svg, png_path, chrome=chrome, paper=theme["paper"],
                          scale=float(args.png_scale), max_px=int(args.png_max_px))
        from PIL import Image
        with Image.open(png_path) as im:
            pw, ph = im.size
        report["preview"] = {"path": os.path.basename(png_path), "width": pw,
                             "height": ph, "bytes": os.path.getsize(png_path),
                             "seconds": round(time.time() - t3, 1)}
        print(f"· png     {pw}×{ph} px  ({time.time() - t3:.1f}s)")

    report["totalSeconds"] = round(time.time() - t0, 1)
    _atomic_write_text(os.path.join(out_dir, "report.json"),
                       json.dumps(report, ensure_ascii=False, indent=2))
    print(f"√ build   {out_dir}  总耗时 {report['totalSeconds']}s")

    if not args.keep_tmp:
        n = purge_run_dirs()
        if n:
            print(f"· cleanup 清理临时目录 {n} 个（{workspace_tmp()}）")
    return 0


def _atomic_write_text(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


# ----------------------------------------------------------------- audit ----
def cmd_audit(args) -> int:
    from . import audit as audit_mod
    argv = [args.target]
    if args.source:
        argv += ["--source", args.source]
    if args.theme:
        argv += ["--theme", args.theme]
    if args.json:
        argv += ["--json", args.json]
    if args.save_golden is not None:
        argv += ["--save-golden"] + ([args.save_golden] if args.save_golden else [])
    if args.compare_golden is not None:
        argv += ["--compare-golden"] + ([args.compare_golden] if args.compare_golden else [])
    if args.quiet:
        argv += ["--quiet"]
    return audit_mod.main(argv)


# ------------------------------------------------------------------ main ----
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mmk", description="思维导图流水线")
    p.add_argument("--version", action="version", version=f"mmk {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("doctor", help="环境自检")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("themes", help="主题管理")
    sp.add_argument("action", nargs="?", default="list",
                    choices=["list", "show"])
    sp.add_argument("name", nargs="?", default="morandi")
    sp.set_defaults(func=cmd_themes)

    sp = sub.add_parser("audit", help="质量门禁（只读产出，不信 build 自述）")
    sp.add_argument("target", help="产出目录，或直接给 .svg / .pdf")
    sp.add_argument("--source", default=None, help="源 md（目录里没有 report.json 时用）")
    sp.add_argument("--theme", default=None, help="主题名（用于取 paper/字号表）")
    sp.add_argument("--json", default=None, help="把结构化结果另存为 json")
    sp.add_argument("--save-golden", nargs="?", const="", default=None,
                    metavar="NAME", help="存为 golden 基准快照")
    sp.add_argument("--compare-golden", nargs="?", const="", default=None,
                    metavar="NAME", help="与 golden 基准比对，列出变了哪些指标")
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(func=cmd_audit)

    sp = sub.add_parser("build", help="Markdown → SVG/PDF/PNG")
    sp.add_argument("src")
    sp.add_argument("--theme", default="morandi")
    sp.add_argument("--layout", default="tree",
                    choices=list(render_mod.LAYOUTS.keys()))
    sp.add_argument("--font-scale", default=1.0, type=float)
    sp.add_argument("-o", "--out", default=None)
    sp.add_argument("--decorate", default="full",
                    choices=["none", "badges", "index", "footer", "full"],
                    help="装饰层：none 关闭 / badges 仅编号徽标 / "
                         "index 索引栏 / footer 页脚数据卡 / full 全开")
    sp.add_argument("--meta-line", default="MINDMAP-KIT",
                    help="页脚 meta 小字；传空字符串则不显示")
    sp.add_argument("--png-scale", default=0.3, type=float)
    sp.add_argument("--png-max-px", default=14000, type=int)
    sp.add_argument("--no-pdf", action="store_true")
    sp.add_argument("--no-png", action="store_true")
    sp.add_argument("--keep-tmp", action="store_true")
    sp.set_defaults(func=cmd_build)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ChromeError as exc:
        print(f"× 浏览器错误：{exc}", file=sys.stderr)
        return 3
    except FileNotFoundError as exc:
        print(f"× 缺少文件：{exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())