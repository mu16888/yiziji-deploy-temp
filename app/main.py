"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.api.auth import pages_router as auth_pages_router
from app.api.auth import router as auth_router
from app.api.credits import router as credits_router
from app.middleware.csrf import CSRFMiddleware

app = FastAPI(title="AI Monetization Template")

app.add_middleware(CSRFMiddleware)
app.include_router(auth_router)
app.include_router(auth_pages_router)
app.include_router(credits_router)


@app.get("/health")
def health():
    return {"status": "ok"}
