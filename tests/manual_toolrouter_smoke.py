# Run:  DEV_OFFLINE=true python -m tests.manual_toolrouter_smoke
from __future__ import annotations
import os
import uuid
from pathlib import Path

os.environ.setdefault("DEV_OFFLINE", "true")
os.environ.setdefault("ARTIFACTS_DIR", "./artifacts")
os.environ.setdefault("PUBLIC_BASE_URL", "")

from app.kernel.context import ExecutionContext
from app.kernel.events import EventEmitter
from app.kernel.bootstrap_toolrouter import build_toolrouter
from app.kernel.storage import project_root

def print_event(event_type, payload):
    print(f"[{event_type}] {payload}")

def main():
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    project_id = f"prj_{uuid.uuid4().hex[:8]}"

    emitter = EventEmitter(on_emit=print_event)
    ctx = ExecutionContext(
        job_id=job_id,
        project_id=project_id,
        permissions={"fs_write", "fs_read"},  # from manifest snapshot normally
        env={"DEV_OFFLINE": True},
        emitter=emitter,
    )

    tr = build_toolrouter()

    # 1) save a small text file
    r1 = tr.call("io.save_text", ctx, text="hello kernel", to_dir="input", filename="hello.txt")
    print("save_text ->", r1)

    # 2) fetch that same file via file:// URL into 'copied'
    file_url = f"file://{r1['path']}"
    r2 = tr.call("io.fetch", ctx, url=file_url, to_dir="copied")
    print("fetch ->", r2)

    print("Artifacts root:", project_root(project_id))

if __name__ == "__main__":
    main()
