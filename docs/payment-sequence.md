# 支付与计费时序（设计 §5.4、§7）

```
Client            API           PaymentGW        Worker        AIAdapter
  |--estimate----->|                 |               |             |
  |<-预计积分-------|                 |               |             |
  |--submit------->|--reserve(预扣)---|               |             |
  |                |--create job-------------------->|             |
  |<-job_id--------|                 |               |--submit---->|
  |                |                 |               |<-external---|
  |--pay order---->|--create payment>|               |             |
  |<-pay params----|<----------------|               |             |
  |--(支付)--------|---------------->|---webhook---->|             |
  |                |<--verify/sync---|               |             |
  |                |--fulfilled(发积分/权益/佣金)    |             |
  |                |                 |               |<--done/cb---|
  |                |--settle/退款差额-----------------|             |
  |<-通知完成-----|                 |               |             |
```

## 关键 API

| 路径 | 作用 |
|---|---|
| `POST /api/pay/orders {sku, channel}` | 服务端权威定价下单，返回 `order_no` / `amount_fen` / `pay_params` |
| `POST /api/pay/native_pay` / `/jsapi_pay` | 调网关适配器生成支付参数 |
| `POST /api/pay/sync` | webhook / 前端回调：**先验签**，再幂等确认 |
| `GET /api/pay/orders/{no}` | 查询订单与网关状态 |
| `POST /api/pay/cancel` | 关闭未支付订单 |

## 幂等与事务

- webhook 必须验签；回调签名与 `signature` 字段在 `canonical_json` 时剔除，签名可复现。
- `confirm_payment` 在**一个事务**内：校验服务端价格 → 发 purchase 积分 → 应用权益 →
  调用佣金钩子 → 写 `order_events`。`order_no + event_id` 唯一键 + 确定性幂等键
  （`event:{no}:...` / `purchase:{no}`）保证重放安全。
- `paid_amount_fen` 必须是整数且严格等于服务端价格，否则 400 且订单留 `pending_payment`，不发积分。
- 退款：反向冲回佣金 → 回收权益（补偿审计）→ 按 FIFO lot 退未消费积分 → 写 `refund_completed`；
  不记负余额、不冻结账户。

## 网关适配器（可替换）

`PaymentGateway` 协议四个操作：`create_payment` / `verify_callback` / `query` / `refund`。
`mock` 与 `wechat` 均实现该协议；`PAYMENT_GATEWAY` 切换。微信适配器为占位实现，需真实商户凭证。
