import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import main
from app.auth.cookies import CSRF_COOKIE
from app.database import Base, get_db
from app.models import EmailVerification, OAuthAccount, RefreshToken, User
from app.routers import auth


class AccountLifecycleTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(engine, tables=[
            model.__table__ for model in (User, EmailVerification, OAuthAccount, RefreshToken)
        ])
        self.addCleanup(engine.dispose)

        def get_test_db():
            with Session(engine, expire_on_commit=False, autoflush=False) as session:
                yield session

        self.enterContext(patch.dict(main.app.dependency_overrides, {get_db: get_test_db}))
        self.enterContext(patch.object(main.limiter, "enabled", False))
        self.mail = self.enterContext(patch.object(auth, "send_verification_email"))
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.email = "traveller@example.org"
        self.password = "Route-map!902"

    def activate(self):
        pending = self.client.post("/auth/register", json={"email": self.email})
        self.assertEqual(pending.status_code, 200)
        self.assertTrue(pending.json()["verification_required"])
        code = self.mail.call_args.kwargs["code"]
        activated = self.client.post("/auth/verify-email", json={
            "email": self.email, "code": code, "password": self.password,
        })
        self.assertEqual(activated.status_code, 200, activated.json())
        return activated

    def test_registration_sets_password_at_mailbox_verification(self):
        self.activate()
        signed_in = self.client.post("/auth/login", json={
            "email": self.email, "password": self.password,
        })
        self.assertEqual(signed_in.status_code, 200, signed_in.json())
        self.assertTrue(signed_in.json()["has_password"])

    def test_refresh_and_logout_require_request_confirmation(self):
        self.activate()
        self.assertEqual(self.client.post("/auth/logout").status_code, 403)
        csrf = self.client.cookies[CSRF_COOKIE]
        self.assertEqual(self.client.post("/auth/refresh", headers={"X-CSRF-Token": csrf}).status_code, 200)
        csrf = self.client.cookies[CSRF_COOKIE]
        self.assertEqual(self.client.post("/auth/logout", headers={"X-CSRF-Token": csrf}).status_code, 200)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_password_change_revokes_another_browser_session(self):
        self.activate()
        other_browser = TestClient(main.app)
        self.addCleanup(other_browser.close)
        response = other_browser.post("/auth/login", json={
            "email": self.email, "password": self.password,
        })
        self.assertEqual(response.status_code, 200)
        csrf = self.client.cookies[CSRF_COOKIE]
        changed = self.client.post("/auth/password", headers={"X-CSRF-Token": csrf}, json={
            "current_password": self.password,
            "new_password": "Updated-route!903",
            "confirm_new_password": "Updated-route!903",
        })
        self.assertEqual(changed.status_code, 200, changed.json())
        self.assertEqual(self.client.get("/auth/me").status_code, 200)
        self.assertEqual(other_browser.get("/auth/me").json()["code"], "E_SESSION_REVOKED")

    def test_request_is_rejected_if_browser_expects_another_account(self):
        activated = self.activate()
        matching = self.client.get("/auth/me", headers={"X-Expected-Account": activated.json()["id"]})
        self.assertEqual(matching.status_code, 200)
        mismatch = self.client.get("/auth/me", headers={"X-Expected-Account": "another-account"})
        self.assertEqual(mismatch.status_code, 403)

    def test_location_endpoint_requires_current_opt_in(self):
        self.activate()
        with patch("app.routers.geo.reverse_geocode") as provider:
            response = self.client.post("/geo/reverse", json={"lat": 0, "lon": 0},
                                        headers={"X-CSRF-Token": self.client.cookies[CSRF_COOKIE]})
        self.assertEqual(response.status_code, 403)
        provider.assert_not_called()
