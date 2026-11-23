# 网站美化实施报告 —「你自己的课堂」

- 日期：2026-11-23
- 设计依据：`docs/superpowers/specs/2026-11-23-PPT与网站美化设计.md` §2 / §4
- 范围：网站视觉（纯 CSS / 内联样式颜色值），与已提交的 PPTX 深蓝金主题统一
- 用户确认：**米白 #F7F4EC 页面背景保留**
- 约束：不引入构建链；不动路由 / Python / JS / CSRF / 权限 / 文案 / class 名

## 1. 变更内容

### `static/app.css`（整体重写，结构按区块组织）
- `:root` 定义设计 §2 精确调色板并加注释：
  - `--navy:#0F2A47`（顶栏/标题/主按钮）、`--navy-2:#1B3E5C`（卡片/区块）、
    `--gold:#C9A227`（强调/描边/图标）、`--gold-soft:#E8D08D`（次要高亮/深底文字）、
    `--cream:#F7F4EC`（页面背景，保留）、`--ink:#1F2933`（正文）、`--muted:#6B7280`（辅助）。
  - 由同色 alpha 派生 `--line` / `--gold-line` / `--gold-glow` / `--on-dark` 用于细线、焦点圈、深底文字；
    `--ok` / `--err` 语义色保持不变；保留 `--primary` / `--bg` 别名向后兼容。
- 顶栏：深蓝底 + 金色下边线 + 金色品牌标记（`✦`），导航浅色、hover/active/aria-current 金色。
- 按钮：主按钮深蓝底 + 金色描边 + 浅金文字，hover 切次深蓝并加金色外发光；次按钮白底深蓝描边，hover 金色描边。
- 卡片：白底、圆角、柔和深蓝阴影、**金色顶部细线**；Hero 为深蓝渐变 + 金色顶线，表头为米白 + 金色下边线。
- 标题深蓝（Hero 内白字），链接深蓝 / hover 金色，正文深灰、辅助文字灰；表单焦点金色光圈；标签/步骤条/二维码点阵统一为深蓝金。
- 学生页（`body.student`）：外围改为深蓝渐变壳，顶栏/按钮/卡片/讲稿区/测验区全部换成深蓝金配色；
  「讲稿」标签为金底深蓝字，旁白/解析为浅金或米白字。
  - **4:3 白色幻灯片几何完全未动**：`.student .slide-stage` 仍为 `aspect-ratio: 4/3`、`max-width: 900px`、
    `margin: auto`、`background: #fff`、`color: #1a1a1a`（现有测试 `test_slide_stage_css_is_white_4by3` 锁定）。
    仅把标题下划线改为金色，未改尺寸/结构/JS。

### 独立内联样式的模板（只改颜色值，未动结构 / class / 文案）
- `templates/login.html`：米白渐变背景 + 金顶线白卡片 + 深蓝标题 + 深蓝底浅金字主按钮 + 金色焦点圈。
- `templates/pricing.html`：米白背景 + 深蓝标题/价格 + 金顶线卡片 + 推荐卡片金色描边 + 浅金标签；绿/橙语义色保留。
- `templates/admin/base.html`：米白底 + 深蓝标题/表头 + 金顶线卡片 + 金色 hover。

## 2. 测试与结果

| 命令 | 结果 |
|---|---|
| `.venv-sdd\Scripts\python.exe -m pytest tests/test_pages.py tests/test_student_courseware_page.py -q` | **20 passed**（改动前 20 passed，改动后 20 passed） |
| `.venv-sdd\Scripts\python.exe -m pytest -q` | **249 passed**，1 warning（Starlette/httpx 弃用提示，与本次无关） |

补充静态核对：
- 旧紫色系字面量（`#4b6bfb` / `#7b4bfb` / `#f6f7fb` / `#eef1ff` / `#111827` / `#1f2937` / `#93c5fd` 等）在 `static/*.css` 与 `templates/**/*.html` 中已无残留。
- 7 个调色板色值均在 `app.css` 的 `:root` 中精确定义。
- 学生页 DOM 结构断言（讲稿在幻灯片外、测验在幻灯片外、4:3 规则）全部通过。
- 模板 diff 仅涉及 `<style>` 块内的声明，未删除任何 class 名或中文文案；无价格字面量新增。

## 3. 变更文件

- `static/app.css`（重写，深蓝金主题）
- `templates/login.html`（内联样式配色）
- `templates/pricing.html`（内联样式配色）
- `templates/admin/base.html`（内联样式配色）
- `docs/website-beautify-report.md`（本报告）

## 4. 关注点 / 已知限制

- 本会话没有桌面浏览器接入（`browser.disconnected`），因此**未做实机截图核对**；验证以上述自动化测试 + CSS/DOM 静态检查为准。设计 §6 的浏览器目视检查建议由负责人本地执行。
- 米白底上直接使用 `#C9A227` 文字对比度偏低，因此正文链接保持深蓝、金色用于 hover / 描边 / 图标 / 深底文字（浅金 `#E8D08D`），兼顾设计调性与可读性。
- 学生页外围采用深蓝渐变而非米白：设计 §4 明确要求「学生页顶栏/按钮/讲稿区换成深蓝金配色」，白色幻灯片在深底上对比更强；主站页面仍为米白背景。
- 测验正确/错误反馈、价格页复购徽章沿用既有绿/橙语义色（非 §2 主调色板），属功能性状态色，未改动。
- 未新增测试：本次为纯视觉改动，现有 CSS/DOM 测试已覆盖幻灯片几何与页面渲染；全量 249 全绿。
