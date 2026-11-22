"""Credit balance endpoints (stub for Task 3; Task 4 replaces the service)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.auth import get_current_user
from app.services.credits import get_balance

router = APIRouter(prefix="/api/credits", tags=["credits"])


@router.get("/balance")
def balance(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    return {
        "user_id": current_user.id,
        "balance": get_balance(db, current_user.id),
    }
