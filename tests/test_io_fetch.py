import io
import os
from pathlib import Path
import pytest

from app.kernel.ops import io as io_ops
from app.kernel.errors import ProblemDetails

class Ctx:
    def __init__(self, root: Path):
        self._root = root
        self.permissions = {"fs_write"}
        self.DEV_OFFLINE = True
    def artifacts_dir(self, to_dir: str) -> Path:
        p = (self._root / to_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    def write_text(self, rel_path: str, text: str) -> Path:
        p = (self._root / rel_path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

def test_fetch_same_file_short_circuits(tmp_path: Path):
    # Arrange: source already in artifacts/<proj>/input/file.docx
    proj = tmp_path
    input_dir = proj / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    src = input_dir / "test.docx"
    src.write_bytes(b"dummy")

    ctx = Ctx(proj)

    # Act: fetch to to_dir="input" (dest will be the same path)
    out = io_ops.fetch(ctx, url=str(src), to_dir="input")

    # Assert: no crash, path returned, file still exists
    assert Path(out["path"]).resolve() == src.resolve()
    assert src.exists()

def test_fetch_blocks_http_when_dev_offline(tmp_path: Path):
    ctx = Ctx(tmp_path)
    with pytest.raises(ProblemDetails) as e:
        io_ops.fetch(ctx, url="http://example.com/a.docx", to_dir="input")
    assert e.value.code == "E_DEV_OFFLINE"
