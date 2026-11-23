# 「你自己的课堂」— AI 互动课堂变现模板

一套可 fork 复用的 **AI 工具变现代码脚手架**：FastAPI 变现内核 + Jinja2 服务端页面 + 默认 SQLite / 可切 PostgreSQL。内置积分账本、订单支付、按量生成、推广代理、管理后台与 AIGC 合规，自带 **mock 支付 / mock 生成适配器**，本地零外部依赖即可跑通全链路。

产品主线是「**你自己的课堂**」：老师讲义 → **自有编排** → 结构化 **Courseware** → 学生互动课堂。底层 LLM 使用可替换的 DeepSeek（key 由使用者自己提供），但产品的护城河是**自研的讲义到课件编排流程与 Courseware 格式**，不依赖任何外部开源生成引擎。变现采用个人版 ¥199/699/1999 引流 + 机构版 ¥3999 收割；学生/家长免费。

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

本地零依赖跑通用 `GENERATION_ADAPTER=mock`；想接真实模型按 §4 配 DeepSeek key（见 `.env.deepseek.howto.md`）。

**为什么是 `alembic upgrade head && python scripts/seed.py` 这个顺序？**
迁移负责建表与结构，seed 只写默认商品/角色/演示账号。`scripts/seed.py` 里的
`Base.metadata.create_all(bind=engine)` 在表已存在时是 **no-op**（T2 裁决），
它不会覆盖或改写 Alembic 迁移出来的结构；反过来先 seed 再迁移则会出现结构漂移。
这条顺序同样写在容器启动命令里。

跑测试：

```bash
pytest -q                                    # 全量（222+）
pytest tests/test_e2e.py -v                  # 变现全链路验收
pytest tests/test_e2e_own_generation.py -v   # 自有生成闭环验收（fake LLM，无需 key）
```

---

## 2. 端到端链路（本模板验证过的闭环）

`tests/test_e2e.py::test_e2e_full_loop` 覆盖完整变现用户流程：

```
注册(送100积分) → 建档(学科/年级档案) → 备课生成(估算→预扣→worker结算)
→ mock 支付(下单→拉参数→验签回调) → 积分到账(+6000、开通 basic 推广员)
→ 继续生成 → 分享学生(免登录链接、AIGC 标注) → 邀请同事(绑归属+确认+付费)
→ 佣金到账(basic 首单 20% → 13980 分，进 15 天观察期)
```

`tests/test_e2e_own_generation.py::test_e2e_own_generation_loop` 覆盖**自有生成**闭环：

```
注册 → 备课估算(本地规则) → create_job(DeepSeekGenerationAdapter + fake LLM)
→ poll_once(提交→自研编排→结算) → 合法 Courseware(v1.0/scenes/quiz + 分享码)
→ 学生页 GET /c/{share_code} 200 渲染旁白与选项 → 积分预扣=实耗，无差额
```

关键不变量在测试里逐笔断言：金额全是**整数分**、价格由服务端计算、回调幂等、
积分不透支、佣金税前应计且观察期内不可提现；生成结果符合 Courseware schema 且带 AIGC 标注。

---

## 3. 配置（全部环境变量）

复制 `.env.example` 为 `.env` 后按需修改。所有配置项也都在 `app/config.py` 里有相同默认值。

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./app.db` | 换成 `postgresql://user:pwd@host:5432/db` 即切 PostgreSQL |
| `SECRET_KEY` | `dev-secret-change-me` | JWT HS256 签名密钥；**生产必须改** |
| `PAYMENT_GATEWAY` | `mock` | `mock` 或 `wechat`（见 §5） |
| `GENERATION_ADAPTER` | `mock` | `mock`（本地零依赖）或 `deepseek`（自有生成，见 §4） |
| `DEEPSEEK_API_KEY` | 空 | 底层 LLM 的 key，**仅从环境变量读取，不进代码/仓库** |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | 兼容 OpenAI 协议的端点，可换其他厂商 |
| `DEEPSEEK_MODEL` | `deepseek-chat` | 模型名，换厂商时一并改 |
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

## 4. 自有生成能力（讲义 → Courseware → 互动课堂）

变现内核与 AI 能力解耦：订单/账本完全不认识模型厂商。模型只是**可替换的底层执行器**，产品能力掌握在自己手里。

### 4.1 架构与护城河

```
老师讲义（txt/md/pdf/docx，app/services/ingest.py 抽取纯文本）
        │
        ▼
自研编排 app/domain/courseware.py::build_courseware
  理解主题 → 设计场景大纲 → 逐场景生成(失败重试一次/降级占位) → 出随堂测验
        │  LLM 只按指令返回 JSON，编排/提示词/重试/组装全是我们自己的
        ▼
Courseware（自定义 schema v1.0：version/title/scenes/quiz/aigc）
        │  DeepSeekGenerationAdapter 缓存结果，worker 轮询结算
        ▼
学生互动课堂 /c/{share_code}（服务端 Jinja2 + 原生 JS，旁白/要点/答题）
```

讲义输入有两条路径：① **API 上传**——`POST /api/ingest`（multipart，字段名 `file`）调用
`app/services/ingest.py::extract_text` 提取纯文本，支持 txt/md/pdf/docx（扩展名白名单、
大小上限；非法文件返回 400），响应为 `{"text": "..."}`；② **直接粘贴文本**——调用方把
文本放进 job spec 的 `material_text`（或 `text`）。完整「生成页文件上传 UI」尚未接线，
属于后续工作（见 §4.5）。

**护城河是自研编排 + Courseware 格式**，不是任何一个外部开源项目：提示词模板、场景级重试与降级、schema 校验、积分预扣/多退少补、AIGC 打标、分享链路都在本仓库内，换掉底层 LLM 不影响产品。

### 4.2 本地默认（mock）

`GENERATION_ADAPTER=mock`：`MockGenerationAdapter` 按 §12.6 估算表（基础 10 / 互动 25 / PBL 40 / 导出 5 积分）返回模拟结果与分享码，零外部依赖，适合开发和测试。

### 4.3 接真实模型（deepseek）

```bash
GENERATION_ADAPTER=deepseek
DEEPSEEK_API_KEY=sk-你的真实key     # 只从环境变量读，不进代码/仓库
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

详细取 key 与安全说明见 [`.env.deepseek.howto.md`](.env.deepseek.howto.md)。估算（`estimate`）是**本地规则，绝不调用模型**；只有 `submit` 时才真正跑编排。

### 4.4 换模型厂商

只要新厂商兼容 OpenAI chat-completions 的 JSON mode，改 `DEEPSEEK_BASE_URL` + `DEEPSEEK_MODEL` 即可；若要改协议/提示词，实现 `app.adapters.llm.LLMClient` 协议（`complete_json(system, user, *, temperature=0.7) -> dict`）并注入 `DeepSeekGenerationAdapter(client=...)`，或在 `app/adapters/generation/__init__.py::get_adapter()` 登记新分支。worker 轮询、积分预扣/多退少补、失败全额退款、AIGC 标注全部复用。

### 4.5 已知限制

- **单 worker 同步阻塞**：`DeepSeekGenerationAdapter.submit` 在当前 worker 进程内同步跑完整编排；生成期间该 worker 的队列被占用，并发任务会排队。生产应把生成放进真正的任务队列（Celery/Redis 等），worker 只做状态收敛。
- **进程内结果注册表重启即丢失**：适配器用内存 `dict` 按 `external_id` 缓存结果；worker 重启或多 worker 共享 DB 时，注册表丢失会让任务停在 `running`（已刻意设计为不结算空结果，避免误收费），需由持有结果的 worker 重新提交。生产应把结果持久化到 DB/对象存储。
- **计价按课件类型、与场景降级无关**：价格由 `estimate` 按 basic / interactive / PBL 本地规则预估；单场景生成失败降级为占位**不降价也不触发部分退款**，只有整篇编排失败（理解/大纲/测验阶段抛错）才全额退积分。不做按成功场景数的细粒度结算。
- **生成页文件上传 UI 未接线**：目前只有 `POST /api/ingest` 上传 API 与「粘贴文本进 job spec」两条输入路径。若两者都未提供文本，`DeepSeekGenerationAdapter` 会退回内置的 **DEMO 默认讲义**（一元二次方程示例）以保证 demo 可跑；生产接入务必传入真实讲义。
- **key 由使用者提供**：`DEEPSEEK_API_KEY` 不随模板分发，生产需自行妥善保管并遵守所选模型厂商的服务条款与配额。

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
- **密钥**：`SECRET_KEY` 用随机强值；DeepSeek key 与微信凭证放密钥管理系统，不要提交进仓库（`.env` 已被 `.gitignore` 忽略）。
- **HTTPS**：在 nginx 前挂 TLS（或云负载均衡），并确保 `X-Forwarded-Proto` 正确透传——Web 通道的 Cookie + 双提交 CSRF 依赖它。
- **日志清理**：定时跑 `python scripts/purge_logs.py` 按 `AUDIT_RETENTION_DAYS=190` 清理过期审计。
- **生成队列**：按 §4.5 把同步生成迁到真实任务队列，并把结果注册表持久化，以支持多 worker 与重启恢复。
- **静态文件**：`nginx.conf` 已从 `/var/www/static` 直出；改了 `static/` 无需重建 app 镜像。

---

## 7. 合规注意事项

- **AIGC 标识**：`AIGC_LABEL=true`（默认）对生成结果与导出件做元数据打标（学生页展示「本内容由 AI 生成」类提示）；`AUDIT_RETENTION_DAYS=190` 控制审计留存，`scripts/purge_logs.py` 定期清理。
- **模型厂商条款**：底层 LLM key 由使用者自行提供，使用前请遵守所选厂商的服务条款、内容政策与数据合规要求；生成内容由产品侧做 AIGC 标注。
- **防自购套利**：`SELF_REFERRAL_CHECK` 开发默认 `off`；生产应设为 `identity` / `org` / `both`，配合 `identity_hash`（证件号哈希，不存明文）与 `org_id` 拦截同主体套利。
- **分享链接**：`SHARE_LINK_TTL_DAYS=7`，过期学生链接返回 410，不可访问。
- **密钥安全**：`.env` 已被忽略，任何 key 不得提交进仓库或写入日志/共享记忆。

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
| `docs/superpowers/specs/2026-11-22-真实验证结果.md` | 真实 DeepSeek 对上线 `build_courseware` 的验证结果（6 条标准） |
| `docs/superpowers/plans/2026-11-22-ai-monetization-template.md` | T1–T12 实施计划 |
| `docs/data-model.md` | 数据表与金额/幂等约定 |
| `docs/state-machines.md` | 订单、任务、归因状态机 |
| `docs/payment-sequence.md` | 支付与计费时序、幂等 |
| `docs/commission-formulas.md` | §5.6 佣金公式与算例 |
| `docs/compliance-checklist.md` | 合规清单 |
| `.env.deepseek.howto.md` | DeepSeek key 配置与安全说明 |

---

## 10. 已知限制

- 微信登录/支付为 stub，需真实凭证与证书（§5）。
- 开发验证码固定于 `VERIFY_CODE_DEV`，生产需接短信/邮件。
- 队列为 DB-backed 轮询；高并发请换 Celery/Redis；生成为单 worker 同步执行，详见 §4.5。
- 生成结果注册表在进程内，重启/多 worker 场景需持久化，详见 §4.5。
- 计价按课件类型（basic/interactive/PBL），单场景降级不改变价格、不做按场景数结算；生成页上传 UI 未接线，缺省时用 DEMO 默认讲义，详见 §4.5。
- DeepSeek key 由使用者提供，不随模板分发，详见 §4.3 与 `.env.deepseek.howto.md`。
- 不做多租户、订阅制、小程序虚拟支付。
