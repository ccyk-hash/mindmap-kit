# -*- coding: utf-8 -*-
"""Chrome / Chromium / Edge 定位与无头调用封装。

三条硬约束（源自 spike 实测）：
  1) Chrome 在含空格路径下 --print-to-pdf 会失败（系统找不到指定的路径），
     所有 --print-to-pdf / --dump-dom 的操作必须在无空格目录里进行。
  2) 无头模式必须带 --virtual-time-budget，否则导出 Promise 还没 resolve 就退出。
  3) --no-pdf-header-footer 才能拿到精确 @page 尺寸（否则被页眉页脚撑大）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

_NO_SPACE_ROOT_ENV = "MMK_TMP"


@dataclass
class ChromeInfo:
    path: str
    version: str
    kind: str  # chrome | edge | chromium


def find_chrome(explicit: Optional[str] = None) -> ChromeInfo:
    """按显式路径 → 环境变量 → 候选列表 → PATH 的顺序定位浏览器。"""
    tried = []
    if explicit:
        if os.path.isfile(explicit):
            return ChromeInfo(explicit, _version(explicit), _kind(explicit))
        tried.append(f"{explicit} (不存在)")
    env = os.environ.get("MMK_CHROME")
    if env:
        if os.path.isfile(env):
            return ChromeInfo(env, _version(env), _kind(env))
        tried.append(f"{env} (MMK_CHROME 不存在)")
    for c in CANDIDATES:
        if os.path.isfile(c):
            return ChromeInfo(c, _version(c), _kind(c))
        tried.append(c)
    for name in ("chrome", "chromium", "google-chrome", "msedge"):
        p = shutil.which(name)
        if p:
            return ChromeInfo(p, _version(p), _kind(p))
    raise FileNotFoundError(
        "未找到 Chrome/Chromium/Edge。已尝试：\n  " + "\n  ".join(tried)
        + "\n可用环境变量 MMK_CHROME 指定浏览器可执行文件路径。"
    )


def _kind(path: str) -> str:
    low = os.path.basename(path).lower()
    if "edge" in low or "msedge" in low:
        return "edge"
    if "chromium" in low:
        return "chromium"
    return "chrome"


def _version(path: str) -> str:
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True,
                             timeout=25, encoding="utf-8", errors="replace")
        txt = (out.stdout or out.stderr or "").strip()
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)", txt)
        return m.group(1) if m else (txt or "unknown")
    except Exception as exc:  # noqa: BLE001
        return f"unknown ({type(exc).__name__})"


def workspace_tmp() -> str:
    """返回一个**路径不含空格**的临时目录，必要时创建。

    Chrome 的 -print-to-pdf / --dump-dom 在含空格路径下会静默失败，
    所以这里优先选系统盘根目录下的短名子目录。
    """
    override = os.environ.get(_NO_SPACE_ROOT_ENV)
    bases = []
    if override:
        bases.append(override)
    drive = os.path.splitdrive(os.getcwd())[0] or "C:"
    bases += [os.path.join(drive + os.sep, "mmk_tmp"), "/tmp/mmk_tmp"]
    for b in bases:
        if " " in os.path.abspath(b):
            continue
        try:
            os.makedirs(b, exist_ok=True)
            probe = os.path.join(b, ".probe")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
            return b
        except Exception:  # noqa: BLE001
            continue
    fallback = tempfile.mkdtemp(prefix="mmk_")
    return fallback


def make_run_dir(tag: str = "run") -> str:
    """在无空格根目录下建一个本次运行专属的工作目录。"""
    root = workspace_tmp()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^0-9A-Za-z_.-]", "_", tag)[:40] or "run"
    d = os.path.join(root, f"{safe}_{stamp}_{os.getpid()}")
    os.makedirs(d, exist_ok=True)
    return d


def list_run_dirs(root: Optional[str] = None) -> list[str]:
    """列出临时根目录下的历史 run 目录。"""
    root = root or workspace_tmp()
    if not os.path.isdir(root):
        return []
    out = []
    for name in os.listdir(root):
        p = os.path.join(root, name)
        if os.path.isdir(p):
            out.append(p)
    return out


def purge_run_dirs(root: Optional[str] = None, keep: int = 0,
                   max_age_s: float = 0.0) -> int:
    """清理临时 run 目录，返回删除数量。

    keep   —— 保留最近 N 个（按 mtime 倒序）。
    max_age_s —— 只删除比这更旧的；0 表示不看年龄。
    """
    root = root or workspace_tmp()
    dirs = list_run_dirs(root)
    if not dirs:
        return 0
    dirs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    now = time.time()
    removed = 0
    for i, p in enumerate(dirs):
        if i < keep:
            continue
        try:
            if max_age_s and now - os.path.getmtime(p) < max_age_s:
                continue
            shutil.rmtree(p, ignore_errors=True)
            if not os.path.exists(p):
                removed += 1
        except OSError:
            continue
    return removed


def _base_flags(info: ChromeInfo) -> list[str]:
    flags = [
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--hide-scrollbars",
        "--force-color-profile=srgb",
        "--font-render-hinting=none",
        "--disable-lcd-text",
        "--allow-file-access-from-files",
        "--disable-extensions",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,BackForwardCache",
    ]
    if info.kind == "chrome" and _major(info.version) >= 112:
        # 新版 chrome 用 --headless=new 更贴近真实渲染
        pass
    return flags


def _major(version: str) -> int:
    m = re.match(r"(\d+)", version)
    return int(m.group(1)) if m else 0


class ChromeError(RuntimeError):
    pass


def dump_dom(info: ChromeInfo, url: str, budget_ms: int = 15000,
             extra: Optional[list[str]] = None,
             timeout: int = 120, retries: int = 3) -> str:
    """打开 url，等 JS 跑完后返回序列化后的 DOM 文本。"""
    flags = _base_flags(info) + [f"--virtual-time-budget={budget_ms}",
                                 "--run-all-compositor-stages-before-draw",
                                 "--dump-dom"]
    if extra:
        flags += extra
    last = ""
    for attempt in range(1, retries + 1):
        proc = subprocess.run([info.path] + flags + [url],
                              capture_output=True, timeout=timeout)
        html = (proc.stdout or b"").decode("utf-8", "replace")
        if html.strip():
            return html
        last = (proc.stderr or b"").decode("utf-8", "replace")[-800:]
        if attempt < retries:
            time.sleep(1.2 * attempt)
    raise ChromeError(f"--dump-dom 连续 {retries} 次无输出。stderr 尾部：\n{last}")


def print_to_pdf(info: ChromeInfo, url: str, out_pdf: str,
                 budget_ms: int = 15000, extra: Optional[list[str]] = None,
                 timeout: int = 180, retries: int = 3) -> str:
    """把 url 打印为 PDF。out_pdf 必须是**无空格绝对路径**。"""
    out_pdf = os.path.abspath(out_pdf)
    if " " in out_pdf:
        raise ChromeError(
            f"--print-to-pdf 目标路径含空格，Chrome 会失败：{out_pdf}\n"
            "请先复制到无空格临时目录再移回。"
        )
    os.makedirs(os.path.dirname(out_pdf), exist_ok=True)
    flags = _base_flags(info) + [
        f"--virtual-time-budget={budget_ms}",
        "--run-all-compositor-stages-before-draw",
        "--no-pdf-header-footer",
        "--print-to-pdf-no-header",
        f"--print-to-pdf={out_pdf}",
    ]
    if extra:
        flags += extra
    last = ""
    for attempt in range(1, retries + 1):
        if os.path.exists(out_pdf):
            os.remove(out_pdf)
        proc = subprocess.run([info.path] + flags + [url],
                              capture_output=True, timeout=timeout)
        if os.path.exists(out_pdf) and os.path.getsize(out_pdf) > 800:
            return out_pdf
        last = (proc.stderr or b"").decode("utf-8", "replace")[-800:]
        if attempt < retries:
            time.sleep(1.2 * attempt)
    raise ChromeError(f"--print-to-pdf 连续 {retries} 次失败。stderr 尾部：\n{last}")


def screenshot(info: ChromeInfo, url: str, out_png: str, width: int, height: int,
               budget_ms: int = 15000, extra: Optional[list[str]] = None,
               timeout: int = 180, retries: int = 3) -> str:
    """整页截图（PNG）。用于预览 —— SVG 里的文字在 foreignObject 中，
    PyMuPDF 直接渲染会变成黑块，只有 Chrome 能正确画出 HTML 文字。"""
    out_png = os.path.abspath(out_png)
    if " " in out_png:
        raise ChromeError(f"--screenshot 目标路径含空格：{out_png}")
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    flags = _base_flags(info) + [
        f"--virtual-time-budget={budget_ms}",
        f"--window-size={int(width)},{int(height)}",
        "--screenshot=" + out_png,
        "--default-background-color=00000000",
    ]
    if extra:
        flags += extra
    last = ""
    for attempt in range(1, retries + 1):
        if os.path.exists(out_png):
            os.remove(out_png)
        proc = subprocess.run([info.path] + flags + [url],
                              capture_output=True, timeout=timeout)
        if os.path.exists(out_png) and os.path.getsize(out_png) > 500:
            return out_png
        last = (proc.stderr or b"").decode("utf-8", "replace")[-800:]
        if attempt < retries:
            time.sleep(1.2 * attempt)
    raise ChromeError(f"--screenshot 连续 {retries} 次失败。stderr 尾部：\n{last}")


def check_fonts() -> dict:
    """粗略检查中文字体可用性（Windows 直接看字体目录）。"""
    result = {"cjk_fonts": [], "fallback_ok": False, "dir": ""}
    if sys.platform.startswith("win"):
        fdir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        result["dir"] = fdir
        want = {
            "msyh.ttc": "Microsoft YaHei",
            "msyhbd.ttc": "Microsoft YaHei Bold",
            "simsun.ttc": "SimSun",
            "simhei.ttf": "SimHei",
            "Deng.ttf": "DengXian",
        }
        if os.path.isdir(fdir):
            for fn, label in want.items():
                if os.path.isfile(os.path.join(fdir, fn)):
                    result["cjk_fonts"].append(label)
    else:
        try:
            out = subprocess.run(["fc-list", ":lang=zh"], capture_output=True,
                                 text=True, timeout=25)
            for line in (out.stdout or "").splitlines()[:12]:
                result["cjk_fonts"].append(line.strip())
        except Exception:  # noqa: BLE001
            pass
    result["fallback_ok"] = bool(result["cjk_fonts"])
    return result