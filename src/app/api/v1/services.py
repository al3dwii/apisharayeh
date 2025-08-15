from fastapi import APIRouter, HTTPException
from app.kernel.plugins.loader import registry as plugin_registry
from app.kernel.plugins.spec import ServiceManifest

router = APIRouter(prefix="/v1", tags=["services"])

@router.get("/services")
def list_services():
    # light-weight listing
    items = []
    for mf in plugin_registry.list():
        items.append({
            "id": mf.id,
            "name": getattr(mf, "name", mf.id),
            "version": mf.version,
            "runtime": mf.runtime,
        })
    return {"services": items}

@router.get("/services/{service_id}")
def get_service(service_id: str) -> ServiceManifest:
    try:
        return plugin_registry.get(service_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")
