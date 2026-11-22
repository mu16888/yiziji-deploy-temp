"""End-to-end acceptance test: the full monetization loop (Task 12).

老师首次的全链路（设计 §12.4）：
    注册 → 建档 → 备课生成 → mock 支付 → 积分到账 → 继续生成 → 分享学生
    → 邀请同事 → 佣金到账

业务步骤复用 T1–T11 的真实服务与 API；最后一步「部署与文档验收」校验本任务
交付的部署文件与 README 是否齐备（RED→GREEN 的驱动点）。所有金额均为整数分，
价格全部由服务端计算，客户端不参与计价。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.payment import mock as mock_pay
from app.db import Base, get_db
from app.main import app
from app.models import CommissionEntry, GenerationJob, Product, Promoter, User
from app.services.auth import issue_token
from app.services.credits import get_balance
from app.workers.worker import poll_once
from scripts.seed import PRODUCTS

CODE = "123456"  # VERIFY_CODE_DEV
PERSONAL = "pkg_personal_699"
PRICE_FEN = 69900
PURCHASE_CREDITS = 6000
BASIC_FIRST_COMMISSION_FEN = PRICE_FEN * 20 // 100  # 13980 (§5.6 basic 首单 20%)


# --- fixtures / helpers -------------------------------------------------------


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    for data in PRODUCTS:
        session.add(Product(**data))
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _auth(client: TestClient, db, user_id: int) -> dict[str, str]:
    """Bearer headers for one actor; clear cookies so the Bearer channel wins."""
    client.cookies.clear()
    user = db.get(User, user_id)
    assert user is not None
    return {"Authorization": f"Bearer {issue_token(user, channel='api')}"}


def _sync_payment(client: TestClient, order_no: str, event_id: str, amount_fen: int) -> None:
    """Simulate the mock gateway webhook, signed exactly like a real callback."""
    payload = {
        "order_no": order_no,
        "event_id": event_id,
        "paid_amount_fen": amount_fen,
        "timestamp": "1700000000",
        "nonce": f"nonce-{event_id}",
    }
    payload["signature"] = mock_pay.sign(payload)
    r = client.post("/api/pay/sync", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "fulfilled"
    assert r.json()["order_no"] == order_no


def _assert_deployment_and_docs(root: Path) -> None:
    """Acceptance: the deployable artifacts and the required README content exist."""
    for name in ("README.md", "Dockerfile", "docker-compose.yml", "nginx.conf"):
        assert (root / name).is_file(), f"missing deployment artifact: {name}"

    readme = (root / "README.md").read_text(encoding="utf-8")
    for token in (
        "5 分钟",  # 5-minute local run
        "alembic upgrade head",  # required workflow order
        "python scripts/seed.py",
        "GENERATION_ADAPTER",  # swap model
        "DEEPSEEK_API_KEY",  # self-owned generation, replaceable LLM
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_MODEL",
        "Courseware",
        "自有生成",
        "PAYMENT_GATEWAY",  # swap payment
        "WECHAT_",
        "docker compose",  # deployment
        "nginx",
        "AIGC_LABEL",
        "SELF_REFERRAL_CHECK",
        "AUDIT_RETENTION_DAYS",
        "SHARE_LINK_TTL_DAYS",
        "DATABASE_URL",
        "SECRET_KEY",
        "分",  # money in fen
    ):
        assert token in readme, f"README missing required topic: {token}"

    # Short brand tokens use word boundaries so ordinary words containing the
    # letters (e.g. "thus") cannot trip the check; the rest stay substrings.
    for banned in ("清华", "lgpl", "mathml2omml", "pptxgenjs"):
        assert banned not in readme.lower(), f"README still mentions rejected direction: {banned}"
    for pattern in (r"\bTHU\b", r"\bOpenMAIC\b"):
        assert not re.search(pattern, readme, re.IGNORECASE), (
            f"README still mentions rejected direction: {pattern}"
        )

    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "nginx" in compose and "app:" in compose
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "uvicorn" in dockerfile
    nginx = (root / "nginx.conf").read_text(encoding="utf-8")
    assert "proxy_pass" in nginx and "8000" in nginx


# --- the full loop ------------------------------------------------------------


def test_e2e_full_loop(db, client):
    root = Path(__file__).resolve().parents[1]

    # 0. 健康检查
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}

    # 1. 注册：送 100 积分，拿到邀请码
    r = client.post(
        "/api/auth/register",
        json={"phone": "13811110001", "code": CODE},
    )
    assert r.status_code == 200, r.text
    teacher_id = r.json()["user_id"]
    teacher = db.get(User, teacher_id)
    teacher_invite = teacher.invite_code
    teacher_headers = _auth(client, db, teacher_id)
    assert teacher_invite and get_balance(db, teacher_id) == 100

    # 2. 建档：老师档案（注册时建立）+ 首次建档页可达
    r = client.get("/onboarding", headers=teacher_headers)
    assert r.status_code == 200, r.text
    assert teacher.display_name and teacher.invite_code

    # 3. 备课生成：估算 → 预扣 → worker 结算（多退少补）
    spec1 = {"type": "basic", "scenes": 12, "topic": "第3章 分数运算"}
    est = client.post("/api/estimate", json={"spec": spec1}, headers=teacher_headers)
    assert est.status_code == 200, est.text
    assert est.json()["credits"] == 10
    r = client.post("/api/jobs", json={"spec": spec1}, headers=teacher_headers)
    assert r.status_code == 200, r.text
    job1 = r.json()
    assert job1["status"] == "queued" and job1["reserved_credits"] == 10
    assert get_balance(db, teacher_id) == 90
    assert poll_once(db) >= 1
    done1 = db.get(GenerationJob, job1["id"])
    assert done1.status == "succeeded"
    assert done1.result["share_code"] and done1.result["aigc_label"] is True
    assert get_balance(db, teacher_id) == 90  # 预估=实耗，无差额

    # 4. mock 支付：下单 → 拉取支付参数 → 验签回调确认
    r = client.post(
        "/api/pay/orders",
        json={"sku": PERSONAL, "channel": "native"},
        headers=teacher_headers,
    )
    assert r.status_code == 200, r.text
    order = r.json()
    order_no = order["order_no"]
    assert order["amount_fen"] == PRICE_FEN  # 服务端权威定价
    r = client.post(
        "/api/pay/native_pay", json={"order_no": order_no}, headers=teacher_headers
    )
    assert r.status_code == 200 and "code_url" in r.json(), r.text
    _sync_payment(client, order_no, "e2e-teacher-1", PRICE_FEN)

    # 5. 积分到账：套餐积分入库，开通 basic 推广员权益
    assert get_balance(db, teacher_id) == 90 + PURCHASE_CREDITS  # 6090
    assert db.query(Promoter).filter_by(user_id=teacher_id).one().level == "basic"

    # 6. 继续生成：余额充足后再生成一套 PBL 课堂（40 积分）
    spec2 = {"type": "pbl", "topic": "项目式课堂"}
    r = client.post("/api/jobs", json={"spec": spec2}, headers=teacher_headers)
    assert r.status_code == 200, r.text
    job2 = r.json()
    assert job2["reserved_credits"] == 40
    assert get_balance(db, teacher_id) == 90 + PURCHASE_CREDITS - 40  # 6050
    assert poll_once(db) >= 1
    done2 = db.get(GenerationJob, job2["id"])
    assert done2.status == "succeeded"
    share_code = done2.result["share_code"]
    assert share_code

    # 7. 分享学生：免登录链接可用，且带 AIGC 合规标注
    client.cookies.clear()
    r = client.get(f"/c/{share_code}")
    assert r.status_code == 200, r.text
    assert "本内容由 AI 生成" in r.text  # AIGC_LABEL 默认开启

    # 8. 邀请同事：注册 → 绑定归属 → 明确确认 → 同事付费
    r = client.post(
        "/api/auth/register",
        json={"phone": "13811110002", "code": CODE, "referrer": teacher_invite},
    )
    assert r.status_code == 200, r.text
    colleague_id = r.json()["user_id"]
    colleague_headers = _auth(client, db, colleague_id)
    r = client.post(
        "/api/promote/bind", json={"code": teacher_invite}, headers=colleague_headers
    )
    assert r.status_code == 200, r.text
    r = client.post("/api/promote/confirm", headers=colleague_headers)
    assert r.status_code == 200 and r.json()["confirmed"] is True

    r = client.post(
        "/api/pay/orders",
        json={"sku": PERSONAL, "channel": "native"},
        headers=colleague_headers,
    )
    assert r.status_code == 200, r.text
    colleague_order_no = r.json()["order_no"]
    assert r.json()["amount_fen"] == PRICE_FEN
    _sync_payment(client, colleague_order_no, "e2e-colleague-1", PRICE_FEN)

    # 9. 佣金到账：推广员税前应计佣金入账，进入 15 天观察期
    commission = db.query(CommissionEntry).filter_by(user_id=teacher_id).one()
    assert commission.amount_fen == BASIC_FIRST_COMMISSION_FEN  # 13980
    assert commission.status == "pending"
    teacher_headers = _auth(client, db, teacher_id)
    r = client.get("/api/promote/summary", headers=teacher_headers)
    assert r.status_code == 200, r.text
    summary = r.json()
    assert summary["total_commission_fen"] == BASIC_FIRST_COMMISSION_FEN
    assert summary["withdrawable_fen"] == 0  # 观察期未满，不可提现

    # 10. 部署与文档验收（本任务交付物）
    _assert_deployment_and_docs(root)
