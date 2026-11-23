# 学生课堂 PPT 风格改造报告（student-ppt-style）

日期：2026-11-23
范围：让免登录学生端 `/c/{share_code}` 的画面与导出的 4:3 PPTX 一致。

## 需求来源
无正式设计文档；产品负责人在对话中确认的口径即为需求：学生看到的应是「4:3 白底幻灯片，一页一场景，标题 + 要点在幻灯片上（与 PPT 幻灯片一致），讲稿（narration）显示在幻灯片下方作为讲师备注，测验保持交互」。

## 变更内容

### 1. `templates/student_classroom.html`
- 新增 `.slide-stage` 容器，仅包裹 **场景标题 h2 + 要点列表 ul**（对应 PPT 幻灯片的 title + bullets）。
- 将 `#scene-narration` 从幻灯片内移出，放入 new `.speaker-notes` 区块，标注「讲稿」，位于幻灯片下方（对应 PPT 的 speaker notes / 备注）。
- `#scene-visual-hint` 同样保留在幻灯片下方。
- 翻页按钮、随堂测验、AIGC 标注、有效期页脚、`tojson` 注入 **完全未改**；JS 逻辑块原样保留（`#scene-title` / `#scene-bullets` / `#scene-narration` / `#scene-visual-hint` 的 id 不变，翻页仍会更新）。

### 2. `static/app.css`
- 新增 scoped 规则 `.student .slide-stage`：
  - `aspect-ratio: 4/3`、`width: 100%`、`max-width: 900px`、`margin: auto`
  - `background: #fff`、`color: #1a1a1a`（白底黑字，脱离原来深色主题）
  - flex 居中 + padding + `overflow: auto`，圆角/阴影模拟幻灯片
- `.student .slide-stage #scene-title`：`clamp(24px, 4vw, 40px)`，居中 + 下边框，模拟 PPT 标题。
- `.student .slide-stage .scene-bullets`：`clamp(18px, 2.6vw, 28px)`、更大行距与段距，模拟 PPT 正文要点。
- `.student .speaker-notes` / `-label`：深色卡片 + 黄色「讲稿」标签，承载幻灯片下方的备注文字。
- 删除旧的 `.student .scene-narration`（在卡内）与 `.student .scene-bullets`（inline-block）布局规则，避免与新结构冲突；测验样式一字未动。

### 3. `tests/test_student_courseware_page.py`
新增 4 个测试（不依赖 jsdom，静态 + 结构断言；仓库无 jsdom）：
- `test_slide_stage_holds_title_and_bullets`：`.slide-stage` 存在，标题/要点 DOM 祖先包含它，JS 仍更新两者。
- `test_narration_lives_in_speaker_notes_outside_the_slide`：narration 在 `.speaker-notes` 内、**不在** `.slide-stage` 内；有「讲稿」标签；visual_hint 也在幻灯片外。使用基于 `html.parser` 的 `_DomProbe` 做真实 DOM 祖先判定（非仅字符串顺序）。
- `test_slide_stage_css_is_white_4by3`：从 `static/app.css` 提取 `.student .slide-stage` 规则块，断言 `aspect-ratio: 4/3`、`max-width: 900px`、`margin: auto`、`background: #fff`、`color: #1a1a1a`。
- `test_quiz_remains_outside_the_slide`：`#quiz-box` 不在 `.slide-stage` 内。

## 测试与结果
- 红阶段（实现前）：`pytest tests/test_student_courseware_page.py -q` → 3 failed / 6 passed（符合预期）。
- 绿阶段（实现后）：`pytest tests/test_student_courseware_page.py -q` → **9 passed**。
- 全量：`pytest -q` → **245 passed, 1 warning**（241 基线 + 4 新增；warning 为 starlette/httpx 弃用提示，与本次改动无关）。

## 变更文件
- `templates/student_classroom.html`
- `static/app.css`
- `tests/test_student_courseware_page.py`
- `docs/student-ppt-style-report.md`（本报告）

## 疑虑 / 备注
1. **无浏览器行为测试**：仓库无 jsdom/Node 测试链，按要求采用「静态断言 + 结构 DOM 解析 + 既有测试」；翻页交互仅通过保留的 JS id 引用做静态验证，未做真实点击回放。
2. **并发改动**：任务开始时工作树干净；执行期间 `app/adapters/generation/deepseek.py` 出现了与本次任务无关的改动（topic 感知 material 拼接，疑似父会话/其他进程并发编辑）。我**未修改、也未暂存**该文件，提交仅包含上述 4 个文件。该改动仍留在工作树中，需由对应负责人处理。
3. **幻灯片溢出**：要点过多时依赖 `overflow: auto` 滚动，未做字号自动缩放；当前场景要点数量（≤5）在 4:3 框内可完整显示。
4. **AIGC/CSRF**：学生页为免登录 GET，无 CSRF token；AIGC 标注在顶栏，未受影响。
