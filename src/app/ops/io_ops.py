import os, tempfile, requests
from app.services.artifacts import save_file

def fetch(url: str):
    with requests.get(url, stream=True, timeout=60, allow_redirects=True) as r:
        r.raise_for_status()
        fd, p = tempfile.mkstemp()
        with os.fdopen(fd, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
    return {"file": p}
