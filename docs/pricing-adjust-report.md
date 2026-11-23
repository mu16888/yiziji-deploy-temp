# 定价与价格页调整 — 实施报告

- 日期：2026-11-23
- 设计：`docs/superpowers/specs/2026-11-23-定价调整.md`（用户已批准）
- 状态：已完成并提交，提交 `7c3e4af`
- 说明：小额档按用户最终决定为 **¥9.9 / 100 积分 / SKU `pkg_starter_9`**（设计文档验证段残留的 ¥19.9 字样未采纳）

## 一、变更内容

### 1. `scripts/seed.py`
- 新增 `pkg_starter_9`：`name="体验包"`、`type="package"`、`amount_fen=990`、`credits=100`、`sort=0`、`bonuses={}`、`eligibility={}`；已加入 `DEFAULT_SKUS`。
- 从 `PRODUCTS` 与 `DEFAULT_SKUS` 中**移除死商品** `svc_voice_clone_299`（无后端交付不了）。
- **保留** `agent_admission_9999`（真实代理功能），仅在展示层过滤，不删除。

### 2. `app/services/catalog.py`
- 新增模块级常量：
  `HIDDEN_FROM_PRICING_SKUS = frozenset({"agent_admission_9999", "svc_voice_clone_299"})`
- `list_products` 查询增加 `.filter(Product.sku.notin_(HIDDEN_FROM_PRICING_SKUS))`，因此价格页与 `/api/catalog/products` 均不再出现这两项。
- **`get_price` 完全未改**：隐藏 SKU 仍可按 sku 直接定价/购买，admin 与代理流程不受影响。

### 3. `templates/pricing.html`
- 在 `.grid` 上方新增醒目 banner（深蓝渐变 + 金色左边框，沿用 #0F2A47 / #1B3E5C / #C9A227 / #E8D08D / #F7F4EC 配色）：
  `🎉 新用户注册即送 100 积分（约可生成 10 套课件），先免费体验再决定 ✨`
- 商品循环保持原样，数据来自后端过滤后的 `list_products`；模板无任何价格字面量（原有守护测试仍通过）。

### 4. `templates/library.html`（我的资产）
- 「专属音色」卡片的 `<a href="/pricing">了解音色复刻</a>` 改为 `<button disabled title="功能筹备中">敬请期待</button>`；其余三张卡片与 `data-empty` 脚本未动。

### 5. `scripts/update_catalog.py`（新增，幂等迁移）
- `update_catalog(db) -> list[str]`：
  - 缺 `pkg_starter_9` 则按 seed 数据新增；
  - `svc_voice_clone_299` 存在且 active 则置 `active=False`（**软删除，保留行**）；已 inactive 或不存在则为 no-op；
  - agent SKU 不动（由代码过滤隐藏）；
  - `db.commit()` 后返回人类可读的动作列表。
- CLI：`python scripts/update_catalog.py`，支持 `--database-url`（默认 `DATABASE_URL`），`create_all` 兜底，逐行打印动作与汇总。

## 二、测试与结果

采用 TDD：先写测试并确认 RED（6 failed / 1 passed 预期守卫），再实现，GREEN（7 passed）。

新增/更新测试：
- `test_starter_sku_seeded`：seed 中新 SKU 的名称/类型/990 fen/100 credits/sort 0/空 bonuses/active。
- `test_hidden_skus_not_listed`：`list_products` 排除 agent 与 voice（含手动补回旧库中 voice 行的情形），正常套餐含 `pkg_starter_9` 仍列出。
- `test_hidden_skus_still_purchasable_by_sku`：`get_price` 对隐藏 SKU 仍返回正确价格（约束守卫；未改 `get_price`）。
- `test_pricing_page_shows_free_banner`：GET `/pricing` 含 `100 积分` 与 `免费`。
- `test_update_catalog_adds_starter_and_deactivates_voice`、`test_update_catalog_is_idempotent`：迁移新增/软删/幂等。
- `test_list_products_hides_inactive_products`：更新为使用 `pkg_starter_9`（voice 已不再 seed），保留“inactive 不列出”的原有覆盖。

结果：
- 基线：`249 passed`（改动前实测）。
- 新增 6 个测试 → 预期 `255`。
- 全套件（提交后重跑，最终证据）：**`255 passed, 1 warning in 27.40s`**，唯一 warning 为既有的 StarletteDeprecationWarning。
- 迁移脚本 CLI 在 `app.db` 副本上连跑两次：第一次 `added SKU pkg_starter_9` + `deactivated retired SKU svc_voice_clone_299`；第二次全为 `already present` / `already inactive` no-op → 幂等性实测通过。
- 工作树干净，提交后无残留改动。

## 三、变更文件

| 文件 | 变更 |
|---|---|
| `scripts/seed.py` | 新增 `pkg_starter_9`、移除 `svc_voice_clone_299`、同步 `DEFAULT_SKUS` |
| `app/services/catalog.py` | `HIDDEN_FROM_PRICING_SKUS` + `list_products` 过滤 |
| `templates/pricing.html` | 免费积分 banner + 深蓝金样式 |
| `templates/library.html` | 音色复刻按钮改「敬请期待」disabled |
| `scripts/update_catalog.py` | 新增幂等迁移脚本 + CLI |
| `tests/test_catalog.py` | 3 个新测试 + 更新 inactive 覆盖 |
| `tests/test_models.py` | `test_starter_sku_seeded` |
| `tests/test_update_catalog.py` | 新增迁移/幂等测试 |
| `docs/pricing-adjust-report.md` | 本报告 |

提交：`7c3e4af feat: add ¥9.9 entry SKU, hide dead/misplaced SKUs, show free credits`（8 files changed, 301 insertions(+), 15 deletions(-)；报告未包含在提交内）。

## 四、关注点 / 后续

1. **服务器必须执行一次迁移**：`python scripts/update_catalog.py`（或 `--database-url ...`）。旧库若未执行，仍缺 `pkg_starter_9`（价格页虽已隐藏 voice，但数据未软删）。
2. 价格页实际档位为 **¥9.9 / ¥199 / ¥699 / ¥1999 / ¥3999**；设计文档验证段仍写 ¥19.9，属文档残留，按用户最终决定实现。
3. agent SKU 保持 active，仅靠 `HIDDEN_FROM_PRICING_SKUS` 在 `list_products` 层隐藏；若未来新增直接查 `Product` 表的列表/接口，需同样过滤，或改为 `show_in_pricing` 字段方案。
4. 「敬请期待」按钮无任何后端接线，仅为避免误导；若后续真做音色复刻，需补实现并恢复入口。
5. 本环境无桌面浏览器接入，banner 仅做静态样式检查，未做实机截图；自动化测试断言的是文本内容而非视觉。
6. Windows/PowerShell 终端把中文输出显示为乱码（控制台编码所致），源文件均为 UTF-8，测试断言基于真实 Unicode 字符串，全套件通过。
