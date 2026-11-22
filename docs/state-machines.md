# 状态机（设计 §5.3–§5.6）

## 订单（app/domain/order.py）

```
created → pending_payment → paid → fulfilled → refunding → refunded
   │            │                                  └→ (退款完成)
   └→ closed（未支付取消）   └→ closed
```

- 只允许表中定义的转移；非法转移抛 `InvalidOrderTransition`。
- 未支付订单保留 `pending_payment`：同用户同 SKU 再次下单返回同一订单并带
  `pending_order_payment_in_progress`，前端恢复「继续支付」。
- 支付回调幂等：`order_no + event_id` 唯一键；重复/乱序通知不重复发积分。

## 生成任务（app/domain/generation.py）

```
queued → running → succeeded
            │        └→ (多退少补 settle：按真实用量结算)
            └→ failed / cancelled（全额 refund）
```

- `create_job` 估算并预扣积分（reserve）后入队；worker `poll_once` 提交并回收结果。
- `complete_job` 用真实用量结算：预估多则退、少则补；同一事务写 AIGC 审计。
- `fail_job` 幂等：重放不重复退款。终态不可再次 complete/fail。

## 归因（app/domain/attribution.py, app/services/affiliate.py）

```
点击邀请码 → candidate（90 天候选，candidate_at 是唯一时钟）
   → 客户明确确认（confirmed_at） → 首笔有效套餐订单 → locked（长期唯一归属）
```

- `candidate_at` 只在候选阶段用于 90 天判定；一旦锁定，归属长期有效，
  复购佣金不受 90 天影响。
- 仅 `package` 类型订单可锁定归属；¥199 体验包锁定归属但不计现金佣金（改为送邀请人 50 积分，上限 20 人）。
- 自购/同主体拦截在 ¥199 分支之前；`SELF_REFERRAL_CHECK=off` 时仅拦截同 id 与代理互购。

## 角色层级（app/domain/identity.py）

`admin ≥ agent ≥ promoter ≥ user`：agent 满足 promoter/user，admin 满足全部。
Promoter 由套餐权益自动开通 basic/advanced；agent 由 ¥9999 准入订单履约后自动激活。
