"""Generation job endpoints: estimate cost, submit a job, read its result."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.adapters.generation import get_adapter
from app.db import get_db
from app.domain.generation import GenerationError, create_job, get_job
from app.domain.ledger import InsufficientCredits
from app.services.auth import get_current_user

router = APIRouter(prefix="/api", tags=["jobs"])


class SpecIn(BaseModel):
    spec: dict


def _serialize(job) -> dict:
    result = job.result or {}
    share_code = result.get("share_code")
    return {
        "id": job.id,
        "user_id": job.user_id,
        "spec": job.spec,
        "status": job.status,
        "external_id": job.external_id,
        "reserved_credits": job.reserved_credits,
        "actual_credits": job.actual_credits,
        "result": result,
        "share_code": share_code,
        "share_url": result.get("share_url") or (f"/c/{share_code}" if share_code else None),
    }


@router.post("/estimate")
def estimate(payload: SpecIn, current_user=Depends(get_current_user)):
    return {"credits": get_adapter().estimate(payload.spec)}


@router.post("/jobs")
def create(
    payload: SpecIn,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        job = create_job(db, user_id=current_user.id, spec=payload.spec)
    except InsufficientCredits:
        raise HTTPException(status_code=400, detail="insufficient credits")
    except GenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _serialize(job)


@router.get("/jobs/{job_id}")
def read(
    job_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        job = get_job(db, job_id)
    except GenerationError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="job not found")
    return _serialize(job)
