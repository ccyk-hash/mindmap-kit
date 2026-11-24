---
name: mindmap-kit
description: >
  把 Markdown 笔记 / 教材章节 / 会议纪要渲染成出版级思维导图，一键出 SVG + 单页 PDF + PNG。
  内置 6 套主题（莫兰迪学术、水墨线稿、纸张卡片、蓝图、森林、暗色霓虹），叠加编号徽标、
  右侧分区索引栏、页脚数据卡、符号图例四层装饰，配八项质量门禁（WCAG 对比度、字号阶梯、
  连线 3-2-1 律、单行长度、文字溢出、PDF 单页、标题覆盖率）与基准快照回归。
  适用于"把这份 md 做成思维导图 / 脑图 / 知识地图 / 章节结构图"、"思维导图排版美化 /
  换主题 / 出 PDF 贴教材"、"检查脑图有没有文字溢出、对比度够不够"等需求。
  纯本地执行（Chrome 无头打印），不上传任何内容。
version: 1.0.0
metadata:
  agent_created: true
---

# mindmap-kit —— Markdown → 出版级思维导图

一条命令从 md 到可交付的三件套：**矢量 SVG**（可再编辑）、**单页 PDF**（文字可选中可搜索）、**预览 PNG**。
渲染引擎是 `simple-mind-map`（本地 vendor，`file://` 直开，零构建、零联网），排版引擎是自己写的
`kit/svgtext.py`（把 foreignObject 里的 HTML 拍平成原生 `<text>`），装饰层与门禁各一个模块。

---

## 1. 何时调用 / 不适用边界

### 触发场景

- 用户要求把一份 md / 笔记 / 教材章节 / 提纲"做成思维导图 / 脑图 / 知识地图 / 结构图"
- 已有脑图要**换主题、加装饰、出 PDF/PNG**、贴进讲义或课件
- 要**检查**一份脑图产出的质量（文字有没有溢出、对比度够不够、PDF 是不是单页、标题漏没漏）
- 要一份**长图**（几千 px 高、单页 PDF、可直接打印或投屏）

### 不适用（先判断，别硬套）

- **输入不是 Markdown**：Word / PDF / 图片请先转成 md（`markitdown-skill`、`pdf`、`scanned-pdf-textbook-reader`）
- **要交互式可折叠脑图**：本技能产出静态 SVG/PDF/PNG，不含 JS 交互；交互需求请直接用 simple-mind-map 源码
- **要横向超宽的鱼骨/组织架构大图**：`--layout fishbone|org` 可出，但极宽画布打印会缩得很小，先确认用途
- **机器上没有 Chrome**：PDF/PNG 出不来（SVG 仍可出）。先跑 `mmk doctor` 确认 `[OK] browser`
- **要 XMind / FreeMind 格式互转**：本技能不读写 `.xmind`

---

## 2. 命令速查（全部实测可跑）

入口脚本 `bin/mmk`（bash / Git-Bash / MSYS）。所有命令都在**任意目录**可用，路径可相对当前目录。

```bash
MMK="<skill 目录>/bin/mmk"

# ① 环境自检（第一次用必跑）
bash "$MMK" doctor

# ② 一条命令出全套（SVG + PDF + PNG + report.json）
bash "$MMK" build 笔记.md --theme morandi -o out/笔记

# ③ 只出 SVG，快（约 1.5s，调样式时用）
bash "$MMK" build 笔记.md --theme ink --no-pdf --no-png -o out/试色

# ④ 质量门禁（只读产出，不信 build 自述）
bash "$MMK" audit out/笔记

# ⑤ 存基准 / 改完再比对，看哪些指标变了
bash "$MMK" audit out/笔记 --save-golden 笔记v1
bash "$MMK" audit out/笔记 --compare-golden 笔记v1

# ⑥ 主题清单 / 看某套主题的完整令牌
bash "$MMK" themes list
bash "$MMK" themes show morandi
```

### `build` 全部参数

| 参数 | 取值 | 默认 | 说明 |
|---|---|---|---|
| `src` | md 路径 | 必填 | 源文件 |
| `--theme` | 六套之一 | `morandi` | 见 §3 决策树 |
| `--layout` | `tree` / `balanced` / `org` / `fishbone` / `timeline` / `catalog` | `tree` | 结构布局 |
| `--font-scale` | 0.85 ~ 1.15 | `1.0` | 全局字号缩放；超出范围会破坏字号阶梯门禁 |
| `-o, --out` | 目录 | 与源同名目录 | 产物目录 |
| `--decorate` | `none` / `badges` / `index` / `footer` / `full` | `full` | 装饰层，见 §5 |
| `--meta-line` | 任意字符串，空串则不显示 | `MINDMAP-KIT` | 页脚小字 |
| `--png-scale` | 浮点 | `0.3` | 预览图缩放 |
| `--png-max-px` | 整数 | `14000` | 预览图长边上限 |
| `--no-pdf` / `--no-png` | flag | — | 跳过对应产物 |
| `--keep-tmp` | flag | — | 保留 Chrome 临时目录（调试用） |

### 产物结构

```
out/笔记/
├── 笔记.svg          矢量源（可再编辑，文字是原生 <text>）
├── 笔记.pdf          单页 PDF（文字可选中可搜索）
├── 笔记.preview.png  预览图
├── 笔记.data.json    节点树（喂给 simple-mind-map 的原始 data）
└── report.json       构建元数据 + audit 写入的审计结论
```

`report.json` 顶层键：`source / theme / layout / fontScale / version / svg / size / stats / parseStats /
textFlatten / decorate / pdf / preview / totalSeconds / audit`。`audit` 段由 `mmk audit` 自动回写。

---

## 3. 主题选择决策树

六套主题不是换色，而是**字体 + 形状 + 圆角 + 纹理 + 填充策略 + 明暗**六个维度同时不同。

| 主题 | label | 底 / 字 | 字体 | 形状 | 纹理 | 什么时候选 |
|---|---|---|---|---|---|---|
| `morandi` | 莫兰迪学术 | `#FCFCFB` / `#1F2733` | 雅黑 | 圆角 | 无 | **默认首选**。出版级、讲义、复习卡、给老师看 |
| `paper` | 纸张卡片 | `#FBF6EC` / `#2C2620` | 等线 | 圆角 | 纸纹 | 最像教材插图；要"印在纸上"的观感 |
| `ink` | 水墨线稿 | `#F7F5F0` / `#1E1E1C` | 宋体 | **全直角** | 宣纸 | 极简、去填充只留墨线；文科/人文类内容 |
| `blueprint` | 蓝图 | `#0E2A47` / `#D8E8F5` | 雅黑 | 圆角 | 网格 | 工程制图气质；技术架构、流程、系统图 |
| `forest` | 森林 | `#F4F7EF` / `#22301F` | 雅黑 | 圆角 | 圆点 | 自然耐看，**超长图**（几百节点）不易疲劳 |
| `neon` | 暗色霓虹 | `#0B0D12` / `#E6ECF5` | 雅黑 | 圆角 | 噪点 | 投屏演示、暗色阅读、科技感 |

**速选口诀**：拿不准就 `morandi`；要印在纸上 → `paper`；要极简高级 → `ink`；要暗底 → `neon`（投屏）或 `blueprint`（工程）；节点特别多 → `forest`。

四套浅色（morandi / paper / ink / forest）适合打印与 PDF；两套深色（blueprint / neon）打印会糊，只投屏或屏幕阅读。

---

## 4. Markdown 书写约定（决定图长什么样）

`kit/parse.py` 的解析口径，**这是产出质量的 80%**：

| md 语法 | 变成什么 |
|---|---|
| `# 标题` | 根节点（图上唯一居中那个） |
| `## 二级标题` | 一级分支（会得到编号徽标 ①②③…） |
| `### 三级` | 二级节点 |
| `#### 四级` | 三级节点（最多到 `max_depth=7`） |
| `- / * / +` 列表 | 子节点，**按缩进分层**（2 空格或 1 tab = 1 级） |
| `1. 2. 3.` 有序列表 | 同上，按缩进 |
| 普通段落 | **默认丢弃**（`paragraphs_dropped` 计数），不新增子节点——防止节点爆炸。需要保留时改写成列表项 |
| `\| 表格 \|` | **整行跳过**（`skipped_blocks` 计数）。表格留在 md 正文，不进脑图 |

⚠ **段落默认不进图**。这是刻意的：教材正文的长段落如果都变成节点，一张图会瞬间膨胀到上千节点。
要保留的信息必须写成 `- ` 列表项或 `###` 标题。

### 三个语义标记（会渲染成图形，别当普通字符）

| 写法 | 效果 |
|---|---|
| 行首 `⚠ ` 或 `⚠️ ` | 节点转成**易错警示**样式 + 装饰层画矢量三角图标，计入页脚"警示数" |
| 正文含 `（图2-5）` `（图9-41）` | 标为**插图锚点**，编号自动加粗（不再额外追加括注），计入"插图数" |
| 正文含 `（表2-3）` `（表9-1）` | 标为**表格锚点**，同上，计入"表格数" |

⚠ **一个节点只挂一个 tag**：同时出现图号和表号时**图号优先**（`parse.py:_apply_marks` 的 `if fig ... else tab`），表格统计会漏。要两个都统计就拆成两个节点。

`**粗体**` 与行内 `` `代码` `` 原样保留（走富文本渲染）。

### 写多长才好看（实测口径）

- **一级分支 5~15 个**最舒服；超过 20 个建议拆成两张图
- **每个叶子节点 8~20 个汉字**。超过 20em（约 20 个汉字宽）门禁会给 WARN
- **总节点数**：`<300` 单页清爽；`300~700` 需要 `forest`/`paper` 这类耐看主题；`>1000` 建议拆章
- **层级深度**：3~4 层最好读，5 层勉强，6 层以上要靠右侧索引栏定位
- 描述性长句（整段知识点）会作为叶子节点出现，**物理上装不进 20 字**——门禁把它们单列为"描述超 20em N 行（仅提示）"，不算失败

---

## 5. 装饰层（`--decorate`）

| 取值 | 出什么 |
|---|---|
| `none` | 纯 SMM 渲染，无任何装饰。做 A/B 对比的"before"用这个 |
| `badges` | 只出一级分支左侧的**编号徽标**（双环圆 + 反白数字） |
| `index` | 只出右侧**分区索引栏** |
| `footer` | 出底部**数据卡 + 符号图例** |
| `full` | **全开**（默认） |

装饰层三原则（改代码时勿违背）：

1. **不碰节点几何**——SMM 布局已定死，装饰只往空白区长：向右 `INDEX_W=356 + INDEX_GAP=46`，向下 `FOOT_H=272`
2. **只产原生 `<text>`**——不过 foreignObject，保证 PDF 里可选中可搜索
3. **主题无关**——颜色从 `paper/ink/inkSoft/branches` 派生，`_is_dark(paper)` 阈值 0.42 自动翻转明暗

四块装饰的语义：

- **编号徽标**：`r=21` 双环圆，内圆填分支主色、外环 paper 色、反白编号；`cx = 节点左缘 - 34`
- **分区索引栏**：右侧独立栏，"分区索引 / SECTIONS" 标题 + 每行一个色块编号 + 分支标题；长图靠它定位
- **页脚数据卡**：标题 + 副标题 + `--meta-line` + 四张卡（节点数 / 分区数 / 插图数 / 警示数），数值直接取 `parseStats`，与 md 统计严格一致
- **符号图例**：把三角警示、图号、表号翻译成人话（"易错警示 · 20 处"），正文里的记号不再无解

### A/B 对比卡（要给人看"装饰做了什么"时）

```bash
bash "$MMK" build 笔记.md --theme morandi --decorate none --no-pdf --no-png -o out/before
bash "$MMK" build 笔记.md --theme morandi --decorate full --no-pdf --no-png -o out/after
python -c "
import sys; sys.path.insert(0, r'<skill 目录>')
from kit import ab_card
print(ab_card.build('out/before/笔记.svg','out/after/笔记.svg','out/ab.png',
                    theme='morandi', title='装饰层 A/B 对比'))
"
```

产出并排对比卡：顶栏标题 + BEFORE/AFTER 两栏 + 四枚编号圆指向四块装饰 + 底部 2×2 说明 + 结论行。

---

## 6. 质量门禁八项（`mmk audit`）

`audit` **只读产出文件**（SVG + PDF + report.json），不信 build 的自述。退出码：`0` 非 FAIL / `1` 有 FAIL / `2` 找不到基准。

| # | 项目 | 阈值 | FAIL 条件 |
|---|---|---|---|
| 1 | 画布规模 | — | 只报数，不判（用于横向对比） |
| 2 | 字号档位 | `≤5` 档，级差 `≥2px` | 超档位数或级差过密 |
| 3 | 连线阶梯 | `≥3` 级（3.0 / 2.0 / 1.3 的 3-2-1 律） | 级数不足 |
| 4 | 单行长度 | 标签 `≤20em`，硬上限 `32em` | 标签超 32em（描述层超长只 WARN 计数） |
| 5 | WCAG 对比度 | 正文 `≥4.5:1`，`≥24px` 大字 `≥3:1` | 有节点不足 |
| 6 | 文字溢出 | `0` 处 | 有节点文字画出圆角外 |
| 7 | PDF 交付 | 单页 + MediaBox `≤14400pt` + 文字 `≥50` 字符 | 多页 / 超限 / 无文字层 |
| 8 | 标题覆盖率 | H2 `100%`，H3 `≥95%` | 有标题没进图 |

阈值集中在 `kit/audit.py` 的 `THRESHOLDS` 字典，改门禁只改这一处。

### target 怎么给

| 给什么 | 跑几项 | 说明 |
|---|---|---|
| **产出目录**（推荐） | 八项全跑 | 自动找 `.svg` / 非 `.preview` 的 `.pdf` / 读 `report.json` 取源文与主题 |
| `.svg` | 六项 | 缺 PDF 交付与标题覆盖率，可补 `--source` |
| `.pdf`（同目录有同名 `.svg`） | 八项全跑 | 自动配对 |
| `.pdf`（无同名 `.svg`） | 直接 FAIL | `只给 PDF 无法审计（八项里七项要读 SVG）` |

`--source` 补源 md（覆盖 `report.json` 里记的路径）；`--theme` 取 paper 与字号表，**必须与 build 时一致**——传错主题会拿另一套底色算对比度，得出假的 FAIL（实测 `--theme neon` 套在 paper 产出上 → `最低 1.08:1 · 不足 40 处`）。

### 两个容易踩的门禁语义

- **连线阶梯按"实际出现的层数"算**：md 只有 `##` 没有 `###`（两层结构）时只会有 2 级连线 → `[FAIL] 连线阶梯 2 级`。这不是 bug，是提醒"图太扁平"。补一层 `###` 或加叶子项即可。
- **单行长度分标签层与描述层**：标签层（`size ≥ 20px`，即分支节点）超 `20em` 记 WARN、超 `32em` 记 FAIL；描述层（叶子节点，整句知识点）**只计数不判级**。所以 `描述超 20em 383 行` 是正常现象，不影响 VERDICT。

### 基准快照回归

```bash
bash "$MMK" audit out/笔记 --save-golden v1      # 存基准
# ...改主题 / 改样式 / 改 md...
bash "$MMK" audit out/笔记 --compare-golden v1   # 列出变了哪些指标
```

输出形如 `WCAG 对比度.worstRatio: 4.64 → 4.94（+0.30）`、`字号档位.levels: [14,16,20,28,44] → [14,16,19,26,42]`。
基准存 `golden/<name>.json`，只比归一化指标（比值、档位、计数），不比绝对像素。

---

## 7. 核心事实（勿违背，都是踩出来的）

1. **文字必须是原生 `<text>`**：Chrome 158 的 `--print-to-pdf` 对 `<foreignObject>` 内 HTML 的**多行排版只保住第一行**，PDF 里会缺行。`kit/svgtext.py` 的 `flatten()` 负责把 foreignObject 拍平成 `<text>`——这是整条流水线能出正确 PDF 的根本原因，任何改动都不能绕过它。
2. **PDF 只能走 Chrome 无头打印**：PyMuPDF 直转 SVG→PDF 会把文字变黑块（不嵌字体）。`export.to_pdf` 用 `--headless=new --print-to-pdf`。
3. **Chrome 打印在含空格的路径下会失败**：临时目录必须无空格。`chrome.workspace_tmp()` 已自动挑（`D:\mmk_tmp` 或 `C:\mmk_tmp`）。
4. **PDF 单边上限 14400pt**（200 英寸，PDF 1.7 §7.7.3.3）：超了 Chrome 能出但 Acrobat/预览会裁切。`to_pdf(fit_page=True)` 自动等比缩到 `14370.96pt`，且缩页系数必须留 `0.998` 余量，否则浮点误差会多印一页。
5. **换行宽度是 `textAutoWrapWidth`**：SMM 默认 500px（约 35em），节点几乎不折行。主题里已设 336（neon 315）。实测第2章：336 → 432 行、p95 19.2em、标签超 20em **0 行**；改回 500 → 308 行、p95 24.1em、标签超 20em **5 行**（门禁降为 WARN）。改大会让单行长度指标变差，改小会让图变高（336 比 320 高 +21.6%，比 500 高 +25%）。
6. **连线粗细挂在子节点上**：`d.lineWidth` 按 child 的深度取 `width1/width2/width3`。SMM 的 `styleLine` 读 `childNode.getSelfStyle('lineWidth')`，父节点设了没用。
7. **`data.tag` / `data.note` 是 SMM 保留字段**：所有自定义语义字段统一 `__mmk_` 前缀（`__mmk_mark` / `__mmk_tag` / `__mmk_tagValue` / `__mmk_note`）。
8. **富文本 `<p>` 必须归零 margin**：vendor CSS 没归零，1em margin 会吃掉 16px 节点的可见高度。
9. **等待必须用事件不是 rAF**：`mm.on('node_tree_render_end')` 再等 700ms；4×rAF 会拿到 112×112 空画布。

---

## 8. 常见故障与处理

| 现象 | 原因 | 处理 |
|---|---|---|
| `× 找不到可用的 Python 解释器。` | PATH 里没有真 Python（Windows 商店占位符不算） | `export MMK_PYTHON=C:/path/to/python.exe` |
| `[FAIL] browser chrome 未找到` | 没装 Chrome 或不在标准路径 | 装 Chrome；或只出 SVG（`--no-pdf --no-png`） |
| PDF 只有第一行文字 / 大片空白 | `flatten()` 被绕过或失败 | 检查 `report.json` 的 `textFlatten.lines` 是否 > 0；为 0 说明拍平没跑 |
| PDF 出来 2 页 | 缩页系数没留余量，或 `print.html` 的 `html,body` 宽高用了原始值而非 `__CW__/__CH__` | 确认 `to_pdf(fit_page=True)` 且 `print.html` 已用 `__CW__/__CH__` |
| `[FAIL] 文字溢出 N 处` | 长 ASCII token 找不到断点（如 `GlcNAc(β1→4)MurNAc`） | 确认 `render.html` 的 `.smm-richtext-node-wrap p` 有 `overflow-wrap:anywhere; word-break:break-word` |
| 溢出只差 5px 且含 `₃`/`⁵`/`×` | PIL 对上下标 fallback 码位量宽偏大（测量噪声） | 已由 `_FALLBACK_CHARS` 放宽容差；确认不是真溢出可目视裁剪核对 |
| `[WARN] 单行长度 … 描述超 20em 383 行` | 叶子节点是整句知识点，物理装不进 20 字 | 正常，不算失败；要更好读就把长句拆成 `###` + 子项 |
| `[FAIL] 标题覆盖率 H2 92.9%` | 节点折行后拼回去在词中插了空格 | `audit.py` 已用 `re.sub(r"\s+","",…)` 归一化；若仍缺，检查 md 里该标题是否被表格/代码块吞掉 |
| `[FAIL] WCAG 对比度 … 1.08:1` | 渐变填充 `url(#xxx)` 没解析出来，回退成 paper 底色 | 确认 `audit.py` 的 `_gradients()` / `_resolve_bg()` 在跑；自造主题时别用 `url()` 之外的怪填充 |
| `--compare-golden` 报找不到基准 | 名字不对或没存过 | 先 `--save-golden NAME`；基准在 `golden/NAME.json` |
| `--theme xxx 不存在` | 拼错 | `mmk themes list` 看六套名字 |
| 输出图特别宽/特别高 | `--layout` 选错（`org` 会横向拉很长） | 换回 `tree`（默认）或 `balanced` |

---

## 9. 目录结构

```
mindmap-kit/
├── SKILL.md              本文件
├── bin/mmk               命令行入口（bash；自动挑 Python、绕开 PYTHONPATH 方言）
├── kit/
│   ├── cli.py            子命令：doctor / themes / audit / build
│   ├── doctor.py         环境自检
│   ├── parse.py          md → 节点树（语义标记在这里识别）
│   ├── render.py         节点树 → SVG（驱动 Chrome 里的 simple-mind-map）
│   ├── svgtext.py        foreignObject → 原生 <text>（PDF 正确的关键）
│   ├── decorate.py       编号徽标 / 索引栏 / 页脚数据卡 / 图例
│   ├── export.py         SVG → PDF / PNG（Chrome 无头打印）
│   ├── audit.py          八项质量门禁 + 基准快照
│   ├── ab_card.py        装饰层 A/B 对比卡
│   ├── chrome.py         Chrome 查找、临时目录、打印、截图
│   └── themes/*.json     六套主题令牌
├── templates/            render.html（渲染页）/ print.html（打印页）
├── vendor/smm/           simple-mind-map 0.14.0-fix.3 UMD（本地，零联网）
├── examples/demo-reading.md
├── golden/               基准快照
└── out/                  产物默认落点
```

---

## 10. 典型工作流

### A. 一份教材章节 → 讲义插图

```bash
bash "$MMK" doctor                                    # 先确认环境
bash "$MMK" build 第2章.md --theme morandi -o out/ch02
bash "$MMK" audit out/ch02                            # 应该 OK 8/0/0
# 打开 out/ch02/第2章.pdf 目视检查，不满意就换主题重跑（约 5s/次）
bash "$MMK" build 第2章.md --theme paper -o out/ch02_paper
bash "$MMK" audit out/ch02_paper
```

### B. 已有产出要换风格，又怕改坏

```bash
bash "$MMK" audit out/ch02 --save-golden ch02_base     # 存基准
bash "$MMK" build 第2章.md --theme forest -o out/ch02  # 覆盖重出
bash "$MMK" audit out/ch02 --compare-golden ch02_base  # 看哪些指标动了
```

### C. 只调样式，快速迭代

```bash
bash "$MMK" build 笔记.md --theme ink --no-pdf --no-png -o out/try   # 约 1.5s
# 看 out/try/笔记.svg（浏览器直接打开），满意了再出全量
```

### D. 要证明"装饰层加了什么"

见 §5 末尾的 A/B 对比卡三行命令。

---

## 11. 性能参考（实测）

| 规模 | render | decorate | PDF | PNG | 总计 |
|---|---|---|---|---|---|
| 46 节点（demo） | 1.4s | 0.4s | 1.7s | 1.2s | **4.5s** |
| 264 节点（第2章） | 1.7s | 0.5s | 2.0s | 2.2s | **6.4s** |
| 702 节点（第9章，3315×21870px） | 2.6s | 0.7s | 2.1s | 0.9s | **6.5s** |

`--no-pdf --no-png` 可把单次迭代压到 1.5~2.6s。
