# 通用 AI 工具变现模板 — 设计文档

- 日期：2026-11-22
- 状态：待批准（尚未进入实现计划）
- 类型：项目（完整设计）

---

## 1. 意图与 Why

基于对「宅老板·秒出片」变现链路的拆解，我们要做一个**从零起步、可 fork 复用的 AI 工具变现模板**。它把已经验证过的商业模式（免费试用 → 积分预付 → 按量消耗 → 加购 → 推广代理裂变）固化成一套可运行的代码骨架，让任何做一个「按次/按时长消耗 AI 算力」的产品都能直接接入自己的模型与支付渠道，而不是从零重造账本、订单和分账。

参考产品验证过的关键事实：

- 核心收入是**预付积分套餐**（¥199/¥699/¥1999/¥3999），按真实用量结算、多退少补；
- 支付闭环依赖微信生态（JSAPI/扫码/小程序虚拟支付），订单状态与幂等是关键；
- 留存靠积分有效期、排队优先级、多端资产沉淀；
- 增长靠邀请奖励 + 推广佣金 + 正式代理（¥9999 货款准入、40% 渠道上限、一层团队差额、15 天观察期）。

模板的价值在于：**变现内核最难事后补，应该先固化成代码。**

**Demo 垂直场景（2026-11-22 定）**：模板带一个具体产品——面向老师的 **AI 互动课堂工作台**（备课→生成互动课件→分享学生上课）。理由：纯 AI 文案无壁垒；教育场景的「可交付成品 + 资产沉淀 + 教师渠道」才是护城河。付费方采用 **C 方案：老师个人版（¥199/699/1999）引流 + 学校/机构版（¥3999，多席位共享额度）收割**。学生/家长免费，作为效果证明与扩散节点。生成引擎的生产实现可对接 OpenMAIC（MIT，可商用；注意其 `mathml2omml` 子包为 LGPL-3.0）；模板自带 mock 适配器，本地零依赖跑通闭环。

---

## 2. 目标 / 非目标 / 反目标

### 目标
1. 提供可运行的最小闭环：注册 → 试送积分 → 估算消耗 → 提交生成任务 → 预扣 → 出片结算/失败退款 → 充值 → 分销。
2. 变现逻辑与 AI 能力解耦：换一个模型、换一个支付渠道，不改核心账本。
3. 内置参考产品验证过的规则：积分有效期、预扣多退少补、订单状态机、支付回调幂等、邀请归属、代理货款不可提现与一层差额。
4. 自带 mock 实现，本地零外部依赖即可跑通全链路（含支付与 AI 生成）。
5. 配套可被替换的文档：数据表、状态机、支付时序、佣金公式、合规清单。

### 非目标
- 不实现任何具体 AI 模型/绘图/配音/TTS；只定义适配器接口 + mock。
- 不做 iOS/Android/Windows 客户端；只做 Web（服务端渲染）与预留 API。
- 不做多租户 SaaS（一套部署 = 一个产品）。
- 不做完整订阅制计费（仅一次性套餐 + 按量积分 + 单次付费）。
- 不做营销内容 CMS、不做客服工单系统（预留接口即可）。

### 反目标（做出来就算失败）
- 把价格写死在代码/前端里，让客户端参与计价。
- 只存一个可变余额、没有不可篡改的账本流水。
- 支付回调不做幂等，重复通知导致重复加积分。
- 允许自购套利、多级分销、代理货款可提现。
- 把 AI 供应商 SDK 直接揉进订单逻辑里。

---

## 3. 约束与我的决定（builder 可推翻但需说明）

| 项 | 决定 | 理由 |
|---|---|---|
| 后端 | Python 3.11+ / FastAPI / SQLAlchemy / Alembic | 与参考产品同构（Python + Jinja2），生态成熟，账本事务容易写对 |
| 数据库 | 默认 SQLite，通过 `DATABASE_URL` 可切 Postgres | 模板零配置可跑；生产切 Postgres |
| 前端 | Jinja2 服务端渲染 + 原生 JS（无构建步骤） | 参考产品已验证；降低 fork 门槛 |
| 支付 | 适配器接口 + 微信 JSAPI/Native 实现 + `mock` 网关 | 本地零依赖跑通；微信需商户号，生产替换 |
| AI 生成 | `GenerationAdapter` 接口 + `mock` 适配器（含模拟队列） | 不绑定任何模型厂商 |
| 队列 | 默认 DB-backed 后台 worker；接口支持换 Celery/Redis | 模板阶段避免引入 Redis 依赖 |
| 身份 | JWT（web 用 httpOnly Cookie + CSRF；小程序/API 用 Bearer） | Web 安全默认，同时保留 API 通路 |
| 部署 | uvicorn + nginx 反向代理说明 + docker-compose（可选） | 与参考产品部署形态一致 |
| 归因 | 邀请码/链接 90 天候选期，首笔有效订单锁定长期归属 | 直接复用参考规则 |

---

## 4. 方案对比（Approaches）

| 方案 | 内容 | 取舍 | 结论 |
|---|---|---|---|
| A 可运行代码脚手架 | 仓库含账本/订单/支付/分销/后台 + mock 适配器 + 文档 | 工作量最大；拿来即用 | **已选** |
| B 纯文档蓝图 | 模块图、数据表、状态机、时序、佣金公式 | 快但每行都要自己写 | 放弃（不满足「模板」价值） |
| C 低代码配置 | 现成 SaaS 拼装 | 撑不住按量账本与分账 | 放弃 |

选 A 的理由：模板的价值就是可复用，而账本、订单幂等、分账结算正是最难事后补的部分，必须先固化成代码。B 作为仓库内 docs 一并交付。

---

## 5. 系统模块与边界

```
┌─────────────────────────────────────────────────────────┐
│  表现层  Jinja2 页面：落地/价格/样片/ legal/后台 + 原生JS │
├─────────────────────────────────────────────────────────┤
│  应用服务层                                              │
│  auth │ catalog │ credits │ orders │ generation │       │
│  affiliate │ admin │ notification                       │
├─────────────────────────────────────────────────────────┤
│  领域内核（纯逻辑，不依赖框架/支付/模型）                 │
│  Ledger(复式流水) │ OrderStateMachine │ Attribution │    │
│  CommissionEngine │ QuotaPolicy                         │
├─────────────────────────────────────────────────────────┤
│  适配器层（可替换）                                       │
│  PaymentGateway(wechat/mock) │ GenerationAdapter(mock)   │
│  IdentityProvider(password/wechat) │ Notifier(log)       │
├─────────────────────────────────────────────────────────┤
│  DB(SQLite/Postgres) │ 后台 worker │ nginx              │
└─────────────────────────────────────────────────────────┘
```

### 5.1 身份与角色（auth）
- 注册方式：手机号/邮箱 + 验证码/密码（模板内用可配置验证码，开发期固定码）；可插拔微信 OAuth。
- 角色：`user` → `promoter`（推广员，由套餐权益开通）→ `agent`（正式代理，人工审核后付费开通）→ `admin`。
- Web 用 httpOnly Cookie + CSRF token；API（及未来小程序）用 Bearer JWT。token 中带 `channel`（web / api）与 `referrer_code`。
- 防自购/防套利采用可选字段：`identity_hash`（证件号哈希，不可逆，不存明文）、`org_id`（企业/组织号）；匹配规则由配置 `self_referral_check = off | identity | org | both` 控制，开发环境默认 `off`。

### 5.2 积分账本（credits）— 最核心
- **不可变流水表** `credit_ledger`：`id, user_id, delta, balance_after, type, ref_type, ref_id, idempotency_key, created_at, expires_at, pool`。
- 类型：`grant`（注册赠送100）、`purchase`（套餐到账）、`bonus`（邀请奖励）、`consume`（生成预扣）、`settle`（多退少补回滚差额）、`refund`、`expire`、`adjust`（后台修正，需理由+审计）。
- 余额 = 流水汇总（缓存 `credit_balance` 表 + 每日对账任务校验）。
- **积分池**：`universal`（通用）与 `dedicated`（渠道/专属，如动态图解专属积分）；扣减策略可配置优先级。
- 有效期：默认 2 年（`CREDIT_EXPIRE_DAYS=730`）。「充值自动续期」模板默认语义为：**充值成功后，把该用户所有未过期、未消费完的 `universal` 积分批次的 `expires_at` 统一顺延到「充值时间 + 有效期」**（即全部存量续期，不只给新积分重新计时）；可通过 `RENEW_MODE=all_active|new_only` 切换。到期生成 `expire` 流水。
- 幂等：所有加积分入口强制 `idempotency_key`，支付回调、退款回调、任务结算都带唯一键。
- API：`GET /credits/balance`、`GET /credits/ledger`、`POST /credits/estimate`（调用生成适配器估算消耗）。

### 5.3 商品与价格（catalog）
- `product` 表：`id, sku, name, type(package/single/voice/service/agent), amount_fen, credits, bonuses(json), eligibility(json), active, sort`。
- 规则外置在 DB/配置，不在代码写死：原价、复购价（参考：699→559.20、1999→1399.30）、限时价（19/49/99）、套餐赠品（课程/音色/推广等级/企业席位）。
- 前端只展示服务端返回的 `amount_yuan / credits / 是否可购`，不参与计价。
- 参考默认 SKU：`pkg_newcomer_experience_199`、`pkg_personal_699`、`pkg_team_1999`、`pkg_enterprise_collab_3999`、`svc_voice_clone_299`、`agent_admission_9999`。

### 5.4 订单与支付（orders）
- 订单状态机：`created → pending_payment → paid → fulfilled → closed / refunding → refunded`。
- 流程：
  1. `POST /api/pay/orders {sku, channel}` → 服务端按用户状态计算唯一合法成交价（复购/渠道/资格），创建订单；
  2. 已存在未完成订单时返回 `pending_order_payment_in_progress`，前端恢复「继续支付」；
  3. `POST /api/pay/orders/{no}/jsapi_pay` 或 `/native_pay` → 调支付网关适配器；
  4. 网关 webhook / 前端 `POST /api/pay/orders/{no}/sync` → 幂等确认 → 触发 `fulfilled`（发积分/权益）；
  5. 退款：后台发起 → 冲回佣金 → 回收/冻结权益 → 记 `refund` 流水。
- 支付适配器接口：`create_payment(order) -> {pay_params, qr}`，`verify_callback(payload, signature) -> order_no`，`query(order_no) -> status`，`refund(order_no, amount) -> result`。
- webhook 必须验签；回调幂等靠 `order_no + event_id` 唯一约束。
- 交付含 `mock` 网关（自动确认支付，可模拟失败/退款），本地可跑通。
- **退款与已消费积分**：账本不变量为 `balance >= 0`。退款时按订单来源批次计算「未消费余额」，自动退回**未消费部分**对应的款项；已消费部分不自动退款，进入人工核价（`refund_status=manual_review`）。不记负余额、不冻结账户（避免一个订单拖垮整个账号）。此规则可通过 `REFUND_MODE=unused_only|full_manual` 切换。
- **「单次付费」不是第二条链**：所有钱先走订单 → 履约成积分；所有生成都走积分预扣。单次付费商品（`type=single`）的履约动作是发放一个**专属积分批次**（`pool=dedicated`，额度按该次生成预估），随后提交生成任务时扣减该批次——即与套餐共用同一套账本与任务引擎，只是「先买后用」的批次粒度不同。不存在「支付成功直接触发生成」的旁路。

### 5.5 生成任务（generation）
- 适配器接口：`estimate(input) -> credits`，`submit(job) -> external_id`，`status(external_id) -> state`，`cancel(external_id)`。
- 任务状态：`queued → running → succeeded / failed / cancelled`。
- 计费时序：`estimate` 展示预计积分 → 提交时 `consume` 预扣 → worker 调适配器 → 成功按真实用量 `settle`（多退少补）→ 失败全额 `refund`。
- AIGC 合规钩子：成功后回调 `Notifier` 写入标识元数据与审计日志（可配置保留 190 天）。

### 5.6 推广与代理（affiliate）— 增长内核
- 归因：邀请码/链接产生 90 天**候选关系**；客户明确确认 + 完成首笔有效订单后锁定**长期唯一归属**（防抢客、防自购）。
  - 「客户明确确认」= 客户登录后弹出的归属确认勾选，写入 `attributions.confirmed_at`。仅点击链接/注册/填手机号不锁定。
  - 「有效订单」= 已支付且未退款的套餐订单（含 ¥199 体验包，用于**锁定归属**；但 ¥199 不产生现金佣金）。
  - 「复购」= 同一实名主体存在**同一 SKU 的、未退款的历史成功实付订单**；系统据此计算唯一合法成交价（复购价）。
- 推广员等级：`basic`（首单20%/复购10%）、`advanced`（首单30%/复购15%）；¥199 体验包无现金佣金，按邀请送 50 积分（上限20人）。
- 正式代理：¥9999 准入 → 全额转**不可提现代理货款**；官网订单按客户实付 **40%** 计收益（15 天观察期，退款冲回）；代开按官方价 **60%** 扣货款、40% 价差自留；仅**一层**直属团队差额；成员升级代理后独立。
- **一层团队差额公式**（渠道上限 40%，成员佣金与差额合计 ≤ 40%，基数统一为「客户该笔最终合法现金实付金额」）：
  - 客户官网直付：推广员先按等级拿 `commission_promoter = paid × rate(首单/复购)`；若该客户归属某正式代理且存在直属推广员，代理得 `commission_agent = paid × 40% − commission_promoter`；若无推广员，代理独得 `paid × 40%`。
  - 代理用货款代开：平台按官方价 60% 扣代理货款，代理自留 40% 价差；**该订单不再产生任何推广佣金或代理佣金**。
  - 上述佣金均为**税前应计金额**，进入 15 天观察期，退款同步冲回；已提现部分形成待抵扣金额。
- 结算账本：`commission_ledger`（含观察期、冻结、冲回、已提现形成待抵扣）；提现需审核。推广员佣金为**可提现现金余额**（观察期结束后），默认最低提现 ¥100、人工审核、税费按配置处理；代理货款**不可提现**，仅可用于代开，代理退出/降级按平台清算规则人工结算。
- 防套利：同实名/同企业/代理之间互购不计佣金（依据 §5.1 的 `identity_hash` / `org_id` 与配置开关）。

### 5.7 管理后台（admin）
- 商品/价格配置、订单查询与手动同步、积分调整（需理由）、用户与角色、代理审核与货款/佣金/提现、退款审批、统计看板（注册/付费/消耗/佣金）。

---

## 6. 数据模型（核心表）

`users`、`user_roles`、`credit_ledger`、`credit_balance`、`products`、`orders`、`order_events`、`generation_jobs`、`invitations`、`attributions`、`promoters`、`agents`、`agent_fund_ledger`、`commission_ledger`、`withdrawals`、`audit_logs`、`aigc_audit`。

每张资金相关表都带 `idempotency_key` 唯一约束与 `created_at`；所有金额用**分**（整数）存储，不用浮点。

---

## 7. 关键流程时序（支付 + 计费）

```
Client            API           PaymentGW        Worker        AIAdapter
  |--estimate----->|                 |               |             |
  |<-预计积分-------|                 |               |             |
  |--submit------->|--consume(预扣)---|               |             |
  |                |--create job-------------------->|             |
  |<-job_id--------|                 |               |--submit---->|
  |                |                 |               |<-external---|
  |--pay order---->|--create payment>|               |             |
  |<-pay params----|<----------------|               |             |
  |--(支付)--------|---------------->|---webhook---->|             |
  |                |<--verify/sync---|               |             |
  |                |--fulfilled(发积分/权益)         |             |
  |                |                 |               |<--done/cb---|
  |                |--settle/退款差额-----------------|             |
  |<--通知完成-----|                 |               |             |
```

---

## 8. 技术落地约定

- 分层：`app/api`（路由）、`app/services`（用例编排）、`app/domain`（纯领域逻辑）、`app/adapters`（可替换外部）、`app/models`（ORM）、`templates/`（Jinja2）、`tests/`。
- 所有资金操作在数据库事务内完成；领域函数不依赖 FastAPI Request。
- 配置全部走环境变量（`.env.example`）：`DATABASE_URL`、`SECRET_KEY`、`PAYMENT_GATEWAY=mock|wechat`、`GENERATION_ADAPTER=mock`、`CREDIT_EXPIRE_DAYS=730`、`COMMISSION_OBSERVATION_DAYS=15`。
- 测试：`pytest`，重点覆盖账本不变量、订单状态转移、回调幂等、归因与佣金计算（含退款冲回）。

---

## 9. 交付物清单

1. 可运行仓库：上述模块 + mock 适配器 + Jinja2 页面 + Alembic 迁移 + 测试。
2. `README.md`：5 分钟本地跑通、如何换成自己的模型/支付渠道、部署说明。
3. `docs/` 内的设计包：本文 + 数据表 + 状态机 + 支付时序 + 佣金公式 + 合规清单。
4. 种子数据：默认商品、积分规则、角色、演示账号。

---

## 10. 留给 builder 的部分（我的决定，可调整）

- 具体 UI 视觉（颜色/布局）不做美术设计，只保证功能可用。
- 微信商户号/小程序 AppID、真实模型 API Key 由使用者提供；模板只留配置项与 mock。
- 队列在模板阶段用 DB-backed worker；生产可换 Celery/Redis，接口不变。
- 验证码在开发环境固定，生产需接短信/邮件服务。
- 后台鉴权用同一套角色系统，不单独建后台账号体系。

---

## 11. Builder 审查后的规则澄清（我的决定，均为可配置项）

构建方提出的 8 个问题已按「参考产品事实」或「模板默认规则」解决如下。全部以配置常量呈现，fork 者可改；若你有不同业务判断，直接告诉我改哪一项。

| # | 问题 | 决定 |
|---|---|---|
| 1 | 单次付费 vs 积分/生成的关系 | **统一一条链**：所有钱 → 订单 → 积分；所有生成 → 积分预扣。单次付费只是发放一个 dedicated 积分批次，无支付直达生成的旁路。 |
| 2 | 一层团队差额公式 | 基数 = 客户最终合法实付；渠道总上限 40%；推广员先拿等级佣金，代理拿「40% − 推广员佣金」；代开订单不再计任何佣金。 |
| 3 | 有效订单/复购/明确确认 | 有效订单 = 已付未退的套餐单（¥199 算归属锁定但不计现金佣金）；复购 = 同一主体存在同 SKU 未退款历史实付单；明确确认 = 登录后归属确认勾选写 `confirmed_at`。 |
| 4 | 防自购依据 | `users` 增加 `identity_hash`（证件号哈希，不存明文）与 `org_id`；由 `self_referral_check` 配置开关，开发默认 `off`。 |
| 5 | 已消费积分后退款 | 账本不允许负余额；自动退未消费部分，已消费部分转人工核价；`REFUND_MODE` 可切换。 |
| 6 | 小程序是否在范围内 | **不在交付范围**。仅 Web + 预留 API；Bear Token 通路为未来小程序保留，不实现小程序虚拟支付适配器。 |
| 7 | 资金属性/提现 | 推广佣金 = 观察期后可提现现金（最低 ¥100、人工审核、税前应计）；代理货款不可提现、仅可代开，退出按清算规则人工结算。 |
| 8 | 充值自动续期语义 | 默认 `RENEW_MODE=all_active`：充值把所有未过期 universal 批次续到「充值时 + 有效期」；可切 `new_only`。 |

---

## 12. 产品设计：AI 互动课堂工作台（Demo 垂直场景）

### 12.1 产品定义与壁垒

**一句话**：老师丢进一份教材/讲稿/公众号文章，10 分钟产出一套「明天就能上课」的互动课件包（幻灯片 + 测验 + 互动模拟 + 讲义 + 可编辑 PPTX/离线 HTML），用得越久越贴合这位老师。

| 通用 AI 文案工具 | 本产品（壁垒） |
|---|---|
| 输出一段文字，用户自己再加工 | 输出**可交付的教学成品**：课件、测验、互动、导出文件 |
| 每次从零开始 | 沉淀**个人知识库、模板库、题库、IP 音色**——越用越贴合 |
| 拼模型能力 | 拼**教育工作流深度**：备课→生成→编辑→上课→复用闭环 |
| 单人工具 | 长成**机构工作台 + 教师渠道代理**（直接复用变现内核） |

### 12.2 用户、角色与付费方（C 方案）

| 角色 | 谁 | 动作 | 钱/收益 |
|---|---|---|---|
| 老师用户 | 备课的本人 | 生成课堂、分享学生 | **付费方**：个人版 ¥199/699/1999 |
| 推广老师 | 用得好的老师 | 推给同事/同学科老师 | 首单 20–30%、复购 10–15% 佣金 |
| 机构代理 | 教研组长/培训机构/学校 | 开席位、代开套餐 | ¥9999 准入 + 40% 渠道收益 |
| 机构成员老师 | 机构下的老师 | 用共享额度备课 | 不单独付费，消耗机构积分 |
| 学生/家长 | 课堂接收端 | 通过分享链接上互动课 | **免费**（效果证明与扩散节点，不付费） |

### 12.3 页面清单与角色可见性

| 页面 | 路径 | 面向 | 作用 |
|---|---|---|---|
| 落地页 | `/` | 所有人 | 价值主张、样例课堂、价格入口 |
| 登录/注册 | `/login` | 未登录 | 微信扫码 / 手机号，带邀请码 |
| 首次建档 | `/onboarding` | 新老师 | 学科/年级/用途 → 决定推荐模板 |
| 工作台 | `/workbench` | 老师 | 课堂列表、模板/知识库入口、推广入口 |
| 备课创作 | `/generate` | 老师 | 输入主题/上传资料 → 选风格 → 估算积分 → 提交 |
| 生成进度 | `/jobs/{id}` | 老师 | 队列进度（大纲→场景→互动→合成） |
| 课堂编辑 | `/classroom/{id}/edit` | 老师 | 场景编辑、讲义、测验、音色 |
| 分享设置 | `/classroom/{id}/share` | 老师 | 学生链接/二维码、有效期、密码 |
| 我的资产 | `/library` | 老师 | 知识库、模板、题库、专属音色 |
| 生成历史 | `/history` | 老师 | 回看、复用、导出 |
| 推广中心 | `/promote` | 推广老师/代理 | 邀请码、佣金、提现、代理入口 |
| 价格页 | `/pricing` | 所有人 | 个人版 + 机构版对比 |
| 机构工作台 | `/org` | 机构老板 | 席位、共享额度、成员消耗明细 |
| 学生课堂 | `/c/{share_code}` | 学生（免登录） | 互动课播放、测验、讲义 |
| 管理后台 | `/admin` | admin | 商品/订单/积分/代理/退款/统计 |

### 12.4 端到端用户流程

```
【老师首次】落地页 → 微信登录 → 送100积分 → 建档(学科/年级) → 工作台
【备课】选主题/传资料 → 选课堂风格 → 系统估算积分(服务端) → 预览确认
      → 提交(预扣积分) → 生成进度页 → 完成(按真实用量多退少补)
【交付】课堂编辑 → 分享设置 → 生成学生链接/二维码 → 发到班级群
【学生】点链接(免注册) → 上互动课/做测验 → 效果证明 → 同事看到
【变现】积分不足 → /pricing → 个人版套餐 → 支付 → 到账 → 继续生成
【裂变】/promote → 邀请同事 → 同事付费 → 佣金 → 提现
【升级】机构采购 ¥3999 → /org 开席位 → 老师加入 → 共享额度消耗
```

### 12.5 关键页面文字线框图

**备课创作页 `/generate`**
```
┌──────────────────────────────────────────────┐
│ 秒备课           剩余积分: 100   [价格] [推广] │
├───────────────────┬──────────────────────────┤
│ ① 输入主题/上传资料 │ 实时预估                 │
│ [_______________] │ 课堂类型: 互动讲授          │
│ [上传 PPT/PDF/文档]│ 场景数: 12                │
│                   │ 预计消耗: 20 积分          │
│ ② 课堂风格         │ [当前余额 100]            │
│ ○ 互动讲授 ○ PBL   │ ⚠ 提交前确认，出片后多退少补│
│ ○ 测验课 ○ 研讨    │                          │
│ ③ 音色             │ [ 生成课堂 ]              │
│ ○ 系统音色 ○ 专属  │                          │
└───────────────────┴──────────────────────────┘
```

**学生课堂页 `/c/{share_code}`（免登录）**
```
┌──────────────────────────────────────────────┐
│ 李老师的互动课堂 · 第3章 分数运算      [讲义↓] │
├──────────────────────────────────────────────┤
│                                              │
│        [ 当前场景：幻灯片 / 互动模拟 ]         │
│                                              │
│  讲师讲解(语音) + 白板绘图                     │
│                                              │
│  [上一页]  [下一页]  3 / 12                   │
├──────────────────────────────────────────────┤
│ 随堂测验: 1/3 已答   [ 进入测验 ]             │
└──────────────────────────────────────────────┘
```

**推广中心 `/promote`**
```
┌──────────────────────────────────────────────┐
│ 推广中心                                      │
│ 我的邀请码: TCH-8F3K  [复制] [复制链接]        │
│ 累计邀请: 12 人   待结算佣金: ¥340  可提现:¥210│
│ 等级: 高级推广 (首单30% / 复购15%)             │
│ [ 申请成为机构代理 (¥9999) ]                  │
│ ── 明细 ───────────────────────────────────── │
│ 王老师  首单¥699 佣金¥209  观察期14天          │
│ [ 提现 (最低¥100) ]                           │
└──────────────────────────────────────────────┘
```

### 12.6 计量与商品映射（模板默认值，均可配置）

| 计量项 | 积分 | 说明 |
|---|---|---|
| 生成一套基础互动课堂 | 10 | 幻灯片 + 讲义 |
| 含互动模拟/测验的课堂 | 25 | 多场景、可交互 |
| PBL 项目式课堂 | 40 | 多课时项目 |
| PPTX / 离线 HTML 导出 | 5 | 单次导出 |
| 专属音色复刻 | ¥299 | 加购，不按积分 |

个人版 SKU 沿用默认值（¥199/699/1999）；机构版 ¥3999 含 1 老板 + 10 席位 + 45000 共享积分。

### 12.7 与 OpenMAIC 的集成方式（我的决定）

- 模板**不 fork OpenMAIC**，而是通过 `GenerationAdapter` 的 HTTP 实现把「生成课堂」委托给独立部署的 OpenMAIC 服务；本地开发用 mock 适配器。
- 理由：保持变现内核语言栈（Python）与 AI 引擎（Node）解耦；换模型/换引擎不改账本。
- 合规：OpenMAIC 为 MIT 可商用；整体再分发时须保留其 `mathml2omml`（LGPL-3.0）与 `pptxgenjs`（MIT）子包许可；不得使用「清华/THU」名义背书。

---

## 13. 各任务落地映射（每个任务的可执行方案）

> 本节保证「没有空白项」：每个任务都给出具体文件、配置、命令、验收标准。实现计划（writing-plans）会把每个任务展开成逐步执行的步骤。

| # | 任务 | 具体文件/目录 | 配置项 | 命令 | 验收标准 |
|---|---|---|---|---|---|
| T1 | 项目骨架与配置 | `pyproject.toml`, `app/main.py`, `.env.example`, `docker-compose.yml` | `DATABASE_URL`, `SECRET_KEY`, `PAYMENT_GATEWAY=mock`, `GENERATION_ADAPTER=mock` | `pip install -e .` / `docker compose up` | `uvicorn app.main:app` 启动，`/health` 返回 200 |
| T2 | 数据库迁移与种子 | `app/models/*.py`, `alembic/versions/*`, `scripts/seed.py` | — | `alembic upgrade head && python scripts/seed.py` | 核心表齐全，默认商品/角色/演示账号入库 |
| T3 | 身份与角色 | `app/api/auth.py`, `app/services/auth.py`, `app/adapters/identity/*`, `templates/login.html` | `VERIFY_CODE_DEV=123456` | `pytest tests/test_auth.py` | 注册/登录/角色提升通过；Web Cookie + CSRF、API Bearer 两条通路可用 |
| T4 | 积分账本内核 | `app/domain/ledger.py`, `app/services/credits.py`, `app/api/credits.py`, `tests/test_ledger.py` | `CREDIT_EXPIRE_DAYS=730`, `RENEW_MODE=all_active`, `REFUND_MODE=unused_only` | `pytest tests/test_ledger.py` | 不可变流水、余额≥0、幂等、续期与退款分支全部通过 |
| T5 | 商品与定价 | `app/services/catalog.py`, `app/api/catalog.py`, `scripts/seed.py`, `templates/pricing.html` | 复购价/限时价在 DB 配置 | `pytest tests/test_catalog.py` | 价格由服务端计算；前端只展示；复购/资格规则生效 |
| T6 | 订单与支付（mock + 微信适配器） | `app/domain/order.py`, `app/services/orders.py`, `app/adapters/payment/{mock,wechat}.py`, `app/api/pay.py` | `PAYMENT_GATEWAY`, `WECHAT_*` | `pytest tests/test_orders.py tests/test_payment_callback.py` | 状态机正确、回调幂等、未完成订单恢复、退款冲回佣金通过 |
| T7 | 生成任务与 mock AI | `app/domain/generation.py`, `app/adapters/generation/{mock,openmaic_http}.py`, `app/workers/worker.py`, `app/api/jobs.py` | `GENERATION_ADAPTER`, `OPENMAIC_URL`, `WORKER_POLL_SEC` | `pytest tests/test_generation.py` + 手动跑一次 mock 生成 | 预扣→执行→结算/失败退款闭环；课堂成品与分享码生成 |
| T8 | 推广与代理 | `app/domain/attribution.py`, `app/domain/commission.py`, `app/services/affiliate.py`, `app/api/promote.py`, `app/adapters/payment/withdrawals.py` | `COMMISSION_OBSERVATION_DAYS=15`, `MIN_WITHDRAW_FEN=10000`, `SELF_REFERRAL_CHECK=off` | `pytest tests/test_affiliate.py` | 90天归因/首单锁定、40%上限差额、代开不计佣、提现审核通过 |
| T9 | 管理后台 | `app/api/admin.py`, `templates/admin/*.html` | — | `pytest tests/test_admin.py` | 商品/订单/积分调整/代理审核/退款/统计可用，操作有审计日志 |
| T10 | 产品页面与分享 | `templates/{index,workbench,generate,classroom,share,library,promote,org}.html`, `static/app.js` | `SHARE_LINK_TTL_DAYS` | 启动服务后浏览器逐页走通 | 老师端 8 页 + 学生端课堂页可用；线框图对应元素齐全 |
| T11 | AIGC 合规与审计 | `app/services/compliance.py`, `app/models/aigc_audit.py`, `scripts/purge_logs.py` | `AIGC_LABEL=true`, `AUDIT_RETENTION_DAYS=190` | `pytest tests/test_compliance.py` | 导出带标识；审计日志写入并可按期清理 |
| T12 | 部署与文档 | `README.md`, `docs/*`, `Dockerfile`, `nginx.conf` | 环境变量清单齐备 | `docker compose up --build` + `curl /health` | 5 分钟本地跑通；README 含换模型/换支付/部署说明 |

---

## 14. 交付顺序（阶段）

1. **M1 内核可跑**：T1 → T2 → T3 → T4 → T5 → T6 → T7（本地 mock 跑通注册→备课→生成→支付→结算）。
2. **M2 增长与后台**：T8 → T9 → T11（推广、代理、后台、合规）。
3. **M3 产品化**：T10 → T12（页面完整、文档、部署）。
