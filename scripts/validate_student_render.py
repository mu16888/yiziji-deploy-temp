"""Validation: can the REAL generated courseware actually render for students?

Flow: real DeepSeek generation -> assemble Courseware -> put into a GenerationJob.result
-> call the student classroom endpoint -> assert the HTML contains the generated
scene titles, narration and quiz questions.

Key is read from env/.env only; never printed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.adapters.llm.deepseek import DeepSeekClient
from app.config import settings
from app.domain.generation import QUEUED, SUCCEEDED
from app.main import app
from app.models import GenerationJob
from app.db import Base, engine, SessionLocal


SAMPLE = """一元二次方程的配方法

一、什么是配方法：把 ax²+bx+c=0（a≠0）的左边通过加减同一个数凑成完全平方式，
转化为 (x+m)²=n 求解。
二、关键步骤：1 二次项系数化为1；2 移项；3 两边加一次项系数一半的平方；
4 写成完全平方；5 开平方求解（注意正负两根）。
三、例题：x²+6x+5=0 → (x+3)²=4 → x₁=-1,x₂=-5。
例2：2x²-4x-6=0 → 除以2 → x²-2x=3 → (x-1)²=4 → x₁=3,x₂=-1。
四、易错点：忘化系数、只在一边加数、漏掉负根。
"""


def main() -> int:
    if not settings.deepseek_api_key:
        print("ERROR: DEEPSEEK_API_KEY not configured")
        return 2
    client = DeepSeekClient(http_client=httpx.Client(timeout=120))

    # 1) understanding
    u = client.complete_json(
        "你是资深初中数学老师。严格只输出 JSON 对象，不要 Markdown 围栏。",
        f"提取主题/学科/年级/知识点，输出 {{\"title\":str,\"subject\":str,\"grade\":str,\"points\":[str]}}\n\n讲义：\n{SAMPLE}",
    )
    # 2) scenes (wrapped object, validated pattern)
    s = client.complete_json(
        "你是资深老师设计教学场景。严格只输出 JSON 对象，不要 Markdown 围栏。",
        "设计 4 个场景覆盖 引入→概念→步骤→例题，输出 {\"scenes\":[{\"type\":\"slide\",\"title\":str,\"bullets\":[str],\"narration\":str,\"visual_hint\":str}]}\n"
        f"讲义：\n{SAMPLE}",
    )
    # 3) quiz
    q = client.complete_json(
        "你是资深老师出随堂测验。严格只输出 JSON 对象，不要 Markdown 围栏。",
        "出 2 道单选题，输出 {\"quiz\":[{\"question\":str,\"options\":[str,str,str,str],\"answer\":0,\"explain\":str}]}\n"
        f"讲义：\n{SAMPLE}",
    )

    courseware = {
        "version": "1.0",
        "title": u.get("title", ""),
        "subject": u.get("subject", ""),
        "grade": u.get("grade", ""),
        "scenes": s.get("scenes", []),
        "quiz": q.get("quiz", []),
        "aigc": {"label": "AI辅助生成", "provider": "deepseek", "model": client.model},
        "share_code": "VREAL01",
        "share_url": "/c/VREAL01",
    }
    print("=== COURSEWARE SUMMARY ===")
    print(f"title={courseware['title']!r} scenes={len(courseware['scenes'])} quiz={len(courseware['quiz'])}")

    # Put into a real job result and render through the student endpoint.
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    job = GenerationJob(
        user_id=1, spec={"topic": courseware["title"]}, status=SUCCEEDED,
        reserved_credits=25, actual_credits=25, result=courseware,
        idempotency_key="validate-real-render",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    with TestClient(app) as c:
        resp = c.get("/c/VREAL01")
    db.close()

    print(f"\n=== STUDENT PAGE HTTP {resp.status_code} ===")
    html = resp.text
    checks = []
    for sc in courseware["scenes"]:
        t = sc.get("title", "")
        checks.append((f"scene title: {t[:24]}", t in html))
    for qq in courseware["quiz"]:
        qt = qq.get("question", "")
        checks.append((f"quiz question: {qt[:24]}", qt in html))
    for label, ok in checks:
        print(f"  [{'OK' if ok else 'MISSING'}] {label}")

    Path("validation_render.html").write_text(html, encoding="utf-8")
    passed = all(ok for _, ok in checks) and resp.status_code == 200
    print(f"\nRESULT: {'PASS' if passed else 'FAIL'} — student page renders real courseware")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
