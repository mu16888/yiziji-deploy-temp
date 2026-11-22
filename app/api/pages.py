"""Product HTML pages (§12.3) and the no-login student classroom route.

Every teacher page resolves the caller from the auth cookie and redirects
anonymous visitors to /login; the student link under /c/ is deliberately public
and is validated only by its share code + TTL. Prices shown on pages come from
the server catalogue, never from template literals.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.adapters.generation import get_adapter
from app.config import settings
from app.db import get_db
from app.models import Agent, GenerationJob, Promoter, User
from app.services.affiliate import total_commission, withdrawable_balance
from app.services.auth import get_current_user
from app.services.catalog import list_products
from app.services.compliance import mark_aigc
from app.services.credits import get_balance
from app.services.sharing import (
    find_job_by_share_code,
    is_share_expired,
    share_expires_at,
)

router = APIRouter(tags=["pages"])
_templates = Jinja2Templates(directory="templates")

_ANON_USER = User(id=0, role="user")


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """Resolve the web caller, returning None instead of raising for anon pages."""
    try:
        return get_current_user(request, db)
    except HTTPException:
        return None


def _job_view(job: GenerationJob) -> dict:
    result = job.result or {}
    share_code = result.get("share_code")
    return {
        "id": job.id,
        "spec": job.spec or {},
        "status": job.status,
        "reserved_credits": job.reserved_credits,
        "actual_credits": job.actual_credits,
        "result": result,
        "share_code": share_code,
        "share_url": result.get("share_url")
        or (f"/c/{share_code}" if share_code else None),
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


def _owned_job(db: Session, job_id: int, user: User) -> GenerationJob:
    job = db.get(GenerationJob, int(job_id))
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="classroom not found")
    return job


def _render(request: Request, name: str, **context):
    return _templates.TemplateResponse(request=request, name=name, context=context)


# --- public pages ------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
def landing(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    return _render(
        request,
        "index.html",
        products=list_products(db, user or _ANON_USER),
        user=user,
    )


# --- teacher pages (login required) -----------------------------------------


@router.get("/onboarding", response_class=HTMLResponse)
def onboarding(
    request: Request,
    user: User | None = Depends(optional_user),
):
    if user is None:
        return RedirectResponse("/login", status_code=302)
    return _render(request, "onboarding.html", user=user)


@router.get("/workbench", response_class=HTMLResponse)
def workbench(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    if user is None:
        return RedirectResponse("/login", status_code=302)
    jobs = (
        db.query(GenerationJob)
        .filter_by(user_id=user.id)
        .order_by(GenerationJob.id.desc())
        .limit(50)
        .all()
    )
    return _render(
        request,
        "workbench.html",
        user=user,
        balance=get_balance(db, user.id),
        jobs=[_job_view(job) for job in jobs],
    )


@router.get("/generate", response_class=HTMLResponse)
def generate(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    if user is None:
        return RedirectResponse("/login", status_code=302)
    estimate = get_adapter().estimate({"type": "basic"})
    return _render(
        request,
        "generate.html",
        user=user,
        estimate=estimate,
        balance=get_balance(db, user.id),
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_progress(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    if user is None:
        return RedirectResponse("/login", status_code=302)
    return _render(request, "job.html", user=user, job=_job_view(_owned_job(db, job_id, user)))


@router.get("/classroom/{job_id}/edit", response_class=HTMLResponse)
def classroom_edit(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    if user is None:
        return RedirectResponse("/login", status_code=302)
    return _render(
        request, "classroom_edit.html", user=user, job=_job_view(_owned_job(db, job_id, user))
    )


@router.get("/classroom/{job_id}/share", response_class=HTMLResponse)
def classroom_share(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    if user is None:
        return RedirectResponse("/login", status_code=302)
    job = _owned_job(db, job_id, user)
    return _render(
        request,
        "classroom_share.html",
        user=user,
        job=_job_view(job),
        expires_at=share_expires_at(job).isoformat(),
        ttl_days=int(settings.share_link_ttl_days),
    )


@router.get("/library", response_class=HTMLResponse)
def library(
    request: Request,
    user: User | None = Depends(optional_user),
):
    if user is None:
        return RedirectResponse("/login", status_code=302)
    return _render(request, "library.html", user=user)


@router.get("/history", response_class=HTMLResponse)
def history(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    if user is None:
        return RedirectResponse("/login", status_code=302)
    jobs = (
        db.query(GenerationJob)
        .filter_by(user_id=user.id)
        .order_by(GenerationJob.id.desc())
        .limit(100)
        .all()
    )
    return _render(request, "history.html", user=user, jobs=[_job_view(j) for j in jobs])


@router.get("/promote", response_class=HTMLResponse)
def promote(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    if user is None:
        return RedirectResponse("/login", status_code=302)
    promoter = db.query(Promoter).filter_by(user_id=user.id).first()
    agent = db.query(Agent).filter_by(user_id=user.id).first()
    products = list_products(db, user)
    agent_product = next((p for p in products if p.type == "agent"), None)
    return _render(
        request,
        "promote.html",
        user=user,
        invite_code=user.invite_code,
        promoter_level=promoter.level if promoter else None,
        invited_count=promoter.invited_count if promoter else 0,
        total_commission_yuan=f"{total_commission(db, user_id=user.id) / 100:.2f}",
        withdrawable_yuan=f"{withdrawable_balance(db, user_id=user.id) / 100:.2f}",
        agent_status=agent.status if agent else None,
        agent_price_yuan=agent_product.amount_yuan if agent_product else "",
        min_withdraw_yuan=f"{settings.min_withdraw_fen / 100:.2f}",
    )


@router.get("/org", response_class=HTMLResponse)
def org(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    if user is None:
        return RedirectResponse("/login", status_code=302)
    members = (
        db.query(User).filter_by(org_id=user.org_id).order_by(User.id).all()
        if user.org_id
        else []
    )
    member_views = [
        {
            "id": m.id,
            "display_name": m.display_name,
            "phone": m.phone,
            "role": m.role,
            "balance": get_balance(db, m.id),
        }
        for m in members
    ]
    return _render(
        request,
        "org.html",
        user=user,
        org_id=user.org_id,
        members=member_views,
        seats=len(members),
        shared_balance=get_balance(db, user.id),
    )


# --- student classroom (no login) -------------------------------------------


@router.get("/c/{share_code}", response_class=HTMLResponse)
def student_classroom(
    share_code: str,
    request: Request,
    db: Session = Depends(get_db),
):
    job = find_job_by_share_code(db, share_code)
    if job is None:
        raise HTTPException(status_code=404, detail="classroom not found")
    if is_share_expired(job):
        raise HTTPException(status_code=410, detail="share link expired")

    result = mark_aigc(dict(job.result or {}))
    spec = job.spec or {}
    return _render(
        request,
        "student_classroom.html",
        title=spec.get("topic") or "互动课堂",
        share_code=share_code,
        expires_at=share_expires_at(job).isoformat(),
        result=result,
        scenes=result.get("scenes") or [],
        quiz=result.get("quiz") or [],
        aigc_label=bool(settings.aigc_label),
    )
