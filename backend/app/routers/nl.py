"""Backend-owned. Route briefing (Claude, with a template fallback) and the parse stub."""
from fastapi import APIRouter

from ..ai import explain as briefing
from ..schemas import ExplainRequest, ExplainResponse, ParseRequest, ParseResponse

router = APIRouter()


@router.post("/parse", response_model=ParseResponse)
def parse(req: ParseRequest):
    # Not built: dropped from the plan (Oct 31) in favor of photo reports. Still returns a mock.
    return ParseResponse(**{"from": {"place_id": "stop:green"}, "to": {"place_id": "bldg:baker-berry"}}, notes="mock")


@router.post("/explain", response_model=ExplainResponse)
def explain(req: ExplainRequest):
    text, _source = briefing.explain(req.route.model_dump(),
                                     req.alternative.model_dump() if req.alternative else None)
    return ExplainResponse(text=text)
