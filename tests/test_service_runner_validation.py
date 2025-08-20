import pytest
from app.kernel.service_runner import _validate_flow_steps

def test_accepts_steps():
    _validate_flow_steps({"steps": [{"run": "echo", "in": {}}]})

def test_accepts_nodes():
    _validate_flow_steps({"nodes": [{"op": "echo", "in": {}}]})

def test_rejects_empty():
    with pytest.raises(ValueError):
        _validate_flow_steps({})
