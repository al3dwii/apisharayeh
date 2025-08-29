from fastapi import APIRouter
from app.services.models import ModelRouter
router = APIRouter(prefix="/models", tags=["models"])

@router.get("")
def list_defaults():
    r = ModelRouter()
    return r.cfg or {}

@router.get("/voices")
def list_voices():
    # For Azure: static starter set; later fetch via SDK.
    return {"voices":[{"name":"en-US-JennyNeural","lang":"en-US"},{"name":"ar-SA-HamedNeural","lang":"ar-SA"}]}
