"""Renders a generated Itinerary to a PDF boarding pass.

Mirrors frontend/src/components/chat/TravelTicket.jsx's layout (same
sections, same order) so the emailed/Telegrammed PDF matches what the
traveller already saw in the app. Rendered on demand, not cached to disk —
WeasyPrint's layout pass is a few hundred ms for an itinerary-sized document,
cheap enough to redo per request/delivery rather than manage a file store's
lifecycle for what is otherwise a pure function of (itinerary, trip_request).
"""
from __future__ import annotations

import os
import sys

if sys.platform == "darwin":
    # Homebrew (Apple Silicon) installs Pango/cairo/gobject under /opt/homebrew,
    # a prefix macOS's dlopen doesn't search by default — WeasyPrint's cffi
    # bindings fail to load the native libs without this. Linux (production,
    # via the Dockerfile's apt packages) needs no such shim; this is a no-op
    # there and even on macOS if the caller already set the var.
    os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib:/usr/local/lib")

from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from ..schemas import Itinerary, TripRequest

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
)


def _airport_code(name: str | None) -> str:
    letters = "".join(ch for ch in (name or "") if ch.isalpha()).upper()
    if len(letters) >= 3:
        return letters[:3]
    return (letters + "XXX")[:3]


def _money(value: float | None, currency: str = "USD") -> str:
    if value is None:
        return "—"
    try:
        return f"{currency} {value:,.0f}"
    except (TypeError, ValueError):
        return str(value)


def render_itinerary_pdf(itinerary: Itinerary, trip_request: TripRequest) -> bytes:
    """Returns PDF bytes for the given itinerary. Pure/synchronous — call from
    a worker thread (arq job), never on the FastAPI event loop directly."""
    template = _env.get_template("itinerary.html")
    html = template.render(
        itinerary=itinerary,
        trip=trip_request,
        money=_money,
        origin_code=_airport_code(trip_request.origin or "Home"),
        dest_code=_airport_code(itinerary.destination),
        generated_at=datetime.now(timezone.utc).strftime("%d %b %Y"),
    )
    return HTML(string=html, base_url=str(_TEMPLATE_DIR)).write_pdf()
