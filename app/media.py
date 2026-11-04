"""ffmpeg helpers: normalize for browser playback, thumbnail, keyframes, audio."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

SCALE = "scale='min(768,iw)':-2"


def _run(cmd: list[str], timeout: int = 600) -> None:
    subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)


def probe(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(r.stdout)


def duration(meta: dict) -> float:
    try:
        return float(meta.get("format", {}).get("duration") or 0)
    except ValueError:
        return 0.0


def _stream(meta: dict, kind: str) -> dict | None:
    return next((s for s in meta.get("streams", []) if s.get("codec_type") == kind), None)


def normalize(src: Path) -> tuple[Path, dict]:
    """H.264/AAC MP4 with faststart; remux only if already compatible."""
    meta = probe(src)
    v, a = _stream(meta, "video"), _stream(meta, "audio")
    if v is None:
        raise RuntimeError("downloaded file has no video stream")
    v_ok = v.get("codec_name") == "h264" and v.get("pix_fmt") in ("yuv420p", "yuvj420p")
    a_ok = a is None or a.get("codec_name") == "aac"
    out = src.with_name("video.mp4")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-map", "0:v:0"]
    if a is not None:
        cmd += ["-map", "0:a:0"]
    cmd += ["-c:v", "copy"] if v_ok else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
    if a is not None:
        cmd += ["-c:a", "copy"] if a_ok else ["-c:a", "aac", "-b:a", "128k"]
    cmd += ["-movflags", "+faststart", str(out)]
    _run(cmd)
    return out, probe(out)


def thumbnail(video: Path, out: Path, dur: float) -> Path:
    ts = min(1.0, dur / 2) if dur else 0
    _run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ts:.2f}", "-i", str(video),
          "-frames:v", "1", "-vf", "scale='min(720,iw)':-2", "-q:v", "3", str(out)])
    return out


def keyframes(video: Path, outdir: Path, max_frames: int, dur: float) -> list[Path]:
    """Uniform samples for coverage + scene changes to catch briefly flashed text."""
    outdir.mkdir(exist_ok=True)
    n_uniform = max(3, max_frames // 2)
    frames: list[Path] = []
    for i in range(n_uniform):
        ts = dur * (i + 0.5) / n_uniform if dur else 0
        out = outdir / f"u_{i:02d}.jpg"
        try:
            _run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ts:.2f}", "-i", str(video),
                  "-frames:v", "1", "-vf", SCALE, "-q:v", "4", str(out)])
            if out.exists():
                frames.append(out)
        except subprocess.CalledProcessError:
            pass
    n_scene = max_frames - len(frames)
    if n_scene > 0:
        try:
            _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
                  "-vf", f"select='gt(scene,0.3)',{SCALE}", "-fps_mode", "vfr",
                  "-frames:v", str(n_scene), "-q:v", "4", str(outdir / "s_%02d.jpg")])
        except subprocess.CalledProcessError:
            pass
        frames += sorted(outdir.glob("s_*.jpg"))
    return frames[:max_frames]


def extract_audio(video: Path, out: Path, meta: dict) -> Path | None:
    if _stream(meta, "audio") is None:
        return None
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
          "-c:a", "libmp3lame", "-b:a", "48k", str(out)])
    return out
