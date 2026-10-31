"""FastAPI app. Shared file: every router is registered here already, so you should not need to edit it."""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import FRONTEND_DIR
from .routers import nl, places, reports, route, trust

app = FastAPI(title="Wheelio")
app.add_middleware(GZipMiddleware, minimum_size=1000)  # map layers and routes compress ~5x
log = logging.getLogger("wheelio")


# Every error is one plain-language sentence in "detail", so the app can show it as-is.
@app.exception_handler(RequestValidationError)
async def bad_request(request: Request, exc: RequestValidationError):
    log.info("invalid request to %s: %s", request.url.path, exc.errors())
    return JSONResponse(status_code=422, content={"detail": "Something in that request was missing "
                                                  "or not valid. Please try again."})


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    log.exception("error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Something went wrong on our side. "
                                                  "Please try again."})

app.include_router(places.router, prefix="/api")
app.include_router(route.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(nl.router, prefix="/api")
app.include_router(trust.router, prefix="/api")  # v3: sign-in, votes, facilities (Backend)


@app.get("/api/health")
def health():
    return {"ok": True}


# Mounted last so /api routes take precedence.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
