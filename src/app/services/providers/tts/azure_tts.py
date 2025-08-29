# app/services/providers/tts/azure_tts.py
import os, tempfile
from typing import Any, Dict, List, Optional
from app.services.providers.base import TTSProvider

class AzureNeuralTTS(TTSProvider):
    name = "azure:neural"

    def __init__(self):
        import azure.cognitiveservices.speech as speechsdk
        key = os.getenv("AZURE_SPEECH_KEY")
        region = os.getenv("AZURE_SPEECH_REGION")
        if not (key and region): raise RuntimeError("AZURE_SPEECH_KEY/REGION missing")
        self.speechsdk = speechsdk
        self.speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
        self.speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
        )

    def synthesize(self, segments: List[Dict[str, Any]], voice: Dict[str, Any] | str,
                   lang: Optional[str]=None, sample_rate: int = 24000) -> str:
        voice_name = voice if isinstance(voice, str) else voice.get("name") or "en-US-JennyNeural"
        self.speech_config.speech_synthesis_voice_name = voice_name
        audio_config = self.speechsdk.audio.AudioConfig(use_default_speaker=False)

        ssml = "<speak version='1.0' xml:lang='{}'>".format((lang or "en-US"))
        for s in segments:
            ssml += f"<p><s>{s['text']}</s></p>"
        ssml += "</speak>"

        synth = self.speechsdk.SpeechSynthesizer(speech_config=self.speech_config, audio_config=audio_config)
        result = synth.speak_ssml_async(ssml).get()
        if result.reason != self.speechsdk.ResultReason.SynthesizingAudioCompleted:
            raise RuntimeError(str(result))

        out = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        out.write(result.audio_data)
        out.flush(); out.close()
        return out.name
