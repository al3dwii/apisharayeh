import time
from fastapi.testclient import TestClient
import types

from app.server import app as app_mod

# We patch get_plugin to avoid reading from disk, and service_runner.run to avoid executing real ops.

def test_job_transitions(monkeypatch):
    app = app_mod.app
    client = TestClient(app)

    # Fake plugin loader
    def fake_get_plugin(plugins_root, service_id):
        manifest = {"id": service_id, "name": "Test Service", "version": "0.0.0", "summary": "fake"}
        flow = {"steps": [{"run": "echo", "in": {}}]}
        perms = {"fs_write"}
        return manifest, flow, perms

    # Fake runner that immediately emits succeeded and returns outputs
    def fake_run(self, service_id, inputs, on_event=None, loop=None):
        if on_event:
            on_event("status", {"state": "running"})
        if on_event:
            on_event("status", {"state": "succeeded"})
        return {"ok": True}

    monkeypatch.setattr(app_mod, "get_plugin", fake_get_plugin, raising=True)
    monkeypatch.setattr(app_mod.service_runner, "run", types.MethodType(fake_run, app_mod.service_runner))

    # Start job (validate=0 so we don't need a schema)
    r = client.post("/v1/services/test.service/jobs", json={"inputs": {"project_id": "prj_test"}}, params={"validate": 0})
    assert r.status_code == 201
    job_id = r.json()["job_id"]

    # Poll once; it may already be succeeded or briefly running
    st = client.get(f"/v1/jobs/{job_id}")
    assert st.status_code == 200
    assert st.json()["status"] in ("running", "succeeded")

    # Quick retry to see terminal state
    for _ in range(5):
        st = client.get(f"/v1/jobs/{job_id}")
        if st.json()["status"] == "succeeded":
            break
        time.sleep(0.05)

    assert st.json()["status"] == "succeeded"
