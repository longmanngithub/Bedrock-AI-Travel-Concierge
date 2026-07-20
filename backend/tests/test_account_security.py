from __future__ import annotations

import io
import unittest

from PIL import Image

from app.auth.passwords import validate_password_policy
from app.avatar_storage import MAX_AVATAR_BYTES, normalise_avatar
from app.errors import AppError, ErrorCode


class PasswordPolicyTests(unittest.TestCase):
    def test_accepts_strong_password(self) -> None:
        validate_password_policy("CorrectHorse!42")

    def test_rejects_missing_required_character_classes(self) -> None:
        for password in ("alllowercase123!", "ALLUPPERCASE123!", "NoNumberSymbols!", "NoSymbolPassword12"):
            with self.subTest(password=password):
                with self.assertRaises(AppError) as raised:
                    validate_password_policy(password)
                self.assertEqual(raised.exception.code, ErrorCode.E_PASSWORD_POLICY)

    def test_rejects_short_password(self) -> None:
        with self.assertRaises(AppError):
            validate_password_policy("Short1!")


class AvatarNormalisationTests(unittest.TestCase):
    def _png(self) -> bytes:
        image = Image.new("RGBA", (32, 32), (13, 148, 136, 255))
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    def test_reencodes_valid_image_to_webp(self) -> None:
        avatar = normalise_avatar(self._png())
        self.assertGreater(len(avatar), 20)
        with Image.open(io.BytesIO(avatar)) as image:
            self.assertEqual(image.format, "WEBP")

    def test_rejects_non_image_and_oversized_input(self) -> None:
        with self.assertRaises(AppError):
            normalise_avatar(b"not an image")
        with self.assertRaises(AppError):
            normalise_avatar(b"x" * (MAX_AVATAR_BYTES + 1))


if __name__ == "__main__":
    unittest.main()
