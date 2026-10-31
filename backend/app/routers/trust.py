"""Backend-owned: demo sign-in, "still there?" votes, facilities checks (contract v3)."""
from fastapi import APIRouter, Depends, HTTPException

from .. import auth, barriers
from ..ai import facilities
from ..schemas import Account, FixList, Report, SignInRequest, SignInResponse, VerifyRequest, VoteRequest

router = APIRouter()


@router.get("/auth/demo-accounts", response_model=list[Account])
def demo_accounts():
    return auth.DEMO_ACCOUNTS


@router.post("/auth/demo-signin", response_model=SignInResponse)
def demo_signin(body: SignInRequest):
    token, account = auth.sign_in(body.account_id)
    return SignInResponse(token=token, account=account)


@router.get("/auth/me", response_model=Account)
def me(account: Account = Depends(auth.require_account)):
    return account


@router.post("/reports/{report_id}/vote", response_model=Report)
def vote(report_id: str, body: VoteRequest, account: Account = Depends(auth.require_account)):
    try:
        return barriers.vote(report_id, account, body.still_there)
    except barriers.ReportRejected as e:
        raise HTTPException(e.status_code, e.detail) from e


@router.post("/reports/{report_id}/verify", response_model=Report)
def verify(report_id: str, body: VerifyRequest, account: Account = Depends(auth.require_facilities)):
    try:
        return barriers.verify(report_id, body.action)
    except barriers.ReportRejected as e:
        raise HTTPException(e.status_code, e.detail) from e


@router.get("/facilities/reports", response_model=list[Report])
def facilities_reports(account: Account = Depends(auth.require_facilities)):
    return barriers.facilities_view()


@router.get("/facilities/fixlist", response_model=FixList)
def fix_list(account: Account = Depends(auth.require_facilities)):
    return facilities.fix_list()


# ---- dev only: test the trust rules without Claude vision (off unless WHEELIO_DEV=1) ----------
import os  # noqa: E402

from fastapi import File, Form, UploadFile  # noqa: E402

from ..schemas import VisionResult  # noqa: E402

if os.environ.get("WHEELIO_DEV") == "1":

    @router.post("/dev/reports", response_model=Report)
    async def dev_report(photo: UploadFile = File(...), lat: float = Form(...), lon: float = Form(...),
                         device_lat: float | None = Form(None), device_lon: float | None = Form(None),
                         barrier_type: str = Form("construction"), severity: str = Form("high"),
                         passable: bool = Form(False), confidence: float = Form(0.9),
                         description: str = Form("Construction fencing blocks the path."),
                         as_client: str = Form("dev"),  # pretend to be a different phone (anonymous tests)
                         account: Account | None = Depends(auth.optional_account)):
        """Same rules as POST /api/reports, but the caller supplies the classification."""
        vision = VisionResult(barrier_type=barrier_type, severity=severity,
                              passable_for_manual_chair=passable, confidence=confidence,
                              description=description)
        try:
            return barriers.submit_report(account, await photo.read(), lat, lon, device_lat, device_lon, vision,
                                          client_key=as_client)
        except barriers.ReportRejected as e:
            raise HTTPException(e.status_code, e.detail) from e
