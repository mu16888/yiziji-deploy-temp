"""FastAPI application entrypoint."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.admin import pages_router as admin_pages_router
from app.api.admin import router as admin_router
from app.api.auth import pages_router as auth_pages_router
from app.api.auth import router as auth_router
from app.api.catalog import pages_router as catalog_pages_router
from app.api.catalog import router as catalog_router
from app.api.credits import router as credits_router
from app.api.ingest import router as ingest_router
from app.api.jobs import router as jobs_router
from app.api.pages import router as pages_router
from app.api.pay import router as pay_router
from app.api.promote import router as promote_router
from app.middleware.csrf import CSRFMiddleware

app = FastAPI(title="AI Monetization Template")

app.add_middleware(CSRFMiddleware)
app.include_router(auth_router)
app.include_router(auth_pages_router)
app.include_router(credits_router)
app.include_router(ingest_router)
app.include_router(pay_router)
app.include_router(catalog_router)
app.include_router(catalog_pages_router)
app.include_router(jobs_router)
app.include_router(promote_router)
app.include_router(admin_router)
app.include_router(admin_pages_router)
app.include_router(pages_router)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health")
def health():
    return {"status": "ok"}
