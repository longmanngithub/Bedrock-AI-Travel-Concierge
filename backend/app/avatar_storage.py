"""Validated avatar persistence with local and Cloudflare R2 backends."""
from __future__ import annotations

import io
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from PIL import Image, UnidentifiedImageError

from .config import get_settings
from .errors import AppError, ErrorCode

MAX_AVATAR_BYTES = 5 * 1024 * 1024
MAX_AVATAR_PIXELS = 20_000_000


class AvatarStorage(Protocol):
    def put(self, key: str, content: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalAvatarStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        # Keys are generated internally; reject any accidental traversal.
        if Path(key).name != key or not key.endswith(".webp"):
            raise AppError(ErrorCode.E_VALIDATION, log_detail="unsafe avatar key")
        return self.root / key

    def put(self, key: str, content: bytes) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._path(key).write_bytes(content)

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise AppError(ErrorCode.E_NOT_FOUND, log_detail="avatar not found") from exc

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass


class R2AvatarStorage:
    def __init__(self) -> None:
        settings = get_settings()
        if not all((settings.r2_account_id, settings.r2_access_key_id, settings.r2_secret_access_key, settings.r2_bucket)):
            raise AppError(ErrorCode.E_DELIVERY_UNAVAILABLE, log_detail="R2 avatar configuration incomplete")
        try:
            import boto3
        except ImportError as exc:
            raise AppError(ErrorCode.E_INTERNAL, log_detail="boto3 unavailable for R2") from exc
        self.bucket = settings.r2_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
        )

    def put(self, key: str, content: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=content, ContentType="image/webp", CacheControl="private, max-age=86400")

    def get(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as exc:  # Provider response is intentionally not exposed.
            raise AppError(ErrorCode.E_NOT_FOUND, log_detail="R2 avatar read failed") from exc

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception:
            # Replacement/deletion must not make an account unusable if a stale
            # object has already been deleted by lifecycle tooling.
            pass


@lru_cache
def get_avatar_storage() -> AvatarStorage:
    settings = get_settings()
    if settings.avatar_storage_backend.lower() == "r2":
        return R2AvatarStorage()
    return LocalAvatarStorage(settings.avatar_local_dir)


def normalise_avatar(raw: bytes) -> bytes:
    if not raw or len(raw) > MAX_AVATAR_BYTES:
        raise AppError(ErrorCode.E_VALIDATION, log_detail="avatar size invalid")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > MAX_AVATAR_PIXELS:
                raise AppError(ErrorCode.E_VALIDATION, log_detail="avatar dimensions invalid")
            # Re-encoding removes metadata and makes every stored object a
            # consistent, browser-friendly type.
            converted = image.convert("RGBA") if image.mode in ("RGBA", "LA", "P") else image.convert("RGB")
            output = io.BytesIO()
            converted.save(output, format="WEBP", quality=88, method=6)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise AppError(ErrorCode.E_VALIDATION, log_detail="avatar image invalid") from exc


def store_avatar(raw: bytes) -> str:
    key = f"{uuid.uuid4().hex}.webp"
    get_avatar_storage().put(key, normalise_avatar(raw))
    return key
