"""Backend-owned. Least-effort and fastest accessible routes."""
from fastapi import APIRouter, HTTPException

from .. import barriers
from ..routing import NoRoute, route
from ..schemas import RouteRequest, RouteResponse

router = APIRouter()


@router.post("/route", response_model=RouteResponse)
def post_route(req: RouteRequest):
    try:
        result = route(
            start=req.from_.model_dump(),
            to_place_id=req.to.place_id or "",
            profile=req.profile,
            max_grade_pct=req.max_grade_pct,
            avoid_surfaces=req.avoid_surfaces,
            edge_modifiers=barriers.get_edge_modifiers(),
            blocked_points=barriers.blocked_points(),
        )
        for r in result["routes"]:
            barriers.annotate_route(r)
        return result
    except NoRoute as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
