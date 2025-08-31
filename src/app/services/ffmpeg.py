# app/services/ffmpeg.py
import subprocess
import logging
import re
import sys
import time, tempfile, os, json



def _run(cmd, *, log_name="ffmpeg", duration_s=None):
    """Run ffmpeg and stream stderr -> logger; if duration_s is known, log % progress."""
    logger = logging.getLogger(log_name)
    import subprocess, time, re
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    rx = re.compile(r"time=(\d+):(\d+):(\d+\.?\d*)")
    last_pct = -1
    while True:
        line = p.stderr.readline()
        if not line and p.poll() is not None:
            break
        if not line:
            time.sleep(0.01); continue
        line = line.rstrip()
        logger.info("[FFMPEG] %s", line)
        if duration_s:
            m = rx.search(line)
            if m:
                hh, mm, ss = int(m.group(1)), int(m.group(2)), float(m.group(3))
                cur = hh*3600 + mm*60 + ss
                pct = max(0, min(100, int(cur*100/duration_s))) if duration_s>0 else 0
                if pct != last_pct:
                    logger.info("[PROGRESS] ffmpeg %d%%", pct)
                    last_pct = pct
    rc = p.wait()
    if rc != 0:
        raise subprocess.CalledProcessError(rc, cmd)
def probe(path: str) -> dict:
    p = subprocess.run(["ffprobe","-v","error","-print_format","json","-show_format","-show_streams", path],
                       capture_output=True, check=True, text=True)
    return json.loads(p.stdout)

def ensure_wav_mono_16k(src: str) -> str:
    out = tempfile.NamedTemporaryFile(delete=False, suffix=".wav"); out.close()
    _run(["ffmpeg","-y","-i",src,"-ac","1","-ar","16000","-f","wav", out.name])
    return out.name

def extract_audio(src_media: str, out_wav: str, sample_rate: int = 16000) -> None:
    os.makedirs(os.path.dirname(out_wav), exist_ok=True)
    _run(["ffmpeg","-y","-i",src_media,"-vn","-ac","1","-ar",str(sample_rate),"-f","wav", out_wav])

def mux_replace_audio(src_media: str, new_audio: str, out_mp4: str) -> None:
    """Mux video with new audio (explicit maps, keep video, AAC audio)."""
    _run([
        "ffmpeg","-y",
        "-i", src_media,
        "-i", new_audio,
        "-map","0:v:0","-map","1:a:0",
        "-c:v","copy",
        "-c:a","aac","-b:a","192k",
        "-ar","48000","-ac","2",
        "-shortest",
        out_mp4
    ])
def _fmt_time(t: float) -> str:
    hrs = int(t // 3600); t -= hrs*3600
    mins = int(t // 60);   t -= mins*60
    secs = int(t)
    ms = int(round((t - secs)*1000))
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{ms:03d}"

def write_srt(segments, out_path: str) -> None:
    """segments: [{start, end, text}]"""
    with open(out_path, "w", encoding="utf-8") as f:
        for i, s in enumerate(segments, start=1):
            f.write(f"{i}\n")
            f.write(f"{_fmt_time(float(s.get('start',0)))} --> {_fmt_time(float(s.get('end',0)))}\n")
            f.write((s.get("text","") or "").strip() + "\n\n")

def mux_replace_audio(src_media: str, new_audio: str, out_mp4: str) -> None:
    """Mux video with new audio (explicit maps, keep video, AAC audio)."""
    _run([
        "ffmpeg","-y",
        "-i", src_media,
        "-i", new_audio,
        "-map","0:v:0","-map","1:a:0",
        "-c:v","copy",
        "-c:a","aac","-b:a","192k",
        "-ar","48000","-ac","2",
        "-shortest",
        out_mp4
    ])
