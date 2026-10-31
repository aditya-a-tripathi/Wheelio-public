"""Backend-owned. Barrier reports: photo in, Claude vision, trust rules, photo out.

Flow: signed-in account -> read and shrink the photo -> Claude classifies it (or "unchecked"
without a key) -> barriers.submit_report() applies every rule (see barriers.py) -> save the
photo under the report id. Rules and statuses: docs/api.md v3.
"""
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from .. import auth, barriers
from ..ai import vision
from ..config import ROOT
from ..schemas import Account, ConfirmRequest, Report

router = APIRouter()
UPLOADS = ROOT / "data" / "uploads"  # gitignored: photos may contain people
MAX_UPLOAD = 20 * 1024 * 1024


@router.post("/reports", response_model=Report)
async def create_report(photo: UploadFile = File(...), lat: float = Form(...), lon: float = Form(...),
                        device_lat: float | None = Form(None), device_lon: float | None = Form(None),
                        request: Request = None, account: Account | None = Depends(auth.optional_account)):
    raw = await photo.read()
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "That photo is too large. Please take a new one.")
    try:
        jpeg, _ = vision.prepare(raw)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    result = vision.classify(jpeg)
    try:
        report = barriers.submit_report(account, raw, lat, lon, device_lat, device_lon, result,
                                        client_key=client_key(request))
    except barriers.ReportRejected as e:
        raise HTTPException(e.status_code, e.detail) from e
    if report.photo_url is None:  # a confirming report keeps the first photo
        UPLOADS.mkdir(parents=True, exist_ok=True)
        (UPLOADS / f"{report.id}.jpg").write_bytes(jpeg)
        report.photo_url = f"/api/reports/{report.id}/photo"
    return report


def client_key(request: Request | None) -> str:
    """Network address for anonymous rate limits (the tunnel passes the phone's address along)."""
    if request is None:
        return "unknown"
    return (request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown"))


@router.get("/reports", response_model=list[Report])
def get_reports():
    return barriers.list_reports()


@router.get("/reports/{report_id}/photo")
def get_photo(report_id: str):
    path = UPLOADS / f"{report_id}.jpg"
    if barriers.get_report(report_id) is None or not path.exists():
        raise HTTPException(404, "No photo for that report.")
    return FileResponse(path, media_type="image/jpeg")


@router.post("/reports/{report_id}/confirm", response_model=Report)
def confirm_report(report_id: str, body: ConfirmRequest, account: Account = Depends(auth.require_account)):
    """v1 endpoint kept for compatibility: same as /vote."""
    try:
        return barriers.vote(report_id, account, body.still_there)
    except barriers.ReportRejected as e:
        raise HTTPException(e.status_code, e.detail) from e
