from app.services.models import ModelRouter
def test_rules_fallbacks():
    r = ModelRouter()
    assert r._use("tts", {}) == "azure:neural"
    assert r._use("ocr", {}) == "paddleocr:server"
