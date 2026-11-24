# -*- coding: utf-8 -*-
"""Markdown → simple-mind-map 节点树（data.json）。

解析口径（对齐既有 3 章 md 的书写体例）：
  #       → 根节点
  ##      → 一级分支
  ###     → 二级
  ####    → 三级
  - / *   → 列表层级，按缩进（2 空格或 1 tab = 1 级）
  1. 2.   → 有序列表，同样按缩进
  段落     → 附着到最近的节点（不新增子节点，避免节点爆炸）

语义标记（区别于普通字符）：
  ⚠️ / ⚠ 开头 → 节点带 __mmk_mark="warn"（渲染层转矢量三角）
  （图9-41）(图2-5) → __mmk_tag="fig"，值 "9-41"
  （表9-3）(表2-10)  → __mmk_tag="tab"，值 "9-3"

⚠ 与 SMM 字段名冲突：data.tag 是保留字段（须为字符串数组），
  data.note 亦被占用，故所有自定义语义字段统一 __mmk_ 前缀。
  **粗体**     → 保留富文本标记，交给 SMM 富文本渲染
  行内 `code`  → 保留
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

WARN_RE = re.compile(r"^\s*(?:⚠️|⚠)\s*")
FIG_RE = re.compile(r"[（(]\s*图\s*([0-9]+[-–—][0-9]+)\s*[）)]")
TAB_RE = re.compile(r"[（(]\s*表\s*([0-9]+[-–—][0-9]+)\s*[）)]")
HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")
UL_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
OL_RE = re.compile(r"^(\s*)(\d{1,2})[.)]\s+(.*)$")
HR_RE = re.compile(r"^\s*(?:---+|\*\*\*+|___+)\s*$")
FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


@dataclass
class Node:
    text: str
    level: int = 0                      # 0=根 1=一级分支 2=二级 ...
    kind: str = "heading"               # heading | list | paragraph
    mark: Optional[str] = None          # warn
    tag: Optional[str] = None           # fig | tab
    tag_value: Optional[str] = None
    note: Optional[str] = None          # 备注（段落聚合）
    children: list["Node"] = field(default_factory=list)

    def to_dict(self) -> dict:
        # 注意：SMM 的 data.tag 是保留字段（要求字符串数组），
        # data.note 亦被占用，故自定义语义字段统一加 __mmk_ 前缀。
        data: dict = {"text": self.text}
        if self.mark:
            data["__mmk_mark"] = self.mark
        if self.tag:
            data["__mmk_tag"] = self.tag
            data["__mmk_tagValue"] = self.tag_value
        if self.note:
            data["__mmk_note"] = self.note
        return {"data": data, "children": [c.to_dict() for c in self.children]}


def _clean(text: str) -> str:
    return text.replace("\u200b", "").strip()


def _indent_width(ws: str) -> int:
    return len(ws.replace("\t", "  ")) // 2


def _apply_marks(node: Node, raw: str) -> str:
    """抽取 ⚠️ / 图号 / 表号，返回清洗后的正文。"""
    text = raw
    if WARN_RE.match(text):
        node.mark = "warn"
        text = WARN_RE.sub("", text, count=1)
    m = FIG_RE.search(text)
    if m:
        node.tag = "fig"
        node.tag_value = m.group(1).replace("–", "-").replace("—", "-")
    else:
        m = TAB_RE.search(text)
        if m:
            node.tag = "tab"
            node.tag_value = m.group(1).replace("–", "-").replace("—", "-")
    return _clean(text)


class _Stack:
    """维护 heading / list 两条独立的层级栈。"""

    def __init__(self, root: Node) -> None:
        self.root = root
        self.head: list[Node] = [root]

    def push_heading(self, node: Node, level: int) -> None:
        while len(self.head) > level:
            self.head.pop()
        parent = self.head[-1]
        parent.children.append(node)
        self.head.append(node)

    def last_heading(self) -> Node:
        return self.head[-1]


def parse(md_text: str, *, max_depth: int = 7,
          keep_paragraphs: bool = False,
          root_title: Optional[str] = None) -> tuple[Node, dict]:
    """把 markdown 解析成节点树，同时返回统计信息。"""
    root = Node(text=root_title or "思维导图", level=0, kind="heading")
    st = _Stack(root)
    list_stack: list[tuple[int, Node]] = []   # (缩进级别, 节点)
    stats = {"headings": 0, "list_items": 0, "warns": 0, "figs": 0, "tabs": 0,
             "paragraphs_dropped": 0, "skipped_blocks": 0}

    in_fence = False
    pending_note_for: Optional[Node] = None

    for raw_line in md_text.splitlines():
        line = raw_line.rstrip()

        if FENCE_RE.match(line):
            in_fence = not in_fence
            stats["skipped_blocks"] += 1
            continue
        if in_fence:
            continue

        if not line.strip():
            pending_note_for = None
            continue
        if HR_RE.match(line):
            continue
        if TABLE_ROW_RE.match(line):
            stats["skipped_blocks"] += 1
            continue

        m = HEAD_RE.match(line)
        if m:
            if len(m.group(1)) == 1 and root_title is None and not root.children:
                title = _clean(m.group(2))
                root.text = title
                stats["headings"] += 1
                list_stack.clear()
                continue
            lvl = len(m.group(1)) - (0 if root_title else 1)
            lvl = max(1, min(lvl, max_depth))
            node = Node(text="", level=lvl, kind="heading")
            node.text = _apply_marks(node, m.group(2))
            if node.mark:
                stats["warns"] += 1
            if node.tag == "fig":
                stats["figs"] += 1
            elif node.tag == "tab":
                stats["tabs"] += 1
            st.push_heading(node, lvl)
            list_stack.clear()
            stats["headings"] += 1
            pending_note_for = node
            continue

        m = UL_RE.match(line) or OL_RE.match(line)
        if m:
            indent = _indent_width(m.group(1))
            body = m.group(2) if m.re is UL_RE else m.group(3)
            parent = st.last_heading()
            while list_stack and list_stack[-1][0] >= indent:
                list_stack.pop()
            if list_stack and list_stack[-1][0] == indent - 1:
                parent = list_stack[-1][1]
            elif list_stack and list_stack[-1][0] < indent - 1:
                parent = list_stack[-1][1]          # 跳级时挂到最近一级
            depth = parent.level + 1
            if depth > max_depth:
                stats["paragraphs_dropped"] += 1
                continue
            node = Node(text="", level=depth, kind="list")
            node.text = _apply_marks(node, body)
            if node.mark:
                stats["warns"] += 1
            if node.tag == "fig":
                stats["figs"] += 1
            elif node.tag == "tab":
                stats["tabs"] += 1
            parent.children.append(node)
            list_stack = [x for x in list_stack if x[0] < indent]
            list_stack.append((indent, node))
            stats["list_items"] += 1
            pending_note_for = node
            continue

        # 普通段落：默认丢弃（避免节点爆炸），可选折叠成父节点备注
        if keep_paragraphs and pending_note_for is not None:
            if pending_note_for.note:
                pending_note_for.note += " " + _clean(line)
            else:
                pending_note_for.note = _clean(line)
        else:
            stats["paragraphs_dropped"] += 1

    stats["node_count"] = _count(root)
    stats["max_level"] = _max_level(root)
    return root, stats


def _count(n: Node) -> int:
    return 1 + sum(_count(c) for c in n.children)


def _max_level(n: Node) -> int:
    return max([n.level] + [_max_level(c) for c in n.children])


def to_smm(root: Node) -> dict:
    return root.to_dict()


def load(path: str, **kw) -> tuple[Node, dict]:
    with open(path, encoding="utf-8") as fh:
        return parse(fh.read(), **kw)


def save_json(root: Node, stats: dict, path: str, meta: Optional[dict] = None) -> None:
    payload = {"meta": {"nodeCount": stats.get("node_count"),
                        "maxLevel": stats.get("max_level"),
                        "stats": stats, **(meta or {})},
               "tree": to_smm(root)}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)