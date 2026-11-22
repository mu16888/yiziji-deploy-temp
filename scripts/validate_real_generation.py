"""One-off REAL validation of the SHIPPED generation pipeline with DeepSeek.

This script deliberately calls the *production* entry point
``app.domain.courseware.build_courseware`` with a real ``DeepSeekClient``: it does
NOT hand-roll a parallel prompt pipeline. What runs in production is what gets
validated here, so the prompt shapes, retry/degradation and assembly are covered
by the real API call. The resulting Courseware is printed for human judgement.

The API key is read from the environment / .env via app.config.settings; it is
NEVER printed, logged, or embedded in this file.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Windows consoles default to GBK and cannot print the superscript/dash
# characters that real Chinese math content contains; force UTF-8 output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import httpx

from app.adapters.llm.deepseek import DeepSeekClient
from app.config import settings
from app.domain.courseware import build_courseware


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

    # This is the exact shipped orchestration (no hand-rolled parallel pipeline).
    courseware = build_courseware(SAMPLE_LECTURE, client)
    print("=== SHIPPED build_courseware OUTPUT ===")
    print(json.dumps(courseware, ensure_ascii=False, indent=2))

    # Save full output for inspection (no key in it).
    path = Path("validation_output.json")
    path.write_text(json.dumps(courseware, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved full output to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
