"""Promotion-centre endpoints: invite binding, commissions and withdrawals."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Agent, Promoter, Withdrawal
from app.services.affiliate import (
    WithdrawalError,
    bind_referrer,
    confirm_attribution,
    request_withdrawal,
    total_commission,
    withdrawable_balance,
)
from app.services.auth import get_current_user

router = APIRouter(prefix="/api/promote", tags=["promote"])


class BindIn(BaseModel):
    code: str


class WithdrawIn(BaseModel):
    amount_fen: int


@router.get("/summary")
def summary(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    promoter = db.query(Promoter).filter_by(user_id=current_user.id).first()
    agent = db.query(Agent).filter_by(user_id=current_user.id).first()
    return {
        "invite_code": current_user.invite_code,
        "role": current_user.role,
        "promoter_level": promoter.level if promoter is not None else None,
        "invited_count": promoter.invited_count if promoter is not None else 0,
        "total_commission_fen": total_commission(db, user_id=current_user.id),
        "withdrawable_fen": withdrawable_balance(db, user_id=current_user.id),
        "agent_fund_fen": agent.fund_fen if agent is not None else 0,
        "agent_status": agent.status if agent is not None else None,
    }


@router.post("/bind")
def bind(body: BindIn, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        attribution = bind_referrer(db, user_id=current_user.id, code=body.code)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return {
        "inviter_id": attribution.inviter_id,
        "code": attribution.code,
        "candidate": True,
    }


@router.post("/confirm")
def confirm(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        attribution = confirm_attribution(db, user_id=current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return {"confirmed": attribution.confirmed_at is not None}


@router.post("/withdraw")
def withdraw(
    body: WithdrawIn, current_user=Depends(get_current_user), db: Session = Depends(get_db)
):
    try:
        withdrawal = request_withdrawal(
            db, user_id=current_user.id, amount_fen=body.amount_fen
        )
    except WithdrawalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return _withdrawal_view(withdrawal)


@router.get("/withdrawals")
def withdrawals(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(Withdrawal)
        .filter_by(user_id=current_user.id)
        .order_by(Withdrawal.id)
        .all()
    )
    return [_withdrawal_view(row) for row in rows]


def _withdrawal_view(withdrawal: Withdrawal) -> dict:
    return {
        "id": withdrawal.id,
        "amount_fen": withdrawal.amount_fen,
        "status": withdrawal.status,
        "reviewed_at": withdrawal.reviewed_at,
    }
