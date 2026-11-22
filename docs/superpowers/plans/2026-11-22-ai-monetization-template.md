# 通用 AI 工具变现模板（AI 互动课堂工作台） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付一个可 fork 的 AI 工具变现代码脚手架，内置积分账本/订单支付/生成任务/推广代理/管理后台，并带一个面向老师的 AI 互动课堂 demo（个人版 + 机构版付费）。

**Architecture:** Python FastAPI 作为变现内核，领域逻辑（账本、订单状态机、归因、佣金）写成不依赖框架的纯函数；外部能力（支付、AI 生成、身份、通知）通过适配器接口注入，默认 mock 实现，本地零依赖可跑通全链路。前端为 Jinja2 服务端渲染 + 原生 JS；数据默认 SQLite，生产可切 Postgres。

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0, Alembic, pytest, Jinja2, uvicorn, nginx(可选)

**Spec:** `docs/superpowers/specs/2026-11-22-ai-tool-monetization-template-design.md`

---

## Global Constraints

- Python ≥ 3.11；依赖写入 `pyproject.toml`，不引入构建步骤的前端工具链。
- 默认 `DATABASE_URL=sqlite:///./app.db`；设置 `DATABASE_URL` 为 `postgresql://...` 时切 Postgres。
- 所有金额用**分**（int）存储与计算，禁止浮点；积分用整数。
- 所有资金操作必须在数据库事务内完成；`app/domain/**` 的函数不得 import fastapi 或读取 Request。
- 所有资金表带 `idempotency_key` 唯一约束；所有回调/结算入口强制幂等键。
- 账本不变量：`balance >= 0`；禁止记负余额或冻结账户。
- 价格只能由服务端计算；前端只展示 `amount_yuan / credits / 可购买`。
- 环境变量名逐字使用：`DATABASE_URL`、`SECRET_KEY`、`PAYMENT_GATEWAY=mock|wechat`、`GENERATION_ADAPTER=mock|openmaic_http`、`CREDIT_EXPIRE_DAYS=730`、`RENEW_MODE=all_active|new_only`、`REFUND_MODE=unused_only|full_manual`、`COMMISSION_OBSERVATION_DAYS=15`、`MIN_WITHDRAW_FEN=10000`、`SELF_REFERRAL_CHECK=off|identity|org|both`、`AIGC_LABEL=true`、`AUDIT_RETENTION_DAYS=190`、`SHARE_LINK_TTL_DAYS=7`、`VERIFY_CODE_DEV=123456`、`OPENMAIC_URL`、`WORKER_POLL_SEC=5`。
- AIGC 合规默认开启（`AIGC_LABEL=true`），审计日志保留 190 天。
- 不实现小程序虚拟支付、不做多租户、不做订阅制计费、不绑定任何模型厂商。

## Review Focus

- **重复支付回调**：同一 `event_id` 的通知到达两次，只能发一次积分 —— 在 T6 加 `test_duplicate_callback_is_idempotent`。
- **并发超额扣减**：两个任务同时扣同一余额，第二个必须失败而非变负 —— 在 T4 加 `test_concurrent_debit_never_negative`。
- **已消费积分后退款**：只退未消费部分，已消费部分转人工核价，余额不得为负 —— 在 T4 加 `test_refund_after_spend_never_negative`。
- **自购/同主体套利**：用户用自己邀请码或同 `org_id` 账号下单，不产生佣金 —— 在 T8 加 `test_self_referral_no_commission`。
- **续期与过期交互**：充值时把所有未过期批次顺延，已过期批次不动 —— 在 T4 加 `test_renew_extends_active_lots_only`。
- **分享链接过期**：过期的学生课堂链接不可访问，未过期的免登录可访问 —— 在 T10 加 `test_share_link_expiry`。

---

## File Structure

```
ai-monetization-template/
├── pyproject.toml, .env.example, docker-compose.yml, Dockerfile, nginx.conf
├── alembic.ini, alembic/versions/
├── app/
│   ├── main.py                      # FastAPI app, include routers, /health
│   ├── config.py                    # env settings (pydantic-settings)
│   ├── db.py                        # SessionLocal, Base, get_db
│   ├── models/                      # SQLAlchemy models per table
│   ├── domain/                      # PURE logic: ledger, order, attribution, commission, generation
│   ├── services/                    # use-case orchestration: auth, credits, catalog, orders, affiliate, admin, compliance
│   ├── adapters/
│   │   ├── identity/{password,wechat,mock}.py
│   │   ├── payment/{mock,wechat}.py
│   │   └── generation/{mock,openmaic_http}.py
│   ├── api/{auth,credits,catalog,pay,jobs,promote,admin,pages}.py
│   ├── workers/worker.py            # DB-backed job poller
│   └── middleware/csrf.py
├── templates/{index,login,onboarding,workbench,generate,job,classroom_edit,share,library,history,promote,pricing,org,student_classroom}.html + admin/
├── static/app.js, static/app.css
├── scripts/{seed.py,purge_logs.py}
├── docs/ (design package copied from spec)
└── tests/ (mirror app structure)
```

---

## Task 1: 项目骨架、配置与健康检查

**Files:**
- Create: `ai-monetization-template/pyproject.toml`, `app/main.py`, `app/config.py`, `app/db.py`, `.env.example`, `tests/test_health.py`

**Interfaces:**
- Produces: `app.config.settings`（读取 Global Constraints 中所有环境变量）；`app.db.Base`, `SessionLocal`, `get_db()`；`app.main.app`（FastAPI 实例，`/health` 返回 `{"status":"ok"}`）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_health.py
from fastapi.testclient import TestClient
from app.main import app

def test_health():
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-monetization-template && pytest tests/test_health.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现骨架**

创建 `pyproject.toml`（依赖 `fastapi`, `uvicorn[standard]`, `sqlalchemy>=2`, `pydantic-settings`, `alembic`, `pytest`, `httpx`, `jinja2`, `python-multipart`）；`app/config.py` 用 pydantic-settings 声明全部环境变量及默认值；`app/db.py` 用 `create_engine(settings.database_url)`；`app/main.py` 创建 app 并注册 `/health`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git init && git add . && git commit -m "feat: project skeleton and health check"
```

---

## Task 2: 数据模型、迁移与种子数据

**Files:**
- Create: `app/models/*.py`, `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_initial.py`, `scripts/seed.py`, `tests/test_models.py`

**Interfaces:**
- Consumes: `app.db.Base`
- Produces: 全部 ORM 模型（`User`, `Role`, `CreditEntry`, `CreditBalance`, `Product`, `Order`, `OrderEvent`, `GenerationJob`, `Invitation`, `Attribution`, `Promoter`, `Agent`, `AgentFundEntry`, `CommissionEntry`, `Withdrawal`, `AuditLog`, `AigcAudit`）；`scripts/seed.py: seed(db) -> None` 写入默认商品/角色/演示账号

- [ ] **Step 1: 写失败测试**

```python
# tests/test_models.py
from app.models import User, Product, CreditEntry

def test_required_tables_and_columns():
    assert User.__tablename__ == "users"
    assert "identity_hash" in User.__table__.columns
    assert "org_id" in User.__table__.columns
    assert "idempotency_key" in CreditEntry.__table__.columns
    assert CreditEntry.__table__.columns["idempotency_key"].unique
    assert "amount_fen" in Product.__table__.columns
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_models.py -v`
Expected: FAIL

- [ ] **Step 3: 实现模型与迁移**

按 §6 建表；金额列 `Integer`（分）；每张资金表 `idempotency_key = Column(String, unique=True, nullable=False)`；生成 Alembic 初始迁移；`seed()` 写入 §5.3 默认 SKU 与角色、演示老师/机构账号。

- [ ] **Step 4: 运行确认通过**

Run: `alembic upgrade head && python scripts/seed.py && pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add . && git commit -m "feat: models, migration and seed data"
```

---

## Task 3: 身份、角色与登录通路

**Files:**
- Create: `app/domain/identity.py`, `app/services/auth.py`, `app/adapters/identity/{password,wechat,mock}.py`, `app/api/auth.py`, `app/middleware/csrf.py`, `templates/login.html`, `tests/test_auth.py`

**Interfaces:**
- Consumes: `User`, `Role`, `settings`
- Produces:
  - `authenticate(db, provider: str, credential: str) -> User`
  - `issue_token(user: User) -> str`（JWT payload 含 `sub`, `channel`, `referrer_code`）
  - `get_current_user(request, db) -> User`（Cookie 优先，Bearer 兜底）
  - `require_roles(*roles)` 依赖

- [ ] **Step 1: 写失败测试**

```python
# tests/test_auth.py
def test_register_and_login_with_dev_code():
    r = client.post("/api/auth/register", json={"phone": "13800000001", "code": "123456", "referrer": "TCH-8F3K"})
    assert r.status_code == 200
    assert "token" in r.json()
    assert client.get("/api/credits/balance").status_code == 200

def test_web_cookie_and_bearer_both_work():
    # Cookie 通路与 Authorization: Bearer 通路都能取到当前用户
    ...
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_auth.py -v`
Expected: FAIL

- [ ] **Step 3: 实现身份服务**

`password` 适配器开发期接受 `VERIFY_CODE_DEV`；`wechat` 适配器留接口抛 `NotImplementedError`；mock 适配器返回固定用户。注册时 `grant` 100 积分（调用 T4 的 `append_entry`，此时用 stub 实现，T4 替换）。Web 登录写 httpOnly Cookie + CSRF token；API 接受 Bearer。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_auth.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add . && git commit -m "feat: auth, roles and dual token channels"
```

---

## Task 4: 积分账本内核（核心）

**Files:**
- Create: `app/domain/ledger.py`, `app/services/credits.py`, `app/api/credits.py`, `tests/test_ledger.py`

**Interfaces:**
- Consumes: `CreditEntry`, `CreditBalance`, `settings`
- Produces:
  - `append_entry(db, *, user_id: int, delta: int, entry_type: str, ref_type: str, ref_id: str, idempotency_key: str, pool: str = "universal", expires_at: datetime | None = None) -> CreditEntry`
  - `get_balance(db, user_id: int, pool: str | None = None) -> int`
  - `reserve(db, *, user_id, amount: int, ref_type, ref_id, idempotency_key, pool="universal") -> CreditEntry`
  - `settle(db, *, user_id, reserved_ref_id, actual_amount, idempotency_key) -> CreditEntry`
  - `refund_unused(db, *, order_no, idempotency_key) -> int`（返回退回**分**）
  - `renew_expiry(db, *, user_id, new_expiry: datetime) -> int`（返回续期批次数）

- [ ] **Step 1: 写失败测试（含 Review Focus 的 3 条）**

```python
def test_append_entry_is_immutable_and_idempotent():
    e1 = append_entry(db, user_id=1, delta=100, entry_type="grant", ref_type="signup", ref_id="1", idempotency_key="k1")
    e2 = append_entry(db, user_id=1, delta=100, entry_type="grant", ref_type="signup", ref_id="1", idempotency_key="k1")
    assert e1.id == e2.id and get_balance(db, 1) == 100

def test_concurrent_debit_never_negative():
    # 余额 100，两个 reserve(60) 并发；一个成功一个抛 InsufficientCredits
    ...

def test_refund_after_spend_never_negative():
    # grant 100 → reserve 80 → refund_unused；余额 == 20，refund 金额按未消费计算

def test_renew_extends_active_lots_only():
    # 一个未过期批次 + 一个已过期批次；renew 后只有前者 expires_at 变化
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_ledger.py -v`
Expected: FAIL

- [ ] **Step 3: 实现账本**

`append_entry` 用事务 + 唯一键冲突时返回既有记录；`reserve` 用 `SELECT ... FOR UPDATE`（SQLite 退化为事务内校验）保证不透支，余额不足抛 `InsufficientCredits`；`settle` 按 `reserved_ref_id` 生成 `settle` 流水（差额多退少补）；`refund_unused` 按订单来源批次汇总未消费额并返回分；`renew_expiry` 按 `RENEW_MODE` 处理（默认 `all_active`）。`GET /credits/balance` 与 `/credits/ledger` 暴露给前端。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_ledger.py -v`
Expected: PASS（含 4 条 Review Focus 测试）

- [ ] **Step 5: 提交**

```bash
git add . && git commit -m "feat: immutable credit ledger with reserve/settle/refund"
```

---

## Task 5: 商品目录与服务端定价

**Files:**
- Create: `app/services/catalog.py`, `app/api/catalog.py`, `templates/pricing.html`, `tests/test_catalog.py`

**Interfaces:**
- Consumes: `Product`, `User`
- Produces:
  - `list_products(db, user: User) -> list[ProductView]`
  - `get_price(db, sku: str, user: User) -> PriceQuote`（`amount_fen`, `credits`, `original_amount_fen`, `eligible`）
  - `apply_bonuses(db, user_id: int, sku: str) -> None`（发课程/音色/推广等级/机构席位权益）

- [ ] **Step 1: 写失败测试**

```python
def test_repurchase_price_is_server_side():
    u = make_user_with_order(sku="pkg_personal_699", paid_fen=69900)
    q = get_price(db, "pkg_personal_699", u)
    assert q.amount_fen == 55920 and q.original_amount_fen == 69900

def test_199_no_cash_commission_but_locks_attribution():
    ...
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_catalog.py -v`
Expected: FAIL

- [ ] **Step 3: 实现定价**

复购价等规则写在 `Product.eligibility`（JSON）与 `original_amount_fen`；`get_price` 查同 SKU 未退款历史订单决定复购资格；前端 `/pricing` 只渲染返回值。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_catalog.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add . && git commit -m "feat: catalog and server-side pricing"
```

---

## Task 6: 订单状态机与支付网关（含回调幂等）

**Files:**
- Create: `app/domain/order.py`, `app/services/orders.py`, `app/adapters/payment/{mock,wechat}.py`, `app/api/pay.py`, `tests/test_orders.py`, `tests/test_payment_callback.py`

**Interfaces:**
- Consumes: `get_price`, `Order`, `PaymentGateway`
- Produces:
  - `class PaymentGateway(Protocol)`: `create_payment(order) -> dict`, `verify_callback(payload, signature) -> str`(order_no), `query(order_no) -> str`, `refund(order_no, amount_fen) -> bool`
  - `create_order(db, *, user_id, sku, channel) -> Order`
  - `start_payment(db, order_no) -> dict`
  - `confirm_payment(db, *, order_no, event_id, paid_amount_fen) -> Order`
  - `cancel_order(db, order_no) -> Order`

- [ ] **Step 1: 写失败测试（含 Review Focus 的 1 条）**

```python
def test_order_state_machine():
    o = create_order(db, user_id=1, sku="pkg_personal_699", channel="native")
    assert o.status == "pending_payment"
    confirm_payment(db, order_no=o.no, event_id="e1", paid_amount_fen=69900)
    assert o.status == "fulfilled" and get_balance(db, 1) == 6000

def test_pending_order_recovery():
    # 同用户同 SKU 已有 pending 订单，再 create_order 返回同一订单

def test_duplicate_callback_is_idempotent():
    confirm_payment(db, order_no=o.no, event_id="e1", paid_amount_fen=69900)
    confirm_payment(db, order_no=o.no, event_id="e1", paid_amount_fen=69900)
    assert ledger_count_for_order(o.no) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_orders.py tests/test_payment_callback.py -v`
Expected: FAIL

- [ ] **Step 3: 实现订单与支付**

状态机常量与转移校验置于 `app/domain/order.py`；`confirm_payment` 用 `order_no + event_id` 唯一键幂等，成功后在一个事务内：发积分（`append_entry`）+ `apply_bonuses` + `apply_order_commission`（T8 的 stub，T8 替换）+ 写 `order_events`。mock 网关 `create_payment` 返回二维码占位数据；`wechat` 适配器实现签名校验与 JSAPI/Native 下单（无商户号时测试用 fake）。未支付订单保留 `pending_payment` 供「继续支付」。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_orders.py tests/test_payment_callback.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add . && git commit -m "feat: order state machine and payment adapters"
```

---

## Task 7: 生成任务、预扣结算与 mock AI

**Files:**
- Create: `app/domain/generation.py`, `app/adapters/generation/{mock,openmaic_http}.py`, `app/workers/worker.py`, `app/api/jobs.py`, `tests/test_generation.py`

**Interfaces:**
- Consumes: `reserve`, `settle`, `GenerationAdapter`
- Produces:
  - `class GenerationAdapter(Protocol)`: `estimate(spec: dict) -> int`, `submit(job: GenerationJob, spec: dict) -> str`, `status(external_id: str) -> str`, `cancel(external_id: str) -> None`
  - `create_job(db, *, user_id: int, spec: dict) -> GenerationJob`（内部 `estimate` → `reserve` → 入队）
  - `complete_job(db, *, job_id: int, actual_credits: int, result: dict | None) -> GenerationJob`
  - `fail_job(db, *, job_id: int) -> GenerationJob`（全额退预扣）

- [ ] **Step 1: 写失败测试**

```python
def test_job_reserve_then_settle_with_refund_difference():
    j = create_job(db, user_id=1, spec={"type": "interactive_classroom", "scenes": 12})
    assert get_balance(db, 1) == 80          # 预扣 20
    complete_job(db, job_id=j.id, actual_credits=15, result={"share_code": "S123"})
    assert get_balance(db, 1) == 85          # 退回 5

def test_failed_job_refunds_full_reservation():
    ...

def test_mock_adapter_generates_share_code():
    ...
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_generation.py -v`
Expected: FAIL

- [ ] **Step 3: 实现生成闭环**

`mock` 适配器：`estimate` 按 §12.6 规则（基础课堂 10 / 含互动 25 / PBL 40 / 导出 5），`submit` 立即返回 external_id，`status` 直接 succeeded，`result` 含 `share_code`。`openmaic_http` 适配器：通过 `OPENMAIC_URL` 调其 HTTP API，接口签名同 Protocol。`worker.py` 每 `WORKER_POLL_SEC` 轮询 queued/running 任务并调用适配器；成功后 `complete_job`、失败 `fail_job`。分享码写入 `GenerationJob.result` 并生成 `/c/{share_code}` 链接。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_generation.py -v` 并启动 worker 手动跑一次 mock 任务
Expected: PASS，且能拿到 `share_code`

- [ ] **Step 5: 提交**

```bash
git add . && git commit -m "feat: generation jobs with reserve/settle/refund and mock adapter"
```

---

## Task 8: 推广、归因与代理结算

**Files:**
- Create: `app/domain/attribution.py`, `app/domain/commission.py`, `app/services/affiliate.py`, `app/api/promote.py`, `tests/test_affiliate.py`

**Interfaces:**
- Consumes: `Order`, `User`, `settings`
- Produces:
  - `bind_referrer(db, *, user_id: int, code: str) -> Attribution`（90 天候选）
  - `confirm_attribution(db, *, user_id: int) -> Attribution`
  - `apply_order_commission(db, *, order: Order) -> list[CommissionEntry]`
  - `request_withdrawal(db, *, user_id: int, amount_fen: int) -> Withdrawal`
  - `approve_withdrawal(db, *, withdrawal_id: int, approved: bool) -> Withdrawal`
  - `activate_agent(db, *, user_id: int, payment_order_no: str) -> Agent`

- [ ] **Step 1: 写失败测试（含 Review Focus 的 1 条）**

```python
def test_attribution_locks_after_first_valid_order():
    a = bind_referrer(db, user_id=2, code="TCH-8F3K")
    confirm_attribution(db, user_id=2)
    # 199 体验包锁定归属但无现金佣金；之后 699 订单给推广员 20%

def test_channel_cap_and_team_difference():
    # 客户实付 69900，推广员 basic 首单 20%=13980；代理拿 40%-20%=13980；合计≤40%

def test_self_referral_no_commission():
    # 用自己邀请码 / 同 org_id 账号下单，apply_order_commission 返回 []
    assert ... == []

def test_observation_period_and_withdrawal_minimum():
    ...
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_affiliate.py -v`
Expected: FAIL

- [ ] **Step 3: 实现分销**

`attribution.py` 实现 90 天候选 + 确认锁定 + 防抢客；`commission.py` 按 §5.6 公式计算（基数=客户实付，推广员优先，代理取 40% 差额，代开订单不计佣），写入 `commission_ledger` 并带 `available_at = paid_at + 15 天`；代理准入订单履约后转 `agent_fund_ledger`（不可提现）；提现校验 `MIN_WITHDRAW_FEN=10000` 与观察期，写 `withdrawals` 待审核。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_affiliate.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add . && git commit -m "feat: attribution, promoter and agent commission engine"
```

---

## Task 9: 管理后台

**Files:**
- Create: `app/api/admin.py`, `templates/admin/*.html`, `tests/test_admin.py`

**Interfaces:**
- Consumes: 全部 services
- Produces: `require_roles("admin")` 保护的管理端点：商品配置、订单同步/退款审批、积分调整（需 reason）、代理审核、佣金/提现审核、统计看板

- [ ] **Step 1: 写失败测试**

```python
def test_non_admin_forbidden():
    assert client.get("/admin/orders").status_code == 403

def test_manual_credit_adjustment_requires_reason_and_is_audited():
    r = client.post("/admin/credits/adjust", json={"user_id": 1, "delta": 50, "reason": ""})
    assert r.status_code == 422
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_admin.py -v`
Expected: FAIL

- [ ] **Step 3: 实现后台**

每个写操作写 `audit_logs`（操作人、动作、前后值、时间）；退款审批调用 `refund_unused` 与佣金冲回；统计看板返回注册数/付费额/积分消耗/待结算佣金。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_admin.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add . && git commit -m "feat: admin console with audit logging"
```

---

## Task 10: 产品页面与学生课堂分享

**Files:**
- Create: `templates/*.html`（§12.3 的 15 个页面）, `static/app.js`, `static/app.css`, `app/api/pages.py`, `tests/test_pages.py`

**Interfaces:**
- Consumes: `list_products`, `create_job`, `get_balance`, `bind_referrer`
- Produces: 页面路由；`GET /c/{share_code}` 免登录渲染学生课堂

- [ ] **Step 1: 写失败测试（含 Review Focus 的 1 条）**

```python
def test_share_link_expiry():
    # 未过期链接 200；把 expires_at 设为过去 → 404/410
    ...

def test_student_classroom_needs_no_login():
    assert client.get("/c/S123").status_code == 200

def test_teacher_pages_require_login():
    assert client.get("/workbench", follow_redirects=False).status_code in (302, 401)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_pages.py -v`
Expected: FAIL

- [ ] **Step 3: 实现页面**

按 §12.5 线框图实现；`/generate` 显示服务端 `estimate` 结果并提交；`/promote` 显示邀请码、佣金、提现、代理入口；`/org` 显示席位与共享额度；学生页读 `GenerationJob.result.share_code` 与 `SHARE_LINK_TTL_DAYS` 校验有效期；所有页面使用 Jinja2 模板继承，无前端构建。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_pages.py -v` 并启动服务用浏览器逐页点通
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add . && git commit -m "feat: product pages and student share link"
```

---

## Task 11: AIGC 合规与审计

**Files:**
- Create: `app/services/compliance.py`, `app/models/aigc_audit.py`(已在 T2 建表，此处实现服务), `scripts/purge_logs.py`, `tests/test_compliance.py`

**Interfaces:**
- Consumes: `GenerationJob`, `settings`
- Produces: `mark_aigc(result: dict) -> dict`（写入标识元数据）、`record_audit(db, *, actor_id, action, payload) -> AigcAudit`、`purge_expired(db) -> int`

- [ ] **Step 1: 写失败测试**

```python
def test_export_carries_aigc_label():
    out = mark_aigc({"format": "pptx"})
    assert out["aigc_label"] is True and "AIGC" in out["meta"]

def test_audit_retention():
    # 写入过期日志，purge_expired 只删超过 AUDIT_RETENTION_DAYS 的
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_compliance.py -v`
Expected: FAIL

- [ ] **Step 3: 实现合规**

`AIGC_LABEL=true` 时在生成结果与导出元数据写入标识；所有资金/管理操作写审计；`purge_logs.py` 按 `AUDIT_RETENTION_DAYS=190` 清理。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_compliance.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add . && git commit -m "feat: AIGC labeling and audit retention"
```

---

## Task 12: 部署、文档与端到端验收

**Files:**
- Create: `README.md`, `Dockerfile`, `nginx.conf`, `docker-compose.yml`, `docs/*.md`, `tests/test_e2e.py`

**Interfaces:**
- Consumes: 全部任务
- Produces: 可运行部署；README 含「5 分钟本地跑通 / 换模型 / 换支付 / 部署 / 合规注意事项（OpenMAIC MIT、LGPL 子包、清华名义）」

- [ ] **Step 1: 写失败测试**

```python
def test_e2e_full_loop():
    # 注册→建档→备课生成→mock支付→积分到账→继续生成→分享学生→邀请同事→佣金到账
    ...
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_e2e.py -v`
Expected: FAIL

- [ ] **Step 3: 实现部署与文档**

写 Dockerfile / nginx.conf / docker-compose；README 逐项说明；从 spec 复制设计包到 `docs/`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_e2e.py -v && docker compose up --build -d && curl -s localhost/health`
Expected: PASS + `{"status":"ok"}`

- [ ] **Step 5: 提交**

```bash
git add . && git commit -m "feat: deployment, docs and end-to-end acceptance"
```

---

## Self-Review（写完后已核对）

- **Spec coverage**：§1–§14 全部映射到 T1–T12；产品页面、计量、OpenMAIC 集成、合规、任务落地映射均有归属。
- **Review Focus**：6 条输入/失败模式均已加到对应任务的测试（T4×3、T6×1、T8×1、T10×1）。
- **Type consistency**：`append_entry/reserve/settle/refund_unused`、`create_order/confirm_payment`、`create_job/complete_job`、`bind_referrer/apply_order_commission` 等签名在生产者/消费者任务间一致。
- **Proportion**：计划以签名+测试断言为主，无大段实现代码。
