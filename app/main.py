"""FastAPI application entrypoint."""

from fastapi import FastAPI

app = FastAPI(title="AI Monetization Template")


@app.get("/health")
def health():
    return {"status": "ok"}
