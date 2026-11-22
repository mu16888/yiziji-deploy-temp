# 设计包索引

本目录是模板的设计包。完整叙述与逐任务实施步骤在 `superpowers/` 下；下面 5 份是按主题拆出的速查文档，内容均源自设计文档（§1–§14），便于开发时定位。

| 主题 | 文件 | 设计文档对应章节 |
|---|---|---|
| 数据表与金额约定 | [data-model.md](data-model.md) | §6、§5.2、§5.6 |
| 状态机 | [state-machines.md](state-machines.md) | §5.3、§5.4、§5.5、§5.6 |
| 支付与计费时序 | [payment-sequence.md](payment-sequence.md) | §5.2、§5.4、§7 |
| 佣金公式 | [commission-formulas.md](commission-formulas.md) | §5.6、§11 |
| 合规清单 | [compliance-checklist.md](compliance-checklist.md) | §3、§5.5、§11、§12.7 |

- 设计全文：`superpowers/specs/2026-11-22-ai-tool-monetization-template-design.md`
- 实施计划：`superpowers/plans/2026-11-22-ai-monetization-template.md`

全局约束（适用于所有主题）：金额一律整数分、价格服务端权威、资金操作在事务内、
资金表带唯一 `idempotency_key`、账本不变量 `balance >= 0`、环境变量名逐字使用。
