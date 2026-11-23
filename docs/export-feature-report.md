# PPTX 导出功能实施报告（2026-11-23）

## 实现

- `pyproject.toml`：新增 `python-pptx` 依赖；已在 `.venv-sdd` 安装 `python-pptx 1.0.2`（附带 `Pillow`、`XlsxWriter`）。
- `app/services/export.py`（纯函数，无 FastAPI/DB）：`build_pptx(courseware: dict) -> bytes`
  - 显式固定 4:3：`slide_width=Inches(10)`、`slide_height=Inches(7.5)`（不依赖库默认值）。
  - 第 1 页封面：标题、学科、年级、AIGC 标识（`AI辅助生成`）。
  - 每个场景一页：标题作标题占位符，bullets 进正文占位符（项目符号列表），`narration` 写入 `slide.notes_slide.notes_text_frame.text` 演讲备注。
  - 空字段跳过；无场景时仍可生成只有封面的课件。`BytesIO` → `bytes`。
- `app/api/export.py`：`GET /api/export/{job_id}.pptx`
  - 登录必需（`get_current_user`）；仅作业所有者或 `admin`（`role_satisfies(role, "admin")`）可导出，其他人 403。
  - 未知作业 404；无 result / 显式失败结果 409；有结果但无场景（如 mock adapter）返回只有封面的 PPTX。
  - 返回 `Response(content=bytes, media_type=...presentationml.presentation, headers={"Content-Disposition": 'attachment; filename="courseware.pptx"'})`。
  - **不写入账本、不扣积分、不需要 commit。**
- `app/main.py`：注册 `export_router`。
- `templates/classroom_edit.html`：场景预览的操作区新增「下载 PPT」链接 → `/api/export/{{ job.id }}.pptx`。
- `templates/student_classroom.html`：未添加。学生页免登录、无作业归属上下文，而导出要求登录且仅本人/管理员可用；在那里放链接会指向一个学生必然拿不到 403 的地址，故按可选项跳过。

## 计价决定（产品负责人更正）

原设计曾写「每次导出扣 5 积分」。产品负责人 2026-11-23 更正：**导出免费**——生成时已按课件类型扣费（basic 10 / interactive 25 / PBL 40），导出属于交付的一部分，再扣费是重复计费且体验差。因此最终实现中**没有任何 `append_entry` / 积分流水 / 幂等扣费逻辑**；测试 `test_export_does_not_charge` 断言两次下载后账本行数与余额均不变。设计文档 `E:/OpenCode工作台/软件开发/docs/superpowers/specs/2026-11-22-PPTX导出设计.md` 的关键决定、实现范围与测试项已同步更正。

## 测试与结果

`tests/test_export.py`（8 项）：4:3 尺寸、封面/场景/备注内容、空字段容错、两次下载不扣费且内容一致、非所有者 403/管理员 200、无课件 409、mock 结果仅封面、编辑页下载链接。

- 基线：`python -m pytest -q` → **233 passed**（改动前）。
- RED：`python -m pytest tests/test_export.py -q` → `ModuleNotFoundError: No module named 'app.services.export'`，exit code 1（1 error during collection）。
- GREEN：`python -m pytest tests/test_export.py -q` → **6 passed**（首次实现；后续补到 8 项）。
- 全量：`python -m pytest -q` → **241 passed, 1 warning in 21.58s**（233 基线 + 8 新增；唯一警告是既存 Starlette/httpx 弃用提示）。

所有命令用 `E:\OpenCode工作台\软件开发\.venv-sdd\Scripts\python.exe`，工作目录为 `ai-monetization-template`。测试全程无真实 AI/API 调用、无密钥。

## 改动文件

- `pyproject.toml`（+1 依赖）
- `app/services/export.py`（新增）
- `app/api/export.py`（新增）
- `app/main.py`（+2 行注册）
- `templates/classroom_edit.html`（+1 链接）
- `tests/test_export.py`（新增）
- `docs/export-feature-report.md`（本报告）
- 仓库外设计文档（同步计价更正，见上）

## 自查与关注点

- ✅ 4:3 尺寸显式固定且有精确断言；✅ 免费导出无账本写入；✅ 所有者/管理员鉴权；✅ 演讲备注逐字断言。
- 关注 1：`MockGenerationAdapter.estimate()` 仍保留既有的 `type=export/format=pptx → 5` 计价分支（旧 §12.6 规则，被既有测试守护）。**新下载接口完全不经过它**，不构成重复收费；若以后要清理旧「导出任务」计价，需单独评估并改测试。
- 关注 2：Hindsight 服务在本机不可达（`ECONNREFUSED`），`list_pages` / `capture_initiative` 均失败；本功能未能在 Hindsight 建页跟踪，改由本报告与共享记忆承担记录。
- 关注 3：学生页下载按钮按要求属可选项，因鉴权冲突未添加（理由见上）。
