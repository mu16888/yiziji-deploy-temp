"""Self-owned orchestration: lecture text -> structured Courseware.

DeepSeek (or any future vendor) is only a replaceable ``LLMClient`` that follows
instructions and returns a JSON object. Every orchestration decision here is
ours: prompt templates, scene-by-scene retry/degradation, assembly and the
Courseware schema. The prompt shapes/system messages are the ones proven by
``scripts/validate_real_generation.py`` against the real API.

Pure domain module: no FastAPI, no DB, no network. Callers inject the LLM.
"""

from __future__ import annotations

from app.adapters.llm import LLMClient

SCHEMA_VERSION = "1.0"

SCENE_TYPES = frozenset({"slide", "quiz", "discussion"})
AIGC_LABEL = "AI辅助生成"

# --- validated prompts (see scripts/validate_real_generation.py) --------------

UNDERSTANDING_SYSTEM = "你是资深初中数学老师。严格只输出 JSON，不要 Markdown 围栏。"
SCENE_PLAN_SYSTEM = "你是资深老师，设计一节课的场景。严格只输出 JSON 对象，不要 Markdown 围栏。"
SCENE_SYSTEM = "你是资深老师，撰写一个教学场景。严格只输出 JSON 对象，不要 Markdown 围栏。"
QUIZ_SYSTEM = "你是资深老师，出随堂测验。严格只输出 JSON 对象，不要 Markdown 围栏。"


class CoursewareError(Exception):
    """Raised when the lecture cannot be turned into a valid Courseware."""


# --- schema validation ---------------------------------------------------------


def _as_text(value, default: str, field: str) -> str:
    """Fill a missing/None field with ``default``; reject a non-string value."""
    if value is None:
        return default
    if not isinstance(value, str):
        raise CoursewareError(f"{field} must be a string")
    return value


def _coerce_answer(value) -> int:
    """Normalize a 0-based single-choice answer to an int (0-3, never bool)."""
    if isinstance(value, bool):
        raise CoursewareError("quiz.answer must be an integer 0-3")
    if isinstance(value, int):
        answer = value
    elif isinstance(value, str) and value.strip().isdigit():
        answer = int(value.strip())
    else:
        raise CoursewareError("quiz.answer must be an integer 0-3")
    if not 0 <= answer <= 3:
        raise CoursewareError("quiz.answer must be between 0 and 3")
    return answer


def _validate_scene(raw) -> dict:
    if not isinstance(raw, dict):
        raise CoursewareError("scene must be a JSON object")
    scene = dict(raw)  # preserve vendor extras such as ``degraded``
    scene_type = scene.get("type", "slide")
    if scene_type not in SCENE_TYPES:
        raise CoursewareError(f"invalid scene type: {scene_type!r}")
    scene["type"] = scene_type
    scene["title"] = _as_text(scene.get("title"), "", "scene.title")

    bullets = scene.get("bullets", [])
    if not isinstance(bullets, list):
        raise CoursewareError("scene.bullets must be a list")
    scene["bullets"] = [_as_text(b, "", "scene.bullets[]") for b in bullets]
    scene["narration"] = _as_text(scene.get("narration"), "", "scene.narration")
    scene["visual_hint"] = _as_text(scene.get("visual_hint"), "", "scene.visual_hint")
    return scene


def _validate_question(raw) -> dict:
    if not isinstance(raw, dict):
        raise CoursewareError("quiz item must be a JSON object")
    question = dict(raw)
    question["question"] = _as_text(question.get("question"), "", "quiz.question")

    options = question.get("options", [])
    if not isinstance(options, list):
        raise CoursewareError("quiz.options must be a list")
    options = [_as_text(o, "", "quiz.options[]") for o in options]
    question["options"] = options

    answer = _coerce_answer(question.get("answer", 0))
    # A real choice question must point at one of its offered options. An empty
    # options list means the whole item was absent and was filled with defaults.
    if options and answer >= len(options):
        raise CoursewareError("quiz.answer out of range for options")
    question["answer"] = answer
    question["explain"] = _as_text(question.get("explain"), "", "quiz.explain")
    return question


def validate_courseware(data: dict) -> dict:
    """Return a normalized Courseware, filling missing fields with defaults.

    Illegal values (wrong container/field types, unknown scene type, unknown
    version, out-of-range answer) raise :class:`CoursewareError`.
    """
    if not isinstance(data, dict):
        raise CoursewareError("courseware must be a JSON object")
    out = dict(data)

    version = out.get("version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise CoursewareError(f"unsupported courseware version: {version!r}")
    out["version"] = SCHEMA_VERSION
    out["title"] = _as_text(out.get("title"), "未命名课件", "title")
    out["subject"] = _as_text(out.get("subject"), "", "subject")
    out["grade"] = _as_text(out.get("grade"), "", "grade")

    scenes = out.get("scenes", [])
    if not isinstance(scenes, list):
        raise CoursewareError("scenes must be a list")
    out["scenes"] = [_validate_scene(s) for s in scenes]

    quiz = out.get("quiz", [])
    if not isinstance(quiz, list):
        raise CoursewareError("quiz must be a list")
    out["quiz"] = [_validate_question(q) for q in quiz]

    aigc = out.get("aigc", {})
    if aigc is None:
        aigc = {}
    if not isinstance(aigc, dict):
        raise CoursewareError("aigc must be a JSON object")
    out["aigc"] = {
        "label": _as_text(aigc.get("label"), AIGC_LABEL, "aigc.label"),
        "provider": _as_text(aigc.get("provider"), "unknown", "aigc.provider"),
        "model": _as_text(aigc.get("model"), "unknown", "aigc.model"),
    }
    return out


# --- prompt builders -----------------------------------------------------------


def _understanding_prompt(text: str) -> str:
    return (
        "根据下面的讲义，提取主题、学科、年级、核心知识点列表，输出 JSON：\n"
        '{"title": str, "subject": str, "grade": str, "points": [str, ...]}\n\n'
        f"讲义：\n{text}"
    )


def _outline_prompt(text: str, points: list, max_scenes: int) -> str:
    return (
        f"根据讲义和知识点，设计不超过 {max_scenes} 个教学场景的大纲，"
        "按顺序覆盖：引入→概念→步骤→例题→易错点。\n"
        '只输出一个 JSON 对象：{"scenes": [{"title": str, "bullets": [str, ...]}, ...]}\n'
        f"知识点：{points}\n\n讲义：\n{text}"
    )


def _scene_prompt(plan: dict, text: str) -> str:
    return (
        "把下面这个场景写成完整的课堂场景。\n"
        '输出一个 JSON 对象：{"type": "slide", "title": str, "bullets": [str,...], '
        '"narration": str, "visual_hint": str}\n'
        f"场景标题：{plan['title']}\n场景要点：{plan['bullets']}\n\n讲义：\n{text}"
    )


def _quiz_prompt(text: str) -> str:
    return (
        "根据讲义出 3 道单选题，每题考察真实知识点。\n"
        '输出一个 JSON 对象：{"quiz": [题目1, 题目2, ...]}\n'
        '每道题是：{"question": str, "options": [str,str,str,str], "answer": 0-3, "explain": str}\n'
        f"讲义：\n{text}"
    )


# --- orchestration helpers ----------------------------------------------------


def _extract_understanding(obj) -> dict:
    if not isinstance(obj, dict):
        raise CoursewareError("understanding phase did not return an object")
    points = obj.get("points", [])
    if not isinstance(points, list):
        points = []
    return {
        "title": obj.get("title") if isinstance(obj.get("title"), str) else "",
        "subject": obj.get("subject") if isinstance(obj.get("subject"), str) else "",
        "grade": obj.get("grade") if isinstance(obj.get("grade"), str) else "",
        "points": [str(p) for p in points if p is not None],
    }


def _extract_scene_plans(obj) -> list[dict]:
    if not isinstance(obj, dict):
        raise CoursewareError("outline phase did not return an object")
    raw_scenes = obj.get("scenes", [])
    if not isinstance(raw_scenes, list):
        raise CoursewareError("outline.scenes must be a list")
    plans = []
    for raw in raw_scenes:
        if not isinstance(raw, dict):
            continue
        title = raw.get("title", "")
        bullets = raw.get("bullets", [])
        plans.append(
            {
                "title": str(title) if title is not None else "",
                "bullets": [str(b) for b in bullets] if isinstance(bullets, list) else [],
            }
        )
    if not plans:
        raise CoursewareError("outline contained no usable scenes")
    return plans


def _aigc_metadata(llm: LLMClient) -> dict:
    """Record the replaceable backend behind the generated result."""
    provider = getattr(llm, "provider", None)
    model = getattr(llm, "model", None)
    if provider is None:
        owner = f"{type(llm).__module__}.{type(llm).__name__}".lower()
        provider = "deepseek" if "deepseek" in owner else "llm"
    if model is None:
        model = "unknown"
    return {"label": AIGC_LABEL, "provider": str(provider), "model": str(model)}


def _generate_scene(llm: LLMClient, text: str, plan: dict, *, fail_fast: bool) -> dict:
    """Generate one scene; retry once, then degrade (unless fail_fast)."""
    prompt = _scene_prompt(plan, text)
    last_error: Exception | None = None
    for _ in range(2):  # initial attempt + exactly one retry
        try:
            obj = llm.complete_json(SCENE_SYSTEM, prompt)
            if not isinstance(obj, dict):
                raise CoursewareError("scene phase did not return an object")
            inner = obj.get("scene")
            if isinstance(inner, dict):
                obj = inner
            return validate_courseware({"scenes": [obj]})["scenes"][0]
        except Exception as exc:  # noqa: BLE001 - any scene failure is degradable
            last_error = exc

    if fail_fast:
        raise CoursewareError(f"scene {plan['title']!r} failed: {last_error}")
    return {
        "type": "slide",
        "title": "（生成失败）",
        "bullets": [],
        "narration": "",
        "visual_hint": "",
        "degraded": True,
    }


# --- public entry point --------------------------------------------------------


def build_courseware(
    text: str,
    llm: LLMClient,
    *,
    subject: str = "",
    grade: str = "",
    max_scenes: int = 12,
    fail_fast: bool = False,
) -> dict:
    """Run the self-owned orchestration and return a validated Courseware.

    A scene failure is retried once, then replaced by a degraded placeholder so
    the rest of the lesson still ships. A failure in the understanding, outline,
    quiz or assembly phase is fatal and raises :class:`CoursewareError` so the
    upper layer can fail the job and refund the reserved credits.
    """
    if max_scenes < 1:
        raise CoursewareError("max_scenes must be >= 1")

    try:
        understanding = _extract_understanding(
            llm.complete_json(UNDERSTANDING_SYSTEM, _understanding_prompt(text))
        )
    except CoursewareError:
        raise
    except Exception as exc:
        raise CoursewareError(f"understanding phase failed: {exc}") from exc

    try:
        plans = _extract_scene_plans(
            llm.complete_json(
                SCENE_PLAN_SYSTEM,
                _outline_prompt(text, understanding["points"], max_scenes),
            )
        )
    except CoursewareError:
        raise
    except Exception as exc:
        raise CoursewareError(f"outline phase failed: {exc}") from exc

    scenes = [
        _generate_scene(llm, text, plan, fail_fast=fail_fast)
        for plan in plans[:max_scenes]
    ]

    try:
        quiz_obj = llm.complete_json(QUIZ_SYSTEM, _quiz_prompt(text))
        if not isinstance(quiz_obj, dict):
            raise CoursewareError("quiz phase did not return an object")
        quiz = quiz_obj.get("quiz", [])
        if not isinstance(quiz, list):
            raise CoursewareError("quiz.quiz must be a list")
    except CoursewareError:
        raise
    except Exception as exc:
        raise CoursewareError(f"quiz phase failed: {exc}") from exc

    draft = {
        "version": SCHEMA_VERSION,
        "title": understanding["title"] or "未命名课件",
        "subject": subject or understanding["subject"],
        "grade": grade or understanding["grade"],
        "scenes": scenes,
        "quiz": quiz,
        "aigc": _aigc_metadata(llm),
    }
    return validate_courseware(draft)
