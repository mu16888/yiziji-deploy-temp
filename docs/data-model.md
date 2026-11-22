# 数据表与金额约定（设计 §6）

## 核心表

| 表 | 用途 | 关键字段 |
|---|---|---|
| `users` | 身份与角色 | `phone`、`role`(user/promoter/agent/admin)、`invite_code`、`identity_hash`、`org_id` |
| `user_roles` | 角色定义 | `name`、`description` |
| `credit_ledger` | 不可变积分流水 | `user_id`、`delta`、`balance_after`、`type`、`ref_type`、`ref_id`、`idempotency_key`、`expires_at`、`pool` |
| `products` | 商品/价格规则 | `sku`、`type`、`amount_fen`、`credits`、`bonuses`(json)、`eligibility`(json)、`sort` |
| `orders` | 订单 | `no`、`user_id`、`sku`、`amount_fen`、`status`、`paid_amount_fen`、`refund_status` |
| `order_events` | 回调/状态事件 | `order_no`、`event_id`、`event_type`、`payload`、`idempotency_key`(唯一) |
| `generation_jobs` | 生成任务 | `user_id`、`spec`、`status`、`reserved_credits`、`actual_credits`、`result` |
| `invitations` / `attributions` | 邀请码 / 90 天候选归属 | `code`、`user_id`；`candidate_at`、`confirmed_at`、`locked_at` |
| `promoters` / `agents` | 推广员 / 代理 | `level`、`invited_count`；`status`、`fund_fen` |
| `agent_fund_ledger` / `commission_ledger` | 代理货款 / 佣金流水 | `delta_fen`、`available_at`、`idempotency_key` |
| `withdrawals` | 提现申请 | `amount_fen`、`status`、`reviewed_at` |
| `audit_logs` / `aigc_audit` | 管理审计 / AIGC 审计 | `action`、`payload`、`actor_id`、`aigc_label` |

## 约定

- **所有金额用「分」（int）**，不用浮点。费率用整数百分比，整数向下取整。
- 每张资金表带**唯一且非空**的 `idempotency_key` 与 `created_at`。
- 积分流水 `type`：`grant` / `purchase` / `bonus` / `consume` / `settle` / `refund` / `expire` / `adjust`。
- 积分池 `pool`：`universal`（通用）/ `dedicated`（专属批次）。
- 余额 = 流水汇总；条件 UPDATE（`balance >= -delta`）保证并发不透支，不记负余额、不冻结账户。
- 默认 SQLite（`DATABASE_URL=sqlite:///./app.db`），切 `postgresql://...` 即迁移到 Postgres；
  Alembic 初始迁移为 `0001_initial`，`alembic check` 无漂移。
