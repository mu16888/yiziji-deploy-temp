# PPT 美化实施报告 —「你自己的课堂」

- 日期：2026-11-23
- 设计依据：`docs/superpowers/specs/2026-11-23-PPT与网站美化设计.md` §2/§3
- 范围：`app/services/export.py` 的 `build_pptx(courseware) -> bytes`（PPT 部分）；网站 CSS 不在本次范围
- 用户确认：内容页保留 **米白 #F7F4EC** 背景

## 1. 变更内容

### `app/services/export.py`
- 封面与场景页全部改为**显式形状构建**，使用空白版式 `slide_layouts[6]`，不再依赖默认 title/content 版式及 placeholders。
- 统一定义设计 §2 调色板常量：深蓝 `0F2A47`、次深蓝 `1B3E5C`、金 `C9A227`、浅金 `E8D08D`、米白 `F7F4EC`、正文 `1F2933`、辅助灰 `6B7280`。
- 新增三个辅助函数避免重复形状代码：`_add_rect`（实心无边框矩形）、`_add_text`（多段落文本框）、`_save`（确定性写盘）。
- **封面**：满版深蓝背景 + 顶部金色细线 + 居中 36pt 白色标题 + 浅金 14pt 学科/年级/AIGC 信息 + 底部金色装饰条。
- **场景页**：米白满版背景 + 1.1 英寸深蓝标题带 + 左侧金色竖条 + 26pt 白色标题（长标题降为 24pt）+ 右上金色序号 `01 / 02` + 金色 `●` 项目符号与 `1F2933` 正文（按总字符数自适应 13/15/16/18pt、自动换行）+ 底部金色细线、AIGC 角标与页码。
- 保持：4:3（`Inches(10) x Inches(7.5)`）、接口签名、免费导出（api 未改）、AIGC 标识、讲稿写入 speaker notes、空字段跳过。
- **确定性输出修复**：python-pptx 保存时给每个 ZIP 条目写入当前时间戳，导致两次导出相隔超过 2 秒即字节不同（`test_export_does_not_charge` 的 deterministic replay 断言偶发失败）。`_save` 用固定时间戳（1980-01-01）重写 ZIP，保留压缩类型与外部属性，使导出真正可重放。该隐患为既有问题，主题化后构建耗时增加使其暴露。

### `tests/test_export.py`
- 颜色/形状检查辅助：`_solid_shapes`、`_has_color`。
- `test_long_bullets_use_smaller_font` 改为从形状中查找金色 `●` 标记所在段落的正文 run（不再访问已不存在的 `placeholders[1]`），保留原断言语义：长文本字号 ≤ 短文本且 ≤ 15pt。
- 新增 `test_cover_uses_deep_blue_and_gold`：封面满版深蓝 + 金色元素 + 标题文本。
- 新增 `test_scene_has_title_band_and_gold_sequence`：场景米白背景 + 深蓝标题带 + 金色竖条 + `01 / 02` 序号。
- 所有既有导出测试（4:3、场景数与 notes、空字段跳过、免费/权限/幂等、编辑页下载链接）保持不变并通过。

## 2. 测试与结果

| 命令 | 结果 |
|---|---|
| `.venv-sdd\Scripts\python.exe -m pytest tests/test_export.py -q` | **11 passed**（首次 33s，修复确定性后 46.8s） |
| `.venv-sdd\Scripts\python.exe -m pytest -q` | **249 passed**（基线 247 + 新增 2），1 warning（FastAPI/Starlette httpx 弃用提示，与本次无关） |

补充验证：
- 间隔 2.2 秒两次调用 `build_pptx` 字节完全相同（`equal across 2.2s: True`）。
- 用 pptx API 逐形状核对：封面满版 `0F2A47` + 两处 `C9A227`；场景满版 `F7F4EC`、标题带 `0F2A47`（1.1in）、左竖条 `C9A227`、序号 `01 / 01`；notes 文本保留。
- 全量测试第一次运行时 `test_ledger.py::test_concurrent_debit_never_negative` 因 SQLite `database is locked` 失败（共享记忆中已记录的既有 flake），重跑全量 249 全绿。

## 3. 变更文件

- `app/services/export.py`（重写，199 行）
- `tests/test_export.py`（+2 测试，改造 1 测试）
- `docs/ppt-beautify-report.md`（本报告）

## 4. 关注点 / 已知限制

- 场景 `type=quiz` 暂用通用版式（设计 §3.2 标注为可选，未做专门变体）。
- 正文自适应上限按设计 §3.2 调整为 18pt（原实现为 20pt）；13/15pt 的收缩档位保持，长文本仍 ≤ 15pt，不溢出。
- `_save` 重写 ZIP 会重新压缩所有部件（课件约 35KB，耗时可忽略）；PowerPoint 兼容的 1980 固定时间戳无副作用。
- `SECONDARY_BLUE` 常量已按调色板定义但当前版式未使用，留待网站/卡片变体复用。
- 本次仅 PPT 部分；网站深蓝金 CSS 美化（设计 §4）未实施。
