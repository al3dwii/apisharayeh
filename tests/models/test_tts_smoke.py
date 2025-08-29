import pytest, os
from app.services.models import ModelRouter
@pytest.mark.integration
def test_tts_smoke(tmp_path):
    if not os.getenv("AZURE_SPEECH_KEY"): pytest.skip("Azure creds not set")
    r = ModelRouter()
    audio = r.tts([{"text":"Hello world"}], voice="en-US-JennyNeural")
    assert os.path.exists(audio) and audio.endswith(".wav")
