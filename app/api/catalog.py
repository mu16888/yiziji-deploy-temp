"""Catalogue read endpoints and the public pricing page.

Only the server computes prices; these endpoints expose the computed values and
the Jinja2 page renders them verbatim (no price literals in the template).
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.services.auth import get_current_user
from app.services.catalog import ProductNotFound, get_price, list_products

router = APIRouter(prefix="/api/catalog", tags=["catalog"])
pages_router = APIRouter(tags=["pages"])
_templates = Jinja2Templates(directory="templates")


@router.get("/products")
def products(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    return list_products(db, current_user)


@router.get("/price/{sku}")
def price(sku: str, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        return get_price(db, sku, current_user)
    except ProductNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@pages_router.get("/pricing", response_class=HTMLResponse)
def pricing_page(request: Request, db: Session = Depends(get_db)):
    """Public page: personalised prices when signed in, list prices otherwise."""
    try:
        user = get_current_user(request, db)
    except HTTPException:
        user = User(id=0, role="user")
    return _templates.TemplateResponse(
        request=request,
        name="pricing.html",
        context={"products": list_products(db, user), "user": user},
    )
