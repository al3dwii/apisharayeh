import pytest, os
from app.services.models import ModelRouter
@pytest.mark.integration
def test_asr_smoke(tmp_path):
    r = ModelRouter()
    # use a tiny wav (generate with ffmpeg sine or include a sample in tests/assets)
    wav = os.path.join(os.path.dirname(__file__), "assets", "hello_en_16k.wav")
    out = r.asr(wav, diarize=False)
    assert "segments" in out and len(out["segments"]) >= 1
