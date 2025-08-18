from fastapi import APIRouter
from app.kernel.plugins.loader import sync_all_from_loader

router = APIRouter(prefix="/v1/admin", tags=["admin"])

@router.post("/sync_plugins")
async def sync_plugins():
    count = await sync_all_from_loader()
    return {"synced": count}
