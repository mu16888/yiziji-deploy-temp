"""One-off REAL validation of the core hypothesis with DeepSeek.

Runs: sample lecture -> ingestion -> self-owned orchestration -> REAL DeepSeek
-> Courseware, then prints the actual generated content for human judgement.

The API key is read from the environment / .env via app.config.settings;
it is NEVER printed, logged, or embedded in this file.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

from app.adapters.llm.deepseek import DeepSeekClient
from app.config import settings


SAMPLE_LECTURE = """一元二次方程的配方法

一、什么是配方法
配方法就是把一元二次方程 ax²+bx+c=0（a≠0）的左边，通过加上并减去同一个适当的数，
凑成一个完全平方式，从而把方程转化为 (x+m)²=n 的形式来求解。

二、配方的关键步骤
1. 把二次项系数化为 1：方程两边同除以 a。
2. 移项：把常数项移到等号右边。
3. 配方：两边同加上一次项系数一半的平方，即 (b/2a)²。
4. 写成完全平方：左边写成 (x + b/2a)²。
5. 开平方求解：当右边非负时，直接开平方得到两个根。

三、例题
例1：解方程 x² + 6x + 5 = 0。
移项得 x² + 6x = -5。两边加 9：x² + 6x + 9 = 4，即 (x+3)² = 4。
所以 x+3 = ±2，解得 x₁ = -1，x₂ = -5。

例2：解方程 2x² - 4x - 6 = 0。
两边除以 2：x² - 2x - 3 = 0。移项：x² - 2x = 3。两边加 1：(x-1)² = 4。
所以 x-1 = ±2，解得 x₁ = 3，x₂ = -1。

四、常见错误
错误1：忘记先把二次项系数化为 1 就直接配方。
错误2：配方时只在一边加数，破坏等式。
错误3：开平方时漏掉负根。
"""


def main() -> int:
    if not settings.deepseek_api_key:
        print("ERROR: DEEPSEEK_API_KEY not configured")
        return 2

    # Key is read from settings; only the client sees it. Never print it.
    client = DeepSeekClient(http_client=httpx.Client(timeout=120))

    # --- Minimal self-owned orchestration (N3 is not built yet) -------------
    # Step 1: outline
    outline = client.complete_json(
        "你是资深初中数学老师。严格只输出 JSON，不要 Markdown 围栏。",
        "根据下面的讲义，提取主题、学科、年级、核心知识点列表，输出 JSON：\n"
        '{"title": str, "subject": str, "grade": str, "points": [str, ...]}\n\n'
        f"讲义：\n{SAMPLE_LECTURE}",
    )
    print("=== STEP 1: UNDERSTANDING ===")
    print(json.dumps(outline, ensure_ascii=False, indent=2))

    # Step 2: scenes
    scenes_obj = client.complete_json(
        "你是资深老师，设计一节课的场景。严格只输出 JSON 对象，不要 Markdown 围栏。",
        "根据讲义和知识点，设计 5 个教学场景，按顺序覆盖：引入→概念→步骤→例题→易错点。\n"
        '输出一个 JSON 对象：{"scenes": [场景1, 场景2, ...]}\n'
        '每个场景是：{"type": "slide", "title": str, "bullets": [str,...], "narration": str, "visual_hint": str}\n'
        f"知识点：{outline.get('points')}\n\n讲义：\n{SAMPLE_LECTURE}",
    )
    scenes = scenes_obj.get("scenes", [])
    print("\n=== STEP 2: SCENES ===")
    print(json.dumps(scenes, ensure_ascii=False, indent=2))

    # Step 3: quiz
    quiz_obj = client.complete_json(
        "你是资深老师，出随堂测验。严格只输出 JSON 对象，不要 Markdown 围栏。",
        "根据讲义出 3 道单选题，每题考察真实知识点。\n"
        '输出一个 JSON 对象：{"quiz": [题目1, 题目2, ...]}\n'
        '每道题是：{"question": str, "options": [str,str,str,str], "answer": 0-3, "explain": str}\n'
        f"讲义：\n{SAMPLE_LECTURE}",
    )
    quiz = quiz_obj.get("quiz", [])
    print("\n=== STEP 3: QUIZ ===")
    print(json.dumps(quiz, ensure_ascii=False, indent=2))

    # Save full output for inspection (no key in it).
    out = {"understanding": outline, "scenes": scenes, "quiz": quiz}
    path = Path("validation_output.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved full output to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
