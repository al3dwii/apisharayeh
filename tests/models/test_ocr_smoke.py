import pytest, os
from app.services.models import ModelRouter
@pytest.mark.integration
def test_ocr_smoke():
    if not os.getenv("PADDLE_OCR_BASE"): pytest.skip("Paddle OCR not set")
    r = ModelRouter()
    out = r.ocr(os.path.join(os.path.dirname(__file__),"assets","ocr_sample.png"))
    assert "pages" in out or "text" in out
