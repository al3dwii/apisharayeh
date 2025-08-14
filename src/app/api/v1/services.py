from fastapi import APIRouter
from app.kernel.plugins.loader import registry

router = APIRouter()

@router.get("/services")
def list_services():
    return [s.model_dump(include={"id","name","version","inputs","outputs"}) for s in registry.list()]

@router.get("/services/{sid}")
def get_service(sid: str):
    mf = registry.get(sid)
    return mf.model_dump()
