# 合规清单（设计 §3、§5.5、§11、§12.7）

## AIGC 标识与审计

- [x] `AIGC_LABEL=true`（默认）：生成结果/导出件写入元数据标识（隐式标记仿 `moov.udta.meta`，
      显式标记在结果 `meta.label`）；学生页展示「本内容由 AI 生成」提示。
- [x] `mark_aigc` 仅做元数据打标（无视频二进制处理），幂等；`AIGC_LABEL=false` 时不打标。
- [x] `AUDIT_RETENTION_DAYS=190`：审计日志保留 190 天；`scripts/purge_logs.py` 定时清理。
- [x] AigcAudit 只记 AIGC 打标事件；资金/管理操作写既有 AuditLog，不重复建账。
- [x] `complete_job` 在结算同一事务记 `aigc:generate` 审计。

## 资金与反套利

- [x] 金额一律整数分，价格服务端权威，前端无价格字面量。
- [x] 账本不可变、`balance >= 0`、并发不透支；每张资金表唯一 `idempotency_key`。
- [x] 支付回调验签 + `order_no + event_id` 幂等；严格校验实付 = 服务端价。
- [x] `SELF_REFERRAL_CHECK` 开发默认 `off`；生产应设 `identity`/`org`/`both`，
      用 `identity_hash`（不存明文）与 `org_id` 拦截同主体套利。
- [x] 不做多级分销（仅一层团队差额）、代理货款不可提现、提现需审核不打款。

## 第三方组件与背书

- [x] 生成链路是**自有编排**：提示词、场景重试/降级、Courseware schema 与校验均在本仓库内，
      不依赖任何外部开源生成引擎，也没有需要随产品再分发的第三方生成子包。
- [x] 底层 LLM 为可替换执行器（默认 DeepSeek），key 由使用者自行提供，仅从环境变量读取；
      使用前须遵守所选厂商的服务条款、内容政策与数据合规要求。
- [x] 不使用任何高校名义或标识为产品背书，不暗示与任何高校存在官方关联。

## 链接与数据

- [x] `SHARE_LINK_TTL_DAYS=7`：学生分享链接 7 天有效，过期返回 410。
- [x] 学生页免登录，仅靠分享码 + TTL 校验。
- [x] `.env` 已被 `.gitignore` 忽略；密钥不入库。
