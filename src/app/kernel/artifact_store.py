from __future__ import annotations
import os, shutil
from pathlib import Path
from typing import Optional, Tuple, Iterable

# optional settings; use getattr defaults
try:
    from app.core.config import settings  # type: ignore
except Exception:  # pragma: no cover
    class _S:
        ARTIFACTS_DIR = "./artifacts"
        PUBLIC_BASE_URL = ""
        ARTIFACT_TTL_DAYS = 7
        S3_BUCKET = None
        AWS_REGION = "us-east-1"
        AWS_ACCESS_KEY_ID = None
        AWS_SECRET_ACCESS_KEY = None
        S3_ENDPOINT = None
    settings = _S()  # type: ignore


class BaseArtifactStore:
    def put_file(self, src_path: str, dst_key: str) -> Tuple[str, str]:
        """Upload a file to the store. Returns (uri, key)."""
        raise NotImplementedError

    def presign_get(self, key: str, expires: int = 3600) -> Optional[str]:
        return None

    def presign_put(self, key: str, expires: int = 3600, content_type: Optional[str] = None) -> Optional[str]:
        return None

    def delete_prefix(self, prefix: str) -> None:
        pass

    def gc_older_than(self, days: int) -> None:
        pass


class LocalArtifactStore(BaseArtifactStore):
    def __init__(self, root: Optional[str] = None) -> None:
        self.root = Path(root or getattr(settings, "ARTIFACTS_DIR", "./artifacts")).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.base_url = (getattr(settings, "PUBLIC_BASE_URL", "") or "").rstrip("/")

    def put_file(self, src_path: str, dst_key: str) -> Tuple[str, str]:
        dst = (self.root / dst_key).resolve()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_path, dst)
        url = f"{self.base_url}/artifacts/{dst_key}" if self.base_url else f"/artifacts/{dst_key}"
        return url, f"file://{dst}"

    def presign_get(self, key: str, expires: int = 3600) -> Optional[str]:
        return f"{self.base_url}/artifacts/{key}" if self.base_url else f"/artifacts/{key}"

    def gc_older_than(self, days: int) -> None:
        import datetime as dt
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
        for p in self.root.glob("**/*"):
            try:
                if p.is_file():
                    mtime = dt.datetime.fromtimestamp(p.stat().st_mtime, tz=dt.timezone.utc)
                    if mtime < cutoff:
                        p.unlink(missing_ok=True)
            except Exception:
                continue


class S3ArtifactStore(BaseArtifactStore):
    def __init__(self) -> None:
        import boto3  # type: ignore
        self.bucket = getattr(settings, "S3_BUCKET", None)
        if not self.bucket:
            raise RuntimeError("S3_BUCKET not configured")
        self.s3 = boto3.client(
            "s3",
            region_name=getattr(settings, "AWS_REGION", None),
            aws_access_key_id=getattr(settings, "AWS_ACCESS_KEY_ID", None),
            aws_secret_access_key=getattr(settings, "AWS_SECRET_ACCESS_KEY", None),
            endpoint_url=getattr(settings, "S3_ENDPOINT", None),
        )

    def put_file(self, src_path: str, dst_key: str) -> Tuple[str, str]:
        self.s3.upload_file(src_path, self.bucket, dst_key)
        url = self.presign_get(dst_key) or f"s3://{self.bucket}/{dst_key}"
        return url, f"s3://{self.bucket}/{dst_key}"

    def presign_get(self, key: str, expires: int = 3600) -> Optional[str]:
        try:
            return self.s3.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires,
            )
        except Exception:
            return None

    def presign_put(self, key: str, expires: int = 3600, content_type: Optional[str] = None) -> Optional[str]:
        params = {"Bucket": self.bucket, "Key": key}
        if content_type:
            params["ContentType"] = content_type
        try:
            return self.s3.generate_presigned_url(
                ClientMethod="put_object",
                Params=params,
                ExpiresIn=expires,
            )
        except Exception:
            return None

    def delete_prefix(self, prefix: str) -> None:
        token = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = self.s3.list_objects_v2(**kwargs)
            keys = [{"Key": obj["Key"]} for obj in resp.get("Contents", [])]
            if keys:
                self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")

    def gc_older_than(self, days: int) -> None:
        import datetime as dt
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
        token = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": "artifacts/"}
            if token:
                kwargs["ContinuationToken"] = token
            resp = self.s3.list_objects_v2(**kwargs)
            for obj in resp.get("Contents", []):
                if obj["LastModified"].replace(tzinfo=dt.timezone.utc) < cutoff:
                    try:
                        self.s3.delete_object(Bucket=self.bucket, Key=obj["Key"])
                    except Exception:
                        pass
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")


def get_store() -> BaseArtifactStore:
    if getattr(settings, "S3_BUCKET", None):
        try:
            return S3ArtifactStore()
        except Exception:
            pass
    return LocalArtifactStore()
