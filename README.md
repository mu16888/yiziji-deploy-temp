# 通用 AI 工具变现模板（AI 互动课堂工作台）

一套可 fork 复用的 **AI 工具变现代码脚手架**：FastAPI 变现内核 + Jinja2 服务端页面 + 默认 SQLite / 可切 PostgreSQL。内置积分账本、订单支付、按量生成、推广代理、管理后台与 AIGC 合规，自带 **mock 支付 / mock 生成适配器**，本地零外部依赖即可跑通全链路。

Demo 垂直场景是面向老师的「AI 互动课堂工作台」：备课 → 生成互动课件 → 分享学生上课。变现采用个人版 ¥199/699/1999 引流 + 机构版 ¥3999 收割；学生/家长免费。

> 设计文档与实施计划见 [`docs/superpowers/`](docs/superpowers/)（设计包索引见 [`docs/README.md`](docs/README.md)）。

---

## 1. 5 分钟本地跑通

前置：Python ≥ 3.11（无需 Node / Redis / 微信商户号）。

```bash
# 1) 建虚拟环境并装依赖
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e .

# 2) 配置（可选；全部有可跑通的默认值）
copy .env.example .env        # macOS/Linux: cp .env.example .env

# 3) 初始化数据库：迁移 → 幂等播种（顺序固定，不可颠倒）
alembic upgrade head && python scripts/seed.py

# 4) 起服务（终端 A）
uvicorn app.main:app --reload --port 8000

# 5) 起生成 worker（终端 B）；也可只跑一次：python -m app.workers.worker --once
python -m app.workers.worker

# 6) 验收（终端 C）
curl -s localhost:8000/health        # → {"status":"ok"}
```

浏览器打开 `http://localhost:8000/` 看落地页，`/pricing` 看服务端定价，`/login` 用手机号 + 开发验证码 `123456` 登录。

**为什么是 `alembic upgrade head && python scripts/seed.py` 这个顺序？**
迁移负责建表与结构，seed 只写默认商品/角色/演示账号。`scripts/seed.py` 里的
`Base.metadata.create_all(bind=engine)` 在表已存在时是 **no-op**（T2 裁决），
它不会覆盖或改写 Alembic 迁移出来的结构；反过来先 seed 再迁移则会出现结构漂移。
这条顺序同样写在容器启动命令里。

跑测试：

```bash
pytest -q                      # 全量（155+）
pytest tests/test_e2e.py -v    # 端到端全链路验收
```

---

## 2. 端到端链路（本模板验证过的闭环）

`tests/test_e2e.py::test_e2e_full_loop` 覆盖设计 §12.4 的完整用户流程：

```
注册(送100积分) → 建档(学科/年级档案) → 备课生成(估算→预扣→worker结算)
→ mock 支付(下单→拉参数→验签回调) → 积分到账(+6000、开通 basic 推广员)
→ 继续生成 → 分享学生(免登录链接、AIGC 标注) → 邀请同事(绑归属+确认+付费)
→ 佣金到账(basic 首单 20% → 13980 分，进 15 天观察期)
```

关键不变量在测试里逐笔断言：金额全是**整数分**、价格由服务端计算、回调幂等、
积分不透支、佣金税前应计且观察期内不可提现。

---

## 3. 配置（全部环境变量）

复制 `.env.example` 为 `.env` 后按需修改。所有配置项也都在 `app/config.py` 里有相同默认值。

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./app.db` | 换成 `postgresql://user:pwd@host:5432/db` 即切 PostgreSQL |
| `SECRET_KEY` | `dev-secret-change-me` | JWT HS256 签名密钥；**生产必须改** |
| `PAYMENT_GATEWAY` | `mock` | `mock` 或 `wechat`（见 §5） |
| `GENERATION_ADAPTER` | `mock` | `mock` 或 `openmaic_http`（见 §4） |
| `OPENMAIC_URL` | 空 | OpenMAIC HTTP 服务根地址，`openmaic_http` 适配器用 |
| `CREDIT_EXPIRE_DAYS` | `730` | 积分有效期（默认 2 年） |
| `RENEW_MODE` | `all_active` | 充值续期语义：`all_active`（全部未过期批次顺延）/ `new_only` |
| `REFUND_MODE` | `unused_only` | 退款只退未消费部分；`full_manual` 转人工核价 |
| `COMMISSION_OBSERVATION_DAYS` | `15` | 佣金观察期（天） |
| `MIN_WITHDRAW_FEN` | `10000` | 最低提现额（分，即 ¥100） |
| `SELF_REFERRAL_CHECK` | `off` | 防自购：`off` / `identity` / `org` / `both`。**开发默认 off** |
| `AIGC_LABEL` | `true` | AIGC 合规打标默认开启 |
| `AUDIT_RETENTION_DAYS` | `190` | AIGC / 审计日志保留天数 |
| `SHARE_LINK_TTL_DAYS` | `7` | 学生分享链接有效期（默认 **7 天**） |
| `VERIFY_CODE_DEV` | `123456` | 开发验证码；生产需接短信/邮件服务 |
| `WORKER_POLL_SEC` | `5` | worker 轮询间隔（秒） |
| `WECHAT_APP_ID` | `wx-fake-app-id` | 微信支付 AppID（占位） |
| `WECHAT_MCH_ID` | `fake-mch-id` | 微信商户号（占位） |
| `WECHAT_API_V3_KEY` | `fake-api-v3-key` | 微信 APIv3 密钥（占位） |
| `WECHAT_NOTIFY_URL` | 空 | 微信支付回调公网地址 |

---

## 4. 换成你自己的模型（GenerationAdapter）

变现内核与 AI 能力解耦：订单/账本完全不认识模型厂商。只改配置，不改核心代码。

**本地默认（mock）**：`GENERATION_ADAPTER=mock`，`MockGenerationAdapter` 按设计 §12.6 的估算表（基础 10 / 互动 25 / PBL 40 / 导出 5 积分）返回一个模拟结果与分享码，零外部依赖。

**接真实引擎（OpenMAIC HTTP）**：

```bash
GENERATION_ADAPTER=openmaic_http
OPENMAIC_URL=https://your-openmaic.example/api   # 适配器会请求 /estimate、/jobs、/status、/result
```

`app/adapters/generation/openmaic_http.py` 实现同一个 `GenerationAdapter` 协议
（`estimate` / `submit` / `status` / `cancel` / `result`）。换成你自己的模型时，
照这个协议新增一个适配器并在 `app/adapters/generation/__init__.py::get_adapter()`
里登记一个分支即可。worker 轮询、积分预扣/多退少补、失败全额退款、AIGC 标注全部复用。

> 合规提醒见 §8：OpenMAIC 以 MIT 许可发布，可商用，但再分发需保留其 LGPL/MIT 子包许可，且不得以清华/THU 名义背书。

---

## 5. 换成你自己的支付（PaymentGateway）

**本地默认（mock）**：`PAYMENT_GATEWAY=mock`。`MockGateway` 生成二维码占位数据，
`app.adapters.payment.mock.sign()` 可对回调签名，`POST /api/pay/sync` 验签后幂等确认。
支付状态仅在内存里模拟，不碰真实资金。

**接微信支付**：`PAYMENT_GATEWAY=wechat`，并填好上面的 `WECHAT_APP_ID`、
`WECHAT_MCH_ID`、`WECHAT_API_V3_KEY`、`WECHAT_NOTIFY_URL`。

> ⚠️ **微信登录与微信支付适配器目前是占位实现（stub）**：签名/HMAC 校验与 JSAPI/Native
> 下单的数据形态已具备并可测试，但**没有真实商户号、APIv3 证书/密钥与回调公网地址就无法真正收款**。
> 生产接入需由使用者提供真实凭证并补齐证书下载/平台验签/资金对账环节。模板不实现小程序虚拟支付。

支付回调幂等由 `order_no + event_id` 唯一约束保证；一个事务内发积分 + 权益 + 佣金 + 事件，重复通知不会重复加积分。**价格始终由服务端计算**（含复购价、资格价），前端只展示，客户端不参与计价。

---

## 6. 部署

### 6.1 Docker Compose（推荐，一条命令）

```bash
docker compose up --build -d
curl -s localhost/health          # → {"status":"ok"}
docker compose logs -f app worker nginx
```

`docker-compose.yml` 定义三个服务：

- **app**：Web/API。容器启动命令固定为 `alembic upgrade head && python scripts/seed.py && uvicorn ...`，先迁移后播种；SQLite 落在命名卷 `appdata` 的 `/app/data/app.db`。
- **worker**：DB-backed 生成 worker，消费 queued 任务、结算/退款积分；与 app 共用镜像和环境变量。生产可换 Celery/Redis，`GenerationAdapter` 接口不变。
- **nginx**：反向代理，`./nginx.conf` 挂载到 `conf.d/default.conf`，静态资源直出，对外只暴露 80（可用 `HOST_PORT` 改）。

对外访问 `http://localhost/`。生产务必设置 `SECRET_KEY`、真实 `DATABASE_URL`（见下）并关掉开发验证码。

### 6.2 生产建议

- **数据库**：默认 SQLite 适合单机/演示；多 worker 或并发写建议设 `DATABASE_URL=postgresql://...`。迁移同样用 `alembic upgrade head`。
- **密钥**：`SECRET_KEY` 用随机强值；微信凭证放密钥管理系统，不要提交进仓库（`.env` 已被 `.gitignore` 忽略）。
- **HTTPS**：在 nginx 前挂 TLS（或云负载均衡），并确保 `X-Forwarded-Proto` 正确透传——Web 通道的 Cookie + 双提交 CSRF 依赖它。
- **日志清理**：定时跑 `python scripts/purge_logs.py` 按 `AUDIT_RETENTION_DAYS=190` 清理过期审计。
- **静态文件**：`nginx.conf` 已从 `/var/www/static` 直出；改了 `static/` 无需重建 app 镜像。

---

## 7. 合规注意事项

- **OpenMAIC**：模板通过 HTTP 适配器对接独立部署的 OpenMAIC，不 fork 其代码。OpenMAIC 本体为 **MIT** 许可，可商用。
- **LGPL 子包**：整体再分发时须保留 OpenMAIC 的 `mathml2omml` 子包的 **LGPL-3.0** 许可（动态链接/独立模块形式分发，保留其源码获取途径与许可声明）。
- **MIT 子包**：`pptxgenjs` 为 **MIT** 许可，再分发时保留其版权与许可声明。
- **不得以清华/THU 名义背书**：不得使用「清华」「THU」「清华大学」等名义或其标识为产品做背书或暗示关联；本模板与相关高校无官方授权关系。
- **AIGC 标识**：`AIGC_LABEL=true`（默认）对生成结果与导出件做元数据打标（学生页展示「本内容由 AI 生成」类提示）；`AUDIT_RETENTION_DAYS=190` 控制审计留存，`scripts/purge_logs.py` 定期清理。
- **防自购套利**：`SELF_REFERRAL_CHECK` 开发默认 `off`；生产应设为 `identity` / `org` / `both`，配合 `identity_hash`（证件号哈希，不存明文）与 `org_id` 拦截同主体套利。
- **分享链接**：`SHARE_LINK_TTL_DAYS=7`，过期学生链接返回 410，不可访问。

---

## 8. 计费与账本不变量

- **金额一律用「分」（int）存储与计算，禁止浮点**；积分是整数。API 边界拒绝非整数 `paid_amount_fen`。
- **价格服务端权威**：`app/services/catalog.py` 统一算原价/复购价/资格价，页面无价格字面量。
- **不可变流水**：余额是所有流水汇总；条件 UPDATE 保证 `balance >= 0`，并发不透支。
- **幂等**：注册赠分、支付回调、任务预扣/结算/退款、佣金、邀请奖励全部带确定性唯一键，重放安全。
- **订单状态机**：`created → pending_payment → paid → fulfilled → refunding → refunded / closed`；未完成订单支持「继续支付」恢复。
- **佣金**：推广员 basic 首单 20%/复购 10%、advanced 首单 30%/复购 15%；渠道总上限 40%；15 天观察期；推广佣金可提现（需审核），代理货款不可提现。

---

## 9. 设计包与文档索引

| 文档 | 内容 |
|---|---|
| `docs/README.md` | 设计包索引 |
| `docs/superpowers/specs/2026-11-22-ai-tool-monetization-template-design.md` | 设计全文（§1–§14） |
| `docs/superpowers/plans/2026-11-22-ai-monetization-template.md` | T1–T12 实施计划 |
| `docs/data-model.md` | 数据表与金额/幂等约定 |
| `docs/state-machines.md` | 订单、任务、归因状态机 |
| `docs/payment-sequence.md` | 支付与计费时序、幂等 |
| `docs/commission-formulas.md` | §5.6 佣金公式与算例 |
| `docs/compliance-checklist.md` | 合规清单 |

---

## 10. 已知限制

- 微信登录/支付为 stub，需真实凭证与证书（§5）。
- 开发验证码固定于 `VERIFY_CODE_DEV`，生产需接短信/邮件。
- 队列为 DB-backed 轮询；高并发请换 Celery/Redis。
- 不做多租户、订阅制、小程序虚拟支付，不绑定任何模型厂商。
