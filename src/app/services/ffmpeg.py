# app/services/ffmpeg.py
import subprocess, tempfile, os, json, shlex, pathlib

def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return p

def probe(path: str) -> dict:
    cmd = ["ffprobe","-v","error","-print_format","json","-show_format","-show_streams", path]
    p = subprocess.run(cmd, capture_output=True, check=True, text=True)
    return json.loads(p.stdout)

def ensure_wav_mono_16k(src: str) -> str:
    out = tempfile.NamedTemporaryFile(delete=False, suffix=".wav"); out.close()
    cmd = ["ffmpeg","-y","-i",src,"-ac","1","-ar","16000","-f","wav", out.name]
    _run(cmd)
    return out.name
