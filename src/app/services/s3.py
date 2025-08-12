import aioboto3, os
from app.core.config import settings

async def get_presigned_put(key: str, expires: int = 3600) -> str:
    session = aioboto3.Session()
    async with session.client("s3") as s3:
        return await s3.generate_presigned_url(
            ClientMethod="put_object",
            Params={"Bucket": settings.S3_BUCKET, "Key": key},
            ExpiresIn=expires,
        )

async def get_presigned_get(key: str, expires: int = 3600) -> str:
    session = aioboto3.Session()
    async with session.client("s3") as s3:
        return await s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": settings.S3_BUCKET, "Key": key},
            ExpiresIn=expires,
        )
