# 更新日志

版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.0] - 2026-11-24

首个公开版本。

### 新增

**渲染流水线**
- Markdown → 节点树解析（`kit/parse.py`）：支持 `#`~`####` 层级、无序/有序列表按缩进分层、`⚠` 易错警示标记、`（图x-x）`/`（表x-x）` 锚点识别
- 节点树 → SVG 渲染（`kit/render.py` + `templates/render.html`），驱动 simple-mind-map 0.14.0-fix.3，`file://` 直开零构建
- **`foreignObject` 拍平引擎**（`kit/svgtext.py`）：把富文本节点的 HTML 布局重新用原生 `<text>`/`<tspan>` 排出，解决 Chrome 158 `--print-to-pdf` 对 foreignObject 多行排版只保留第一行的问题
- SVG → PDF / PNG 导出（`kit/export.py`），走 Chrome `--headless=new --print-to-pdf`，超 14400pt 自动等比缩页保持单页

**主题系统**
- 六套主题：`morandi` / `ink` / `paper` / `blueprint` / `forest` / `neon`
- 五维可配：分层字号令牌、14 组分支调色板、渐变、背景纹理（dots/grid/lines/paper/noise）、字体感知测量
- 字号阶梯固定 `44/28/20/16/14`，`--font-scale` 限幅 `0.85~1.15`

**装饰层**（`kit/decorate.py`）
- 编号徽标：一级分支左侧双环圆 + 反白序号
- 分区索引栏：右侧 356px 独立栏，色块 + 编号 + 行分隔
- 页脚数据卡：4 张卡，数值直取源 md 统计
- 符号图例：易错警示 / 结构插图锚点 / 数据表格锚点
- 细外框 + 主题无关派生色 + 明暗自动翻转
- 五档开关：`none` / `badges` / `index` / `footer` / `full`

**质量门禁**（`kit/audit.py`，728 行）
- 八项检查：画布规模 / 字号档位 / 连线阶梯 / 单行长度 / WCAG 对比度 / 文字溢出 / PDF 交付 / 标题覆盖率
- 只读产出文件，不信 build 自述
- 退出码 `0` 非 FAIL / `1` 有 FAIL / `2` 找不到基准，可直接挂 CI
- 基准快照与差异比对：`--save-golden` / `--compare-golden`，只比归一化指标
- 审计结论自动回写 `report.json` 的 `audit` 键

**其他**
- `kit/doctor.py` 环境自检（Python / Chrome / PyMuPDF / 中文字体 / 主题完整性）
- `kit/ab_card.py` 装饰层 A/B 对比卡生成
- `bin/mmk` 路径无关的 bash 入口，自动挑可用 Python 解释器
- `SKILL.md` AI agent 技能说明（11 节）

### 修复的引擎级问题

以下六条都是实测踩出来的，不是推测：

1. **连线粗细阶梯从未生效** —— 主题里写了 `line.width1/2/3`，但渲染时只设了 `borderWidth`，simple-mind-map 读 `getSelfStyle('lineWidth')` 取不到就落回默认 2px。修复后实测 263 条连线 = `3.0×14 + 2.0×42 + 1.3×207`，与各层节点数吻合。
2. **`textAutoWrapWidth` 默认 500px** —— 约 35em，节点几乎不折行，出现单行 68 字。六套主题改为 336（`neon` 315）。
3. **渐变填充导致对比度误报** —— 一级节点 `fill="url(#...)"`，解析器取不到颜色就回退 paper 底，与白字算出 1.08:1 的假 FAIL。修复：解析 `linearGradient`/`radialGradient` 色标，取最不利端计算。
4. **PDF MediaBox 超限** —— 大章输出 16403pt，超 PDF 规范 14400pt 上限，部分阅读器裁切。修复：`fit_page` 自动等比缩页，留 0.2% 余量避免浮点多印一页。
5. **富文本无断词策略** —— ASCII 开头长 token（如 `GlcNAc(β1→4)MurNAc`）找不到断点，溢出圆角 68px。修复：`overflow-wrap: anywhere; word-break: break-word`。
6. **单行长度判定口径错误** —— 原按字符数判 20 字，把 `纯化 → 拆链 → 专一`（23 字符但仅 16.1em）误判超长。修复：改按 em 当量，且分标签层（`size≥20px`，判级）与描述层（叶子整句，只计数）。

### 已知限制

- 单页输出，超 14400pt 自动缩放不分页
- 产物为静态 SVG/PDF，无交互
- 中文字体依赖系统安装
- 装饰位置按几何推算，宽高比 > 8:1 的极端图索引栏可能显空
- `audit` 文字量宽用 PIL 近似，含 fallback 码位时有 2~6% 偏差（已用 5% 容差吸收）
