"""
FFmpeg Studio — Backend server
================================

A FastAPI application that exposes a REST + Server-Sent-Events API for a
cross-platform FFmpeg GUI desktop app.

Design notes
------------
* All ffmpeg / ffprobe invocations build an **argv list** (never shell=True)
  and run via subprocess.Popen. A human-readable command string for display is
  produced with shlex.join() from that same list, so the command shown by
  /api/preview is byte-for-byte the structure that /api/jobs executes.
* /api/preview and /api/jobs share a single command builder (build_command).
* Long-running jobs execute in background threads. Progress / log lines are
  pushed onto a per-job asyncio.Queue which the SSE endpoint drains.
* API routes are declared BEFORE the static frontend mount so GET / still
  falls through to frontend/index.html while /api/* is never shadowed.
"""

import asyncio
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# --------------------------------------------------------------------------- #
# Project layout — supports PyInstaller frozen bundle
# --------------------------------------------------------------------------- #
import sys as _sys
if getattr(_sys, 'frozen', False) and hasattr(_sys, '_MEIPASS'):
    # PyInstaller one-folder/one-file bundle: static data lives beside the exe
    _bundle = Path(_sys._MEIPASS)
    _appdir = Path(_sys.executable).parent
else:
    _bundle = Path(__file__).resolve().parent.parent
    _appdir = _bundle

PROJECT_ROOT  = _appdir   # runtime data (uploads, outputs) lives beside the app
FRONTEND_DIR  = _bundle / "frontend"  # static files inside bundle
UPLOAD_DIR    = PROJECT_ROOT / "uploads"
OUTPUT_DIR    = PROJECT_ROOT / "outputs"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# --------------------------------------------------------------------------- #
# Global state
# --------------------------------------------------------------------------- #
FFMPEG_PATH: Optional[str] = None
FFPROBE_PATH: Optional[str] = None
FFMPEG_VERSION: str = ""
FFMPEG_AVAILABLE: bool = False
FFPROBE_AVAILABLE: bool = False

MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None

# Registry of jobs and the SSE queues that deliver their live events.
JOBS: Dict[str, Dict[str, Any]] = {}
EVENT_QUEUES: Dict[str, asyncio.Queue] = {}

# Known output extensions (used only for validation / clarity).
KNOWN_EXTENSIONS = {
    "mp4", "mkv", "webm", "mov", "avi", "flv", "ts",
    "mp3", "wav", "aac", "flac", "m4a", "ogg", "opus",
    "gif", "png", "jpg", "jpeg",
}

RESOLUTION_PRESETS = {
    "480p": (854, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
}

LOG_CAP = 500  # max log lines retained per job

# --------------------------------------------------------------------------- #
# Pydantic request model — field names match the JSON contract exactly.
# --------------------------------------------------------------------------- #
class JobRequest(BaseModel):
    mode: str
    inputIds: List[str] = []
    outputName: str = ""
    outputFormat: str = "mp4"
    params: Dict[str, Any] = {}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    """UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


def sse(event: str, data: Dict[str, Any]) -> str:
    """Format a Server-Sent-Event block."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def resolve_resolution(value: Any) -> Optional[tuple]:
    """Map a resolution preset or 'WxH' string to (width, height)."""
    if not value or value == "Original":
        return None
    if isinstance(value, str) and value in RESOLUTION_PRESETS:
        return RESOLUTION_PRESETS[value]
    if isinstance(value, str):
        m = re.match(r"^(\d+)\s*[xX]\s*(\d+)$", value.strip())
        if m:
            return (int(m.group(1)), int(m.group(2)))
    return None


def sanitize_filename(name: str) -> str:
    """Keep only safe characters; replace anything else with '_'."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name or "file")


def resolve_input_paths(input_ids: List[str]) -> List[str]:
    """Resolve the stored paths returned by /api/upload to absolute paths.

    Accepts either the relative path returned by the upload endpoint
    (e.g. ``uploads/u_xxx.mp4``) or a bare id (``u_xxx.mp4``) for
    robustness against older clients and ad-hoc API calls.
    """
    paths: List[str] = []
    for pid in input_ids or []:
        if not pid:
            continue
        p = Path(pid)
        if not p.is_absolute():
            # Try the literal relative path first, then under UPLOAD_DIR.
            candidates = [PROJECT_ROOT / p, PROJECT_ROOT / "uploads" / p.name]
        else:
            candidates = [p]
        for cand in candidates:
            if cand.exists():
                paths.append(str(cand.resolve()))
                break
        else:
            # Fall back to the literal relative path so the user gets a
            # clear "file not found" error from ffmpeg rather than 500.
            paths.append(str((PROJECT_ROOT / p).resolve()))
    return paths


def ffprobe_duration(stored_path: str) -> Optional[float]:
    """Return the duration (seconds) of a media file via ffprobe, or None."""
    if not FFPROBE_AVAILABLE:
        return None
    p = Path(stored_path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    if not p.exists():
        return None
    try:
        argv = [
            FFPROBE_PATH, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1",
            str(p),
        ]
        out = subprocess.run(argv, capture_output=True, text=True, timeout=30).stdout.strip()
        return float(out) if out else None
    except Exception:
        return None


def total_duration_ms(input_ids: List[str], mode: str) -> Optional[int]:
    """Total duration in microseconds, used to compute progress percentage."""
    try:
        if not input_ids:
            return None
        if mode == "merge":
            total = 0.0
            for pid in input_ids:
                d = ffprobe_duration(pid)
                if d:
                    total += d
            return int(total * 1_000_000) if total > 0 else None
        d = ffprobe_duration(input_ids[0])
        return int(d * 1_000_000) if d else None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Command builders
# --------------------------------------------------------------------------- #
def build_video_filters(params: Dict[str, Any]) -> str:
    """Build a comma-separated -vf chain from the GUI parameters."""
    filters: List[str] = []

    # --- scale (explicit or preset) ---
    scale = params.get("scale")
    if scale:
        res = resolve_resolution(scale)
        if res:
            filters.append(f"scale={res[0]}:{res[1]}")
        elif isinstance(scale, str) and "x" in scale.lower():
            w, h = scale.lower().split("x")
            filters.append(f"scale={w}:{h}")

    # --- crop ---
    if params.get("crop_w") and params.get("crop_h"):
        x = params.get("crop_x", 0)
        y = params.get("crop_y", 0)
        filters.append(f"crop={params['crop_w']}:{params['crop_h']}:{x}:{y}")

    # --- rotate / transpose ---
    rot = params.get("rotate")
    if rot in (90, "90"):
        filters.append("transpose=1")
    elif rot in (180, "180"):
        filters.append("rotate=PI")
    elif rot in (270, "270"):
        filters.append("transpose=2")
    elif rot == "flip":
        filters.append("hflip")

    # --- horizontal / vertical flip ---
    if params.get("hflip"):
        filters.append("hflip")
    if params.get("vflip"):
        filters.append("vflip")

    # --- eq (brightness / contrast / saturation) ---
    eq: List[str] = []
    if params.get("brightness") not in (None, 0):
        eq.append(f"brightness={params['brightness']}")
    if params.get("contrast") not in (None, 1):
        eq.append(f"contrast={params['contrast']}")
    if params.get("saturation") not in (None, 1):
        eq.append(f"saturation={params['saturation']}")
    if eq:
        filters.append("eq=" + ":".join(eq))

    # --- grayscale ---
    if params.get("grayscale"):
        filters.append("format=gray")

    # --- speed (setpts) ---
    if params.get("speed"):
        try:
            s = float(params["speed"])
            if s > 0 and s != 1.0:
                filters.append(f"setpts=PTS/{s}")
        except (TypeError, ValueError):
            pass

    # --- fps ---
    if params.get("fps"):
        filters.append(f"fps={params['fps']}")

    # --- image watermark overlay (needs a 2nd input; added by caller) ---
    if params.get("wm_image"):
        x, y = watermark_position(params.get("wm_pos", "br"))
        filters.append(f"overlay={x}:{y}")

    # --- text watermark ---
    dt = drawtext_filter(params)
    if dt:
        filters.append(dt)

    return ",".join(filters)


def build_audio_filters(params: Dict[str, Any]) -> str:
    """Build a comma-separated -af chain from the GUI parameters."""
    filters: List[str] = []

    if params.get("volume") is not None:
        filters.append(f"volume={params['volume']}")

    if params.get("fade_in") is not None:
        filters.append(f"afade=t=in:st=0:d={params['fade_in']}")

    if params.get("fade_out") is not None:
        dur = params.get("duration") or 0
        try:
            dur_f = float(dur) if dur else 0.0
        except (TypeError, ValueError):
            dur_f = 0.0
        st = max(0.0, dur_f - float(params["fade_out"]))
        filters.append(f"afade=t=out:st={st}:d={params['fade_out']}")

    return ",".join(filters)


def watermark_position(pos: str) -> tuple:
    """Return ffmpeg overlay x/y expressions for a position keyword."""
    table = {
        "tl": ("10", "10"),
        "tr": ("W-tw-10", "10"),
        "bl": ("10", "H-th-10"),
        "br": ("W-tw-10", "H-th-10"),
        "center": ("(W-tw)/2", "(H-th)/2"),
    }
    return table.get(pos, ("W-tw-10", "H-th-10"))


def drawtext_filter(params: Dict[str, Any]) -> Optional[str]:
    """Build a drawtext filter string, or None if no wm_text given."""
    text = params.get("wm_text")
    if not text:
        return None
    esc = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    d = f"drawtext=text='{esc}'"
    if params.get("wm_font"):
        d += f":fontfile='{params['wm_font']}'"
    if params.get("wm_color"):
        d += f":fontcolor={params['wm_color']}"
    if params.get("wm_size"):
        d += f":fontsize={params['wm_size']}"
    xy = {
        "tl": "10:10", "tr": "w-tw-10:10",
        "bl": "10:h-th-10", "br": "w-tw-10:h-th-10",
        "center": "(w-tw)/2:(h-th)/2",
    }.get(params.get("wm_pos", "br"), "w-tw-10:h-th-10")
    x, y = xy.split(":")
    d += f":x={x}:y={y}"
    return d


def write_concat_list(list_path: str, input_paths: List[str]) -> None:
    """Write a concat demuxer list file with absolute 'file' lines."""
    with open(list_path, "w", encoding="utf-8") as f:
        for p in input_paths:
            f.write(f"file '{p}'\n")


def _safe_ext(output_format: str) -> str:
    """Sanitise the output container/extension.

    格式**不做白名单**限制 —— ffmpeg 支持什么就允许用什么（mxf / mka / avif /
    y4m / nut …）。但必须净化字符集：只保留字母数字，否则
    ``outputFormat="../../evil"`` 会被直接拼进路径，把文件写出 outputs/ 之外。
    """
    ext = re.sub(r"[^A-Za-z0-9]", "", (output_format or "").strip().lstrip("."))
    return ext or "mp4"


def _output_path(output_name: str, output_format: str, job_id: str,
                 default_stem: str) -> str:
    """Compute the output file path under outputs/."""
    ext = _safe_ext(output_format)
    if output_name:
        stem = Path(output_name).stem
    else:
        stem = default_stem or "output"
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem).strip("._") or "output"
    return str(OUTPUT_DIR / f"{stem}_{job_id}.{ext}")


def build_command(mode: str, input_ids: List[str], output_name: str,
                  output_format: str, params: Dict[str, Any],
                  job_id: str) -> Dict[str, Any]:
    """
    Build the ffmpeg command(s) for a job.

    Returns a dict with:
        commands       : list[list[str]]  (one argv list per pass)
        command        : str              (display string, joined passes)
        output_path    : str              (the final real output file)
        output_paths   : list[str]
        concat_list    : Optional[str]    (path to write for merge mode)
        input_paths    : list[str]        (resolved absolute input paths)

    The same function powers both /api/preview (job_id="preview") and
    /api/jobs (job_id=<real id>), so the command structure is identical.
    """
    params = params or {}
    inputs = resolve_input_paths(input_ids)
    default_stem = Path(inputs[0]).stem if inputs else "output"
    output_path = _output_path(output_name, output_format, job_id, default_stem)
    ext = output_format.strip().lstrip(".") or "mp4"
    concat_list: Optional[str] = None

    argv: List[str] = [FFMPEG_PATH, "-y"]

    if mode == "convert":
        argv += ["-i", inputs[0]]
        vcodec = str(params.get("vcodec", "copy"))
        acodec = str(params.get("acodec", "copy"))
        res = resolve_resolution(params.get("resolution"))
        if res and vcodec == "copy":
            # can't copy AND scale -> force a re-encode
            vcodec = "libx264"
        argv += ["-c:v", vcodec, "-c:a", acodec]
        if res:
            argv += ["-vf", f"scale={res[0]}:{res[1]}"]
        if vcodec != "copy":
            if params.get("crf") is not None:
                argv += ["-crf", str(params["crf"])]
            if params.get("preset"):
                argv += ["-preset", str(params["preset"])]
        if acodec != "copy" and params.get("audiobr"):
            argv += ["-b:a", str(params["audiobr"])]
        argv += [output_path]

    elif mode == "trim":
        start = params.get("start", "0")
        argv += ["-ss", str(start), "-i", inputs[0]]
        if params.get("reencode"):
            argv += ["-c:v", str(params.get("vcodec", "libx264")),
                     "-c:a", str(params.get("acodec", "aac"))]
        else:
            argv += ["-c", "copy"]
        if params.get("duration"):
            argv += ["-t", str(params["duration"])]
        elif params.get("end"):
            argv += ["-to", str(params["end"])]
        argv += [output_path]

    elif mode == "merge":
        concat_list = str(UPLOAD_DIR / f"_concat_{job_id}.txt")
        argv += ["-f", "concat", "-safe", "0", "-i", concat_list]
        if params.get("reencode"):
            argv += ["-c:v", str(params.get("vcodec", "libx264")),
                     "-c:a", str(params.get("acodec", "aac"))]
        else:
            argv += ["-c", "copy"]
        argv += [output_path]

    elif mode == "compress":
        argv += ["-i", inputs[0]]
        crf = params.get("crf", 23)
        preset = str(params.get("preset", "medium"))
        res = resolve_resolution(params.get("resolution"))
        audiobr = str(params.get("audiobr", "128k"))

        if params.get("twopass") and params.get("targetSize"):
            # 2-pass targeting an approximate file size.
            dur = ffprobe_duration(inputs[0]) or 0
            commands: List[List[str]] = []
            if dur > 0:
                total_kbps = int(params["targetSize"] * 8 * 1000 / dur)
                vbr = max(50, total_kbps - 128)
                logfile = str(UPLOAD_DIR / f"_pass_{job_id}")
                pass1 = [FFMPEG_PATH, "-y", "-i", inputs[0],
                         "-c:v", "libx264", "-b:v", f"{vbr}k",
                         "-pass", "1", "-passlogfile", logfile,
                         "-f", "null", os.devnull]
                pass2 = [FFMPEG_PATH, "-y", "-i", inputs[0],
                         "-c:v", "libx264", "-b:v", f"{vbr}k",
                         "-pass", "2", "-passlogfile", logfile,
                         "-b:a", audiobr]
                if res:
                    pass2 += ["-vf", f"scale={res[0]}:{res[1]}"]
                if ext == "mp4":
                    pass2 += ["-movflags", "+faststart"]
                pass2 += [output_path]
                commands = [pass1, pass2]
                command = " && ".join(shlex.join(c) for c in commands)
                return {
                    "commands": commands, "command": command,
                    "output_path": output_path, "output_paths": [output_path],
                    "concat_list": None, "input_paths": inputs,
                }
            # fall through to 1-pass if duration unknown

        argv += ["-c:v", "libx264", "-crf", str(crf), "-preset", preset]
        if res:
            argv += ["-vf", f"scale={res[0]}:{res[1]}"]
        argv += ["-b:a", audiobr]
        if ext == "mp4":
            argv += ["-movflags", "+faststart"]
        argv += [output_path]

    elif mode == "filter":
        argv += ["-i", inputs[0]]
        if params.get("wm_image"):
            wm = resolve_input_paths([params["wm_image"]])
            if wm:
                argv += ["-i", wm[0]]
        vf = build_video_filters(params)
        if vf:
            argv += ["-vf", vf]
        af = build_audio_filters(params)
        if af:
            argv += ["-af", af]
        argv += ["-c:v", str(params.get("vcodec", "libx264")),
                 "-c:a", str(params.get("acodec", "aac"))]
        argv += [output_path]

    elif mode == "custom":
        for i in inputs:
            argv += ["-i", i]
        extra = params.get("extra_args", "")
        if extra and isinstance(extra, str):
            argv += shlex.split(extra)
        argv += [output_path]

    else:
        raise ValueError(f"Unknown mode: {mode}")

    command = shlex.join(argv)
    return {
        "commands": [argv],
        "command": command,
        "output_path": output_path,
        "output_paths": [output_path],
        "concat_list": concat_list,
        "input_paths": inputs,
    }


# --------------------------------------------------------------------------- #
# Job execution
# --------------------------------------------------------------------------- #
def push_event(job_id: str, event: str, data: Dict[str, Any]) -> None:
    """Thread-safe push of an SSE event onto the job's queue."""
    q = EVENT_QUEUES.get(job_id)
    if q and MAIN_LOOP is not None:
        asyncio.run_coroutine_threadsafe(q.put({"event": event, "data": data}),
                                         MAIN_LOOP)


def run_job(job: Dict[str, Any]) -> None:
    """
    Execute a job in a background thread.

    Builds the command, runs ffmpeg (with -progress on stdout), reads stdout
    (progress) and stderr (log) in separate threads, and pushes events to the
    SSE queue until the process terminates or the job is canceled.
    """
    job_id = job["id"]
    mode = job["mode"]
    input_ids = job["inputIds"]
    log_lock = threading.Lock()

    def push(event, data):
        push_event(job_id, event, data)

    try:
        cmd_info = build_command(
            mode, input_ids, job["outputName"], job["outputFormat"],
            job["params"], job_id,
        )
        output_path: str = cmd_info["output_path"]
        commands: List[List[str]] = cmd_info["commands"]
        total_ms = total_duration_ms(input_ids, mode)

        # merge needs a concat list written before execution
        if cmd_info.get("concat_list"):
            write_concat_list(cmd_info["concat_list"], cmd_info["input_paths"])

        job["status"] = "running"
        job["startedAt"] = now_iso()
        print(f"[job {job_id}] started  mode={mode} -> {output_path}")
        push("progress", {"progress": 0, "fps": 0, "speed": "0x",
                          "timeSec": 0, "sizeBytes": 0, "etaSec": None,
                          "status": "running"})

        state = {"out_ms": 0, "speed": "0x", "size": 0, "fps": 0.0}
        state_lock = threading.Lock()

        def read_stdout(proc):
            try:
                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("out_time_ms="):
                        v = line.split("=", 1)[1].strip()
                        with state_lock:
                            try:
                                state["out_ms"] = int(v or 0)
                            except ValueError:
                                pass
                    elif line.startswith("total_size="):
                        v = line.split("=", 1)[1].strip()
                        with state_lock:
                            try:
                                state["size"] = int(v or 0)
                            except ValueError:
                                pass
                    elif line.startswith("fps="):
                        v = line.split("=", 1)[1].strip()
                        with state_lock:
                            try:
                                state["fps"] = float(v or 0)
                            except ValueError:
                                pass
                    elif line.startswith("speed="):
                        with state_lock:
                            state["speed"] = line.split("=", 1)[1].strip()
                    elif line.startswith("progress="):
                        pr = line.split("=", 1)[1].strip()
                        with state_lock:
                            ms = state["out_ms"]
                            sp_raw = state["speed"]
                            # Convert scientific notation like "1.5e+03x" to "1500x"
                            if sp_raw:
                                try:
                                    sp_num = float(sp_raw.rstrip("x"))
                                    sp = f"{sp_num:.0f}x"
                                except ValueError:
                                    sp = sp_raw
                            else:
                                sp = "0x"
                            sz = state["size"]
                            fps = state["fps"]
                        if total_ms and total_ms > 0:
                            prog = min(100, int(ms / total_ms * 100))
                        else:
                            prog = None
                        time_sec = ms / 1_000_000 if ms else 0.0
                        eta = None
                        if sp and sp.endswith("x"):
                            try:
                                sx = float(sp[:-1])
                                if sx > 0 and prog is not None:
                                    remaining = total_ms - ms
                                    eta = int(remaining / 1_000_000 / sx)
                            except ValueError:
                                pass
                        push("progress", {"progress": prog, "fps": fps,
                                          "speed": sp or "0x", "timeSec": time_sec,
                                          "sizeBytes": sz, "etaSec": eta,
                                          "status": "running"})
                        if pr == "end":
                            with state_lock:
                                job["progress"] = 100
            except Exception:
                pass

        def read_stderr(proc):
            try:
                for line in proc.stderr:
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    with log_lock:
                        if len(job["log"]) < LOG_CAP:
                            job["log"].append(line)
                    push("log", {"line": line})
            except Exception:
                pass

        rc = None
        proc = None
        for argv in commands:
            if job.get("cancel"):
                break
            # Insert -progress BEFORE the output file (it is an output option).
            out = argv[-1]
            run_argv = argv[:-1] + ["-nostats", "-progress", "pipe:1", out]
            proc = subprocess.Popen(
                run_argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, encoding="utf-8", errors="replace",
            )
            job["_proc"] = proc
            t_out = threading.Thread(target=read_stdout, args=(proc,), daemon=True)
            t_err = threading.Thread(target=read_stderr, args=(proc,), daemon=True)
            t_out.start()
            t_err.start()
            rc = proc.wait()
            t_out.join()
            t_err.join()

        # ---- finalize ----
        if job.get("cancel"):
            job["status"] = "canceled"
            job["finishedAt"] = now_iso()
            print(f"[job {job_id}] canceled")
            push("error", {"status": "canceled", "error": "Job canceled by user"})
        elif rc == 0 and os.path.exists(output_path):
            size = os.path.getsize(output_path)
            job["status"] = "completed"
            job["progress"] = 100
            job["sizeBytes"] = size
            job["outputPath"] = output_path
            job["finishedAt"] = now_iso()
            print(f"[job {job_id}] completed -> {output_path} ({size} bytes)")
            push("done", {"status": "completed", "outputPath": output_path})
        else:
            err = "\n".join(job["log"][-30:]) or f"ffmpeg exited with code {rc}"
            job["status"] = "failed"
            job["error"] = err
            job["finishedAt"] = now_iso()
            print(f"[job {job_id}] failed rc={rc}")
            push("error", {"status": "failed", "error": err})

    except Exception as exc:  # noqa: BLE001 - surface any builder/run error
        job["status"] = "failed"
        job["error"] = str(exc)
        job["finishedAt"] = now_iso()
        print(f"[job {job_id}] exception: {exc}")
        push("error", {"status": "failed", "error": str(exc)})
    finally:
        # best-effort cleanup of temp helper files
        info = build_command_safe(job)
        if info and info.get("concat_list") and os.path.exists(info["concat_list"]):
            try:
                os.remove(info["concat_list"])
            except OSError:
                pass


def build_command_safe(job: Dict[str, Any]):
    try:
        return build_command(job["mode"], job["inputIds"], job["outputName"],
                             job["outputFormat"], job["params"], job["id"])
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Application lifespan (startup detection of ffmpeg / ffprobe)
# --------------------------------------------------------------------------- #
def _winget_link(name: str) -> Optional[str]:
    """winget 安装的 ffmpeg 会在 %LOCALAPPDATA%\\Microsoft\\WinGet\\Links 下留一个 shim。"""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    p = Path(local) / "Microsoft" / "WinGet" / "Links" / f"{name}.exe"
    return str(p) if p.exists() else None


FFMPEG_CANDIDATES = [
    shutil.which("ffmpeg"),
    # Look beside the app (portable bundle scenario)
    str(_appdir / "ffmpeg.exe"),
    str(_appdir / "bin" / "ffmpeg.exe"),
    _winget_link("ffmpeg"),
]
FFPROBE_CANDIDATES = [
    shutil.which("ffprobe"),
    str(_appdir / "ffprobe.exe"),
    str(_appdir / "bin" / "ffprobe.exe"),
    _winget_link("ffprobe"),
]


def detect_ffmpeg() -> None:
    global FFMPEG_PATH, FFPROBE_PATH, FFMPEG_VERSION, FFMPEG_AVAILABLE, FFPROBE_AVAILABLE

    for c in FFMPEG_CANDIDATES:
        if c and Path(c).exists():
            FFMPEG_PATH = c
            break
    for c in FFPROBE_CANDIDATES:
        if c and Path(c).exists():
            FFPROBE_PATH = c
            break

    if FFMPEG_PATH:
        try:
            out = subprocess.run([FFMPEG_PATH, "-version"],
                                 capture_output=True, text=True, timeout=15)
            FFMPEG_AVAILABLE = out.returncode == 0
            FFMPEG_VERSION = out.stdout.splitlines()[0] if out.stdout else ""
        except Exception:
            FFMPEG_AVAILABLE = False
    if FFPROBE_PATH:
        try:
            out = subprocess.run([FFPROBE_PATH, "-version"],
                                 capture_output=True, text=True, timeout=15)
            FFPROBE_AVAILABLE = out.returncode == 0
        except Exception:
            FFPROBE_AVAILABLE = False

    print(f"[startup] ffmpeg={FFMPEG_PATH} available={FFMPEG_AVAILABLE}")
    print(f"[startup] ffprobe={FFPROBE_PATH} available={FFPROBE_AVAILABLE}")
    if FFMPEG_VERSION:
        print(f"[startup] version: {FFMPEG_VERSION}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    detect_ffmpeg()
    yield
    # nothing to tear down


app = FastAPI(title="FFmpeg Studio", version="1.1.0", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# API routes (declared BEFORE the static mount)
# --------------------------------------------------------------------------- #
@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "ffmpeg": FFMPEG_AVAILABLE,
        "ffprobe": FFPROBE_AVAILABLE,
        "version": FFMPEG_VERSION,
        "path": FFMPEG_PATH,
    }


@app.post("/api/upload")
async def upload(files: List[UploadFile] = File(...)) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for f in files:
        ext = Path(f.filename or "").suffix.lstrip(".") if f.filename else ""
        safe = sanitize_filename(f.filename or "file")
        fid = f"u_{uuid.uuid4().hex[:8]}_{safe}"
        dest = UPLOAD_DIR / fid
        data = await f.read()
        async with aiofiles.open(dest, "wb") as out:
            await out.write(data)
        results.append({
            "id": fid,
            "name": f.filename,
            "stored": f"uploads/{fid}",
            "size": len(data),
            "ext": ext,
        })
    return results


@app.post("/api/probe")
async def probe(file: UploadFile = File(...)) -> Dict[str, Any]:
    suffix = Path(file.filename or "").suffix
    tmp = UPLOAD_DIR / f"_probe_{uuid.uuid4().hex[:8]}{suffix}"
    data = await file.read()
    async with aiofiles.open(tmp, "wb") as out:
        await out.write(data)
    try:
        argv = [FFPROBE_PATH, "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", str(tmp)]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        info = json.loads(proc.stdout or "{}")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    duration = None
    fmt = info.get("format", {})
    if fmt.get("duration") is not None:
        try:
            duration = float(fmt["duration"])
        except (TypeError, ValueError):
            duration = None
    if duration is None:
        for s in info.get("streams", []):
            if s.get("codec_type") == "video" and s.get("duration") is not None:
                try:
                    duration = float(s["duration"])
                except (TypeError, ValueError):
                    pass
                break
    info["duration"] = duration
    return info


@app.post("/api/preview")
async def preview(req: JobRequest) -> Dict[str, Any]:
    info = build_command(req.mode, req.inputIds, req.outputName,
                         req.outputFormat, req.params, "preview")
    return {"command": info["command"], "outputs": info["output_paths"]}


@app.post("/api/jobs")
async def create_job(req: JobRequest) -> Dict[str, Any]:
    job_id = f"j_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    job = {
        "id": job_id,
        "mode": req.mode,
        "inputIds": req.inputIds,
        "inputPaths": resolve_input_paths(req.inputIds),
        "outputName": req.outputName,
        "outputFormat": req.outputFormat,
        "params": req.params or {},
        "status": "queued",
        "progress": None,
        "fps": 0,
        "speed": "0x",
        "timeSec": 0,
        "sizeBytes": 0,
        "etaSec": None,
        "log": [],
        "outputPath": None,
        "error": None,
        "createdAt": now_iso(),
        "startedAt": None,
        "finishedAt": None,
        "cancel": False,
        "_proc": None,
    }
    JOBS[job_id] = job
    EVENT_QUEUES[job_id] = asyncio.Queue()

    # Start processing in a background thread (non-blocking request).
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return {"id": job_id, "status": "queued"}


@app.get("/api/jobs")
async def list_jobs() -> List[Dict[str, Any]]:
    summary: List[Dict[str, Any]] = []
    for job in JOBS.values():
        # Normalize speed for display (e.g. "1.5e+03x" -> "1500x")
        raw_sp = job.get("speed", "0x")
        if raw_sp:
            try:
                sp_num = float(raw_sp.rstrip("x"))
                display_sp = f"{sp_num:.0f}x"
            except ValueError:
                display_sp = raw_sp
        else:
            display_sp = "0x"
        summary.append({
            "id": job["id"],
            "mode": job["mode"],
            "status": job["status"],
            "progress": job["progress"],
            "outputName": job["outputName"],
            "createdAt": job["createdAt"],
            "sizeBytes": job.get("sizeBytes"),
            "speed": display_sp,
            "fps": job.get("fps"),
            "timeSec": job.get("timeSec"),
            "etaSec": job.get("etaSec"),
            "error": job.get("error"),
        })
    # newest first
    summary.sort(key=lambda j: j["createdAt"], reverse=True)
    return summary


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> Dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    # Strip private / non-serializable fields (e.g. the subprocess handle).
    return {k: v for k, v in job.items() if not k.startswith("_")}


@app.get("/api/jobs/{job_id}/stream")
async def job_stream(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    q = EVENT_QUEUES.setdefault(job_id, asyncio.Queue())

    async def gen():
        # If the job already finished before the client connected, emit a
        # terminal event immediately instead of hanging on an empty queue.
        if job["status"] in ("completed", "failed", "canceled"):
            if job["status"] == "completed":
                yield sse("done", {"status": "completed",
                                   "outputPath": job.get("outputPath")})
            else:
                yield sse("error", {"status": job["status"],
                                    "error": job.get("error") or "canceled"})
            return

        # emit current snapshot
        yield sse("progress", {
            "progress": job["progress"], "fps": job["fps"],
            "speed": job["speed"], "timeSec": job["timeSec"],
            "sizeBytes": job["sizeBytes"], "etaSec": job["etaSec"],
            "status": job["status"],
        })

        while True:
            item = await q.get()
            yield sse(item["event"], item["data"])
            if item["event"] in ("done", "error"):
                break

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> Dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    job["cancel"] = True
    proc = job.get("_proc")
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass

        def killer(p):
            time.sleep(2)
            try:
                if p.poll() is None:
                    p.kill()
            except Exception:
                pass

        threading.Thread(target=killer, args=(proc,), daemon=True).start()
    return {"ok": True}


@app.get("/api/download/{job_id}")
async def download(job_id: str):
    job = JOBS.get(job_id)
    if (not job or job["status"] != "completed"
            or not job.get("outputPath")
            or not os.path.exists(job["outputPath"])):
        raise HTTPException(status_code=404, detail="output not available")
    return FileResponse(job["outputPath"],
                        filename=os.path.basename(job["outputPath"]),
                        media_type="application/octet-stream")


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str) -> Dict[str, Any]:
    job = JOBS.pop(job_id, None)
    EVENT_QUEUES.pop(job_id, None)
    if job and job.get("outputPath") and os.path.exists(job["outputPath"]):
        try:
            os.remove(job["outputPath"])
        except OSError:
            pass
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Serve the frontend (after /api routes so they are never shadowed)
# --------------------------------------------------------------------------- #
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn

    # 默认只监听本机回环地址：该服务没有鉴权，绑 0.0.0.0 会让同网段
    # （如校园网 / 公共 Wi-Fi）的任何人操控你的 ffmpeg 并读写本机文件。
    # 确实需要局域网访问时，显式设置 FFSTUDIO_HOST=0.0.0.0。
    host = os.environ.get("FFSTUDIO_HOST", "127.0.0.1")
    port = int(os.environ.get("FFSTUDIO_PORT", "8787"))
    uvicorn.run(app, host=host, port=port, log_level="info")
