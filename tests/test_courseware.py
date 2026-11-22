"""N3: self-owned orchestration turns lecture text into a Courseware.

All LLM traffic here is a scripted FAKE: no real API call and no key. The fake
returns queued JSON objects (or raises queued exceptions), so each orchestration
phase and every degradation branch is deterministic.

The Review Focus contracts (schema valid / per-scene degradation / build failure
propagation) are covered by the three ``test_*`` functions the brief names.
"""
from __future__ import annotations

import inspect

import pytest

from app.adapters.llm import LLMClient
from app.domain.courseware import (
    SCHEMA_VERSION,
    CoursewareError,
    build_courseware,
    validate_courseware,
)


# --- scripted fake LLM ---------------------------------------------------------


class FakeLLM:
    """Queued responses; a queued BaseException is raised instead of returned."""

    def __init__(self, steps):
        self._steps = list(steps)
        self.calls = []  # (system, user, temperature)

    def complete_json(self, system, user, *, temperature=0.7):
        self.calls.append((system, user, temperature))
        if not self._steps:
            raise AssertionError("FakeLLM ran out of scripted responses")
        step = self._steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    def __str__(self):
        return "fake"


def _understanding():
    return {
        "title": "函数的概念",
        "subject": "数学",
        "grade": "高一",
        "points": ["函数定义", "定义域", "值域"],
    }


def _outline(n=3):
    return {
        "scenes": [
            {"title": f"场景{i}", "bullets": [f"要点{i}"]} for i in range(1, n + 1)
        ]
    }


def _scene(i):
    return {
        "type": "slide",
        "title": f"场景{i}",
        "bullets": [f"要点{i}"],
        "narration": f"讲稿{i}",
        "visual_hint": f"图示{i}",
    }


def _quiz():
    return {
        "quiz": [
            {
                "question": "函数定义？",
                "options": ["A", "B", "C", "D"],
                "answer": 0,
                "explain": "因为A。",
            },
            {
                "question": "定义域？",
                "options": ["A", "B", "C", "D"],
                "answer": 1,
                "explain": "因为B。",
            },
        ]
    }


def _happy_steps(n=3):
    return [_understanding(), _outline(n)] + [_scene(i) for i in range(1, n + 1)] + [
        _quiz()
    ]


# --- public interface ----------------------------------------------------------


def test_public_interface():
    assert SCHEMA_VERSION == "1.0"
    assert issubclass(CoursewareError, Exception)
    assert callable(validate_courseware) and callable(build_courseware)
    assert isinstance(FakeLLM([]), LLMClient)  # fake honours the N1 protocol


# --- Review Focus: schema ------------------------------------------------------


def test_courseware_schema_valid():
    llm = FakeLLM(_happy_steps(3))
    cw = build_courseware("函数的概念", llm)

    assert cw["version"] == "1.0" and cw["title"]
    assert all(
        s["type"] in {"slide", "quiz", "discussion"} and s["title"]
        for s in cw["scenes"]
    )
    assert all("question" in q and "options" in q and "answer" in q for q in cw["quiz"])

    # The validated scene/quiz field shapes survive all the way to the result.
    assert cw["scenes"][0]["bullets"] == ["要点1"]
    assert cw["scenes"][0]["narration"] == "讲稿1"
    assert cw["scenes"][0]["visual_hint"] == "图示1"
    assert cw["aigc"]["label"] == "AI辅助生成"
    assert validate_courseware(cw) == cw  # validating twice is idempotent


# --- Review Focus: per-scene degradation ---------------------------------------


def test_single_scene_failure_degrades():
    # 3rd scene fails on both the initial call and the single retry.
    steps = [
        _understanding(),
        _outline(3),
        _scene(1),
        _scene(2),
        RuntimeError("scene 3 attempt 1"),
        RuntimeError("scene 3 attempt 2"),
        _quiz(),
    ]
    llm = FakeLLM(steps)
    cw = build_courseware("函数的概念", llm)

    assert len(cw["scenes"]) == 3
    assert cw["scenes"][2]["degraded"] is True
    assert cw["scenes"][2]["title"] == "（生成失败）"
    assert cw["scenes"][2]["type"] == "slide"
    # Earlier and later scenes/quiz are unaffected; overall build succeeds.
    assert cw["scenes"][0]["title"] == "场景1"
    assert cw["scenes"][1]["title"] == "场景2"
    assert len(cw["quiz"]) == 2
    assert len(llm.calls) == 7  # 1 retry for the failing scene only


# --- Review Focus: whole-build failure propagates ------------------------------


def test_build_failure_propagates():
    # Outline (whole-build) failure -> CoursewareError -> upper layer refunds.
    llm = FakeLLM([_understanding(), RuntimeError("outline down")])
    with pytest.raises(CoursewareError):
        build_courseware("函数的概念", llm)


def test_understanding_failure_propagates():
    with pytest.raises(CoursewareError):
        build_courseware("函数的概念", FakeLLM([RuntimeError("no key")]))


def test_quiz_failure_propagates():
    steps = [_understanding(), _outline(1), _scene(1), RuntimeError("quiz down")]
    with pytest.raises(CoursewareError):
        build_courseware("函数的概念", FakeLLM(steps))


# --- retry / fail-fast ---------------------------------------------------------


def test_scene_retry_succeeds_without_degradation():
    steps = [_understanding(), _outline(1), RuntimeError("transient"), _scene(1), _quiz()]
    cw = build_courseware("函数的概念", FakeLLM(steps))
    assert cw["scenes"][0]["title"] == "场景1"
    assert "degraded" not in cw["scenes"][0]


def test_fail_fast_raises_on_scene_failure():
    steps = [_understanding(), _outline(1), RuntimeError("bad"), RuntimeError("bad"), _quiz()]
    with pytest.raises(CoursewareError):
        build_courseware("函数的概念", FakeLLM(steps), fail_fast=True)


def test_max_scenes_caps_generated_scenes():
    llm = FakeLLM([_understanding(), _outline(5), _scene(1), _scene(2), _quiz()])
    cw = build_courseware("函数的概念", llm, max_scenes=2)
    assert len(cw["scenes"]) == 2
    assert len(llm.calls) == 5


def test_subject_and_grade_override_understanding():
    cw = build_courseware(
        "函数的概念", FakeLLM(_happy_steps(1)), subject="物理", grade="初二"
    )
    assert cw["subject"] == "物理"
    assert cw["grade"] == "初二"


# --- validate_courseware -------------------------------------------------------


def test_validate_courseware_fills_defaults():
    cw = validate_courseware({"title": "x", "scenes": [{"title": "s"}]})
    assert cw["version"] == "1.0"
    assert cw["subject"] == "" and cw["grade"] == ""
    assert cw["scenes"][0]["type"] == "slide"
    assert cw["scenes"][0]["bullets"] == []
    assert cw["scenes"][0]["narration"] == ""
    assert cw["scenes"][0]["visual_hint"] == ""
    assert cw["quiz"] == []
    assert cw["aigc"]["label"] == "AI辅助生成"


def test_validate_courseware_rejects_illegal():
    with pytest.raises(CoursewareError):
        validate_courseware("not a dict")
    with pytest.raises(CoursewareError):
        validate_courseware({"version": "9.9"})
    with pytest.raises(CoursewareError):
        validate_courseware({"scenes": "nope"})
    with pytest.raises(CoursewareError):
        validate_courseware({"scenes": [{"type": "video"}]})
    with pytest.raises(CoursewareError):
        validate_courseware({"quiz": [{"options": ["a", "b"], "answer": 5}]})
    with pytest.raises(CoursewareError):
        validate_courseware({"quiz": [{"options": ["a", "b"], "answer": True}]})


def test_answer_normalized_to_zero_based_int():
    cw = validate_courseware(
        {"quiz": [{"question": "q", "options": ["a", "b", "c", "d"], "answer": "2"}]}
    )
    assert cw["quiz"][0]["answer"] == 2
    assert isinstance(cw["quiz"][0]["answer"], int)


# --- validated prompt shapes + purity -----------------------------------------


def test_prompts_use_validated_json_object_shapes():
    llm = FakeLLM(_happy_steps(2))
    build_courseware("函数的概念", llm)
    systems = [c[0] for c in llm.calls]
    users = [c[1] for c in llm.calls]

    # The exact system messages proven by scripts/validate_real_generation.py.
    assert systems[0] == "你是资深初中数学老师。严格只输出 JSON，不要 Markdown 围栏。"
    assert systems[-1] == "你是资深老师，出随堂测验。严格只输出 JSON 对象，不要 Markdown 围栏。"
    # Every step asks for a JSON object and wraps lists, never a bare array.
    assert '{"title": str, "subject": str, "grade": str, "points": [str, ...]}' in users[0]
    assert '{"scenes":' in users[1]
    assert '{"type": "slide"' in users[2]
    assert (
        '{"question": str, "options": [str,str,str,str], "answer": 0-3, "explain": str}'
        in users[-1]
    )
    assert "JSON 数组" not in " ".join(users)


def test_aigc_metadata_reads_llm_attributes():
    class AttrLLM(FakeLLM):
        provider = "deepseek"
        model = "deepseek-chat"

    cw = build_courseware("函数的概念", AttrLLM(_happy_steps(1)))
    assert cw["aigc"] == {
        "label": "AI辅助生成",
        "provider": "deepseek",
        "model": "deepseek-chat",
    }


def test_domain_module_has_no_fastapi():
    import app.domain.courseware as module

    assert "fastapi" not in inspect.getsource(module)
