"""Post-crew delivery: PDF rendering + email (Resend) / Telegram send.

Deliberately not part of the crew — deterministic, no LLM calls, so it never
costs tokens or adds LLM latency to a plan. Triggered by a user action
(the boarding-pass card's "Email me" / "Send to Telegram" buttons) via the
deliver_itinerary_job arq job (see app/queue/delivery.py), so a slow outbound
HTTP call never blocks a request.
"""
