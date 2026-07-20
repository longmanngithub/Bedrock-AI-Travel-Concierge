"""Reverse geocoding for opt-in, browser-geolocation-derived flight origin."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from starlette.requests import Request

from ..auth.cookies import verify_csrf
from ..auth.deps import current_user
from ..config import get_settings
from ..geocode import reverse_geocode
from ..models import User
from ..rate_limit import limiter

router = APIRouter(prefix="/geo", tags=["geo"])
_settings = get_settings()


class ReverseGeocodeBody(BaseModel):
    # Coordinates are sensitive personal data — always in the request body,
    # never a query string (which would land in access logs and history).
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class ReverseGeocodeView(BaseModel):
    origin: str


@router.post("/reverse", response_model=ReverseGeocodeView)
@limiter.limit(_settings.rate_limit_geocode)
def reverse(body: ReverseGeocodeBody, request: Request, user: User = Depends(current_user)) -> ReverseGeocodeView:
    verify_csrf(request)
    return ReverseGeocodeView(origin=reverse_geocode(body.lat, body.lon))
