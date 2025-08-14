from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel

Runtime = Literal["dsl", "inproc", "http", "grpc"]

class ServiceManifest(BaseModel):
    id: str
    name: str
    version: str
    runtime: Runtime
    entrypoint: Optional[str] = None
    inputs: Dict[str, Any]
    outputs: Dict[str, Any]
    resources: Dict[str, Any] = {}
    capabilities: Dict[str, Any] = {}
    permissions: Dict[str, bool] = {}
