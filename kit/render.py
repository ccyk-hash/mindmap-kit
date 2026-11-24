# -*- coding: utf-8 -*-
"""驱动无头 Chrome 跑 simple-mind-map，回收 SVG 字符串。

流程（三条硬约束的具体落地）：
  1) 把 templates/render.html 复制到**无空格临时目录**，注入 vendor 的 js/css 相对路径
  2) 把主题 JSON 与节点树以 JSON 字面量注入页面
  3) Chrome --dump-dom --virtual-time-budget 跑完，从 DOM 里抠出 base64 包裹的 SVG
  4) 解码写盘，返回尺寸与统计
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import urllib.parse
from dataclasses import dataclass, field

from .chrome import ChromeInfo, make_run_dir, dump_dom

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(KIT_ROOT, "templates", "render.html")
VENDOR_JS = os.path.join(KIT_ROOT, "vendor", "smm", "simpleMindMap.umd.min.js")
VENDOR_CSS = os.path.join(KIT_ROOT, "vendor", "smm", "simpleMindMap.css")

LAYOUTS = {
    "tree": "logicalStructure",
    "balanced": "mindMap",
    "org": "organizationStructure",
    "fishbone": "fishbone",
    "timeline": "verticalTimeline",
    "catalog": "catalogOrganization",
}

_ERR_RE = re.compile(r'<div id="MMK_ERROR">(.*?)</div>', re.S)
_OUT_RE = re.compile(r'<div id="MMK_OUT">(.*?)</div>', re.S)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
_DIM_RE = re.compile(r'<div id="MMK_DIM">(.*?)</div>', re.S)
_STAT_RE = re.compile(r'<div id="MMK_STAT">(.*?)</div>', re.S)


class RenderError(RuntimeError):
    pass


@dataclass
class RenderResult:
    svg: str
    width: float
    height: float
    stats: dict = field(default_factory=dict)
    run_dir: str = ""
    html_path: str = ""


def load_theme(name: str) -> dict:
    path = os.path.join(KIT_ROOT, "kit", "themes", f"{name}.json")
    if not os.path.isfile(path):
        avail = sorted(f[:-5] for f in os.listdir(os.path.join(KIT_ROOT, "kit", "themes"))
                       if f.endswith(".json"))
        raise FileNotFoundError(f"主题 {name!r} 不存在。可用：{', '.join(avail)}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def theme_dir() -> str:
    return os.path.join(KIT_ROOT, "kit", "themes")


def list_themes() -> list[dict]:
    """列出全部可用主题的摘要。"""
    d = theme_dir()
    if not os.path.isdir(d):
        return []
    rows = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fn), encoding="utf-8") as fh:
                th = json.load(fh)
        except (OSError, ValueError):
            continue
        rows.append({
            "name": th.get("name", fn[:-5]),
            "label": th.get("label", ""),
            "description": th.get("description", ""),
            "branches": len(th.get("branches") or []),
            "paper": th.get("paper", ""),
        })
    return rows


def _inject(template: str, cfg: dict, tree: dict, js_href: str, css_href: str) -> str:
    html = template
    html = html.replace("__CSS__", css_href).replace("__JS__", js_href)
    cfg_js = json.dumps(cfg, ensure_ascii=False)
    tree_js = json.dumps(tree, ensure_ascii=False)
    html = html.replace("__CONFIG__", cfg_js).replace("__TREE__", tree_js)
    # 追加输出通道（render.html 里只写 __MMK__ 状态，这里补 DOM 出口）
    patch = """
<script>
(function () {
  var n = 0;
  (function wait() {
    n++;
    var s = window.__MMK__ || {};
    if (s.stage === 'done' || s.stage === 'error' || n > 600) {
      if (s.stage === 'done') {
        var o = document.createElement('div');
        o.id = 'MMK_OUT';
        o.textContent = btoa(unescape(encodeURIComponent(s.svg)));
        document.body.appendChild(o);
        var d = document.createElement('div');
        d.id = 'MMK_DIM';
        d.textContent = btoa(unescape(encodeURIComponent(JSON.stringify(s.dims))));
        document.body.appendChild(d);
        var st = document.createElement('div');
        st.id = 'MMK_STAT';
        st.textContent = btoa(unescape(encodeURIComponent(JSON.stringify(s.stats || {}))));
        document.body.appendChild(st);
      }
      return;
    }
    setTimeout(wait, 40);
  })();
})();
</script>
"""
    return html.replace("</body>", patch + "</body>")


def render(tree: dict, theme: dict, *, chrome: ChromeInfo, layout: str = "tree",
           padding: tuple[int, int] = (56, 56), budget_ms: int = 20000,
           keep_run_dir: bool = False, timeout: int = 180,
           font_scale: float = 1.0) -> RenderResult:
    if not os.path.isfile(VENDOR_JS):
        raise RenderError(f"缺少 vendor JS：{VENDOR_JS}")

    run_dir = make_run_dir("render")
    # 复制 vendor（Chrome 在 file:// 下不允许跨目录读父级资源时更稳）
    vdir = os.path.join(run_dir, "v")
    os.makedirs(vdir, exist_ok=True)
    shutil.copy2(VENDOR_JS, os.path.join(vdir, "smm.js"))
    shutil.copy2(VENDOR_CSS, os.path.join(vdir, "smm.css"))

    cfg = {
        "themeName": theme.get("name", "custom"),
        "theme": theme,
        "layout": LAYOUTS.get(layout, layout),
        "paddingX": padding[0],
        "paddingY": padding[1],
        "fontScale": float(font_scale),
    }
    with open(TEMPLATE, encoding="utf-8") as fh:
        tpl = fh.read()
    html = _inject(tpl, cfg, tree, "v/smm.js", "v/smm.css")
    html_path = os.path.join(run_dir, "render.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)

    url = "file:///" + html_path.replace("\\", "/").lstrip("/")
    dom = dump_dom(chrome, url, budget_ms=budget_ms, timeout=timeout)

    m = _ERR_RE.search(dom)
    if m:
        raise RenderError(_html_unescape(m.group(1))[:2000])

    out = _OUT_RE.search(dom)
    if not out:
        title = (_TITLE_RE.search(dom) or [None, "?"])[1]
        raise RenderError(f"页面未产出 SVG（title={title!r}）。"
                          f"DOM 长度 {len(dom)}，可检查渲染页：{html_path}")

    svg = urllib.parse.unquote(base64.b64decode(out.group(1)).decode("utf-8"))
    dims = {}
    dm = _DIM_RE.search(dom)
    if dm:
        dims = json.loads(urllib.parse.unquote(base64.b64decode(dm.group(1)).decode("utf-8")))
    stats = {}
    sm = _STAT_RE.search(dom)
    if sm:
        stats = json.loads(urllib.parse.unquote(base64.b64decode(sm.group(1)).decode("utf-8")))

    return RenderResult(svg=svg,
                        width=float(dims.get("width") or 0),
                        height=float(dims.get("height") or 0),
                        stats=stats, run_dir=run_dir, html_path=html_path)


def _html_unescape(s: str) -> str:
    import html as _h
    return _h.unescape(s).replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")