"""PPTX download endpoint.

Export is free: generation already charges credits by style tier at create_job,
so recharging for the download would be double-billing. This endpoint only
authorises the request, reads the finished Courseware and streams the file.
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.domain.identity import role_satisfies
from app.models import GenerationJob
from app.services.auth import get_current_user
from app.services.export import build_pptx

router = APIRouter(prefix="/api/export", tags=["export"])

PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)


def _has_courseware(result) -> bool:
    # A finished result is exportable even when it carries no scenes (e.g. the
    # mock adapter's artifact): the builder simply emits a cover-only deck.
    return (
        isinstance(result, dict)
        and bool(result)
        and result.get("_status") not in ("failed", "cancelled")
    )


@router.get("/{job_id}.pptx")
def export_pptx(
    job_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = db.get(GenerationJob, int(job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.user_id != current_user.id and not role_satisfies(
        current_user.role, "admin"
    ):
        raise HTTPException(status_code=403, detail="not allowed to export this job")
    if not _has_courseware(job.result):
        raise HTTPException(status_code=409, detail="courseware is not available")

    content = build_pptx(job.result)
    return Response(
        content=content,
        media_type=PPTX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="courseware.pptx"'},
    )
