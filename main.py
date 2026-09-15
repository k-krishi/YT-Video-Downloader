#!/usr/bin/env python3
"""YouTube video downloader with a simple Tkinter GUI."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


def _configure_ssl_certs() -> None:
    """Use certifi CA bundle when Python's system certs are missing (common on macOS)."""
    try:
        import certifi
    except ImportError:
        return

    cert_path = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", cert_path)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", cert_path)
    os.environ.setdefault("CURL_CA_BUNDLE", cert_path)


_configure_ssl_certs()

import yt_dlp
from yt_dlp.utils import sanitize_filename


@dataclass(frozen=True)
class FormatOption:
    label: str
    quality: str
    codec: str
    container: str
    size_label: str
    format_selector: str
    sort_key: tuple[int, int, int, int]
    is_audio_only: bool = False
    merge_output_format: str | None = None
    remux_video: str | None = None
    recode_video: str | None = None
    embed_subtitles: bool = False


# YouTube quality labels (format_note), not raw pixel height — ultrawide 4K can be 3840x1920.
_RESOLUTION_LABELS: dict[int, str] = {
    4320: "8K (4320p)",
    2160: "4K (2160p)",
    1440: "1440p (QHD)",
    1080: "1080p (Full HD)",
    720: "720p (HD)",
    480: "480p",
    360: "360p",
    240: "240p",
    144: "144p",
}
_STANDARD_HEIGHTS = tuple(sorted(_RESOLUTION_LABELS.keys(), reverse=True))
_NOTE_RES = re.compile(r"(\d{3,4})p", re.IGNORECASE)
# Common YouTube widths → the quality tier YouTube shows in the UI
_WIDTH_TO_TIER: tuple[tuple[int, int], ...] = (
    (7680, 4320),
    (3840, 2160),
    (2560, 1440),
    (1920, 1080),
    (1280, 720),
    (854, 480),
    (640, 360),
    (426, 240),
    (256, 144),
)


def _format_bytes(size: int | float | None) -> str:
    if size is None or size <= 0:
        return "Unknown"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _format_speed(speed: float | None) -> str:
    if not speed or speed <= 0:
        return "—"
    return f"{_format_bytes(speed)}/s"


def _format_eta(seconds: float | int | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _short_codec(codec: str) -> str:
    if not codec or codec == "none":
        return "none"
    codec = codec.lower()
    if "avc" in codec or "h264" in codec:
        return "H.264"
    if "vp9" in codec or "vp09" in codec:
        return "VP9"
    if "av01" in codec or "av1" in codec:
        return "AV1"
    if "aac" in codec or "mp4a" in codec:
        return "AAC"
    if "opus" in codec:
        return "Opus"
    return codec.split(".")[0][:12]


def _codec_family(vcodec: str) -> str | None:
    codec = (vcodec or "").lower()
    if "avc" in codec or "h264" in codec:
        return "h264"
    if "vp9" in codec or "vp09" in codec:
        return "vp9"
    if "av01" in codec or "av1" in codec:
        return "av1"
    return None


def _format_size(fmt: dict) -> int | None:
    return fmt.get("filesize") or fmt.get("filesize_approx")


def _normalize_height(height: int) -> int:
    """Map near-standard heights onto the common ladder (avoids odd labels like 1918p)."""
    for standard in _STANDARD_HEIGHTS:
        if abs(height - standard) <= 16:
            return standard
    return height


def _resolution_pixels(fmt: dict) -> int | None:
    """YouTube quality tier (e.g. 2160 for 4K), not always equal to pixel height."""
    note = fmt.get("format_note") or ""
    match = _NOTE_RES.search(note)
    if match:
        return _normalize_height(int(match.group(1)))

    width = fmt.get("width")
    height = fmt.get("height")
    if width:
        for std_w, std_h in _WIDTH_TO_TIER:
            if abs(int(width) - std_w) <= 16:
                return std_h
    if height and width:
        return _normalize_height(min(int(height), int(width)))
    if height:
        return _normalize_height(int(height))
    return None


def _quality_label(resolution: int, fps: float | None = None) -> str:
    base = _RESOLUTION_LABELS.get(resolution, f"{resolution}p")
    if fps and fps > 31:
        return f"{base} @ {int(fps)}fps"
    return base


def _video_rank(fmt: dict) -> tuple[float, float, int]:
    return (fmt.get("tbr") or 0, fmt.get("fps") or 0, fmt.get("filesize") or fmt.get("filesize_approx") or 0)


def _pick_audio(audio_formats: list[dict], prefer_opus: bool) -> dict | None:
    if not audio_formats:
        return None

    def score(fmt: dict) -> tuple[int, float]:
        acodec = (fmt.get("acodec") or "").lower()
        ext = (fmt.get("ext") or "").lower()
        is_opus = "opus" in acodec or ext == "webm"
        is_aac = "mp4a" in acodec or "aac" in acodec or ext in {"m4a", "mp4"}
        family_score = 0
        if prefer_opus and is_opus:
            family_score = 2
        elif not prefer_opus and is_aac:
            family_score = 2
        elif is_opus or is_aac:
            family_score = 1
        return (family_score, fmt.get("abr") or 0)

    return max(audio_formats, key=score)


def _make_video_option(
    *,
    video_fmt: dict,
    audio: dict | None,
    resolution: int,
    family: str,
    container: str,
    codec_label: str | None = None,
    remux_video: str | None = None,
    recode_video: str | None = None,
    embed_subtitles: bool = False,
    codec_rank: int,
    container_rank: int,
) -> FormatOption:
    if audio:
        selector = f"{video_fmt['format_id']}+{audio['format_id']}"
        video_size = _format_size(video_fmt)
        audio_size = _format_size(audio)
        size = video_size + audio_size if video_size and audio_size else None
        codec = codec_label or (
            f"{_short_codec(video_fmt.get('vcodec', ''))} + {_short_codec(audio.get('acodec', ''))}"
        )
    else:
        selector = str(video_fmt["format_id"])
        size = _format_size(video_fmt)
        codec = codec_label or _short_codec(video_fmt.get("vcodec", ""))

    quality = _quality_label(resolution, video_fmt.get("fps"))
    # Re-encode path: merge to MKV first (VP9/Opus-safe), then convert to MP4 with libx264.
    merge_fmt = "mkv" if recode_video else container.lower()
    return FormatOption(
        label=quality,
        quality=quality,
        codec=codec,
        container=container,
        size_label=_format_bytes(size),
        format_selector=selector,
        sort_key=(resolution, codec_rank, container_rank, int(video_fmt.get("tbr") or 0)),
        merge_output_format=merge_fmt,
        remux_video=remux_video,
        recode_video=recode_video,
        embed_subtitles=embed_subtitles,
    )


def _unique_output_stem(output_dir: Path, stem: str) -> str:
    """Return stem, or 'stem (1)', 'stem (2)', … if that name already exists."""
    cleaned = (stem or "video").strip().rstrip(" .") or "video"
    existing = {path.stem for path in output_dir.iterdir() if path.is_file()} if output_dir.is_dir() else set()
    if cleaned not in existing:
        return cleaned
    index = 1
    while f"{cleaned} ({index})" in existing:
        index += 1
    return f"{cleaned} ({index})"


def _resolve_downloaded_path(info: dict | None, output_dir: Path, stem: str) -> Path | None:
    if info is None:
        return None
    candidates: list[Path] = []
    for key in ("filepath", "_filename", "filename"):
        value = info.get(key)
        if value:
            candidates.append(Path(value))
    for item in info.get("requested_downloads") or []:
        value = item.get("filepath")
        if value:
            candidates.append(Path(value))
    for path in candidates:
        if path.is_file():
            return path
    valid_media_exts = {".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".opus", ".flac", ".aac", ".wav", ".ogg"}
    sub_or_temp_exts = {".vtt", ".srt", ".ass", ".ttml", ".srv1", ".srv2", ".srv3", ".lrc", ".sub", ".part", ".ytdl", ".temp"}
    matches = sorted(output_dir.glob(f"{stem}.*"))
    for path in matches:
        if path.is_file() and path.suffix.lower() in valid_media_exts:
            return path
    for path in matches:
        if path.is_file() and path.suffix.lower() not in sub_or_temp_exts:
            return path
    return None


def _cleanup_external_subtitles(output_dir: Path, stem: str) -> None:
    """Ensure no separate subtitle files remain on disk after embedding into the container."""
    if not output_dir.is_dir():
        return
    sub_exts = {".vtt", ".srt", ".ass", ".ttml", ".srv1", ".srv2", ".srv3", ".lrc", ".sub"}
    for file in output_dir.iterdir():
        if (
            file.is_file()
            and file.suffix.lower() in sub_exts
            and (file.name == f"{stem}{file.suffix}" or file.name.startswith(f"{stem}."))
        ):
            try:
                file.unlink()
            except OSError:
                pass



def _convert_to_compatible_mp4(source: Path, destination: Path) -> None:
    """Re-encode to H.264/AAC MP4 that plays in QuickTime, VLC, browsers, etc."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to convert VP9/AV1 to MP4. Install ffmpeg and try again.")

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        lines = [line.strip() for line in (result.stderr or result.stdout or "").splitlines() if line.strip()]
        error_lines = [line for line in lines if not line.startswith("frame=") and not line.startswith("size=")]
        message = error_lines[-1] if error_lines else (lines[-1] if lines else "Unknown FFmpeg error")
        raise RuntimeError(f"MP4 conversion failed: {message}")
    if not destination.is_file() or destination.stat().st_size <= 0:
        raise RuntimeError("MP4 conversion failed: output file was not created.")


def _build_format_options(info: dict) -> list[FormatOption]:
    formats = info.get("formats") or []
    options: list[FormatOption] = []
    seen_keys: set[tuple[str, str, str]] = set()

    audio_formats = [
        fmt
        for fmt in formats
        if fmt.get("acodec") != "none" and fmt.get("vcodec") == "none"
    ]

    # Best adaptive stream per (YouTube quality tier, codec family)
    video_only: dict[tuple[int, str], dict] = {}
    for fmt in formats:
        if fmt.get("vcodec") == "none" or fmt.get("acodec") != "none":
            continue
        family = _codec_family(fmt.get("vcodec", ""))
        if family not in {"h264", "vp9", "av1"}:
            continue
        resolution = _resolution_pixels(fmt)
        if resolution is None:
            continue
        key = (resolution, family)
        current = video_only.get(key)
        if current is None or _video_rank(fmt) > _video_rank(current):
            video_only[key] = fmt

    by_resolution: dict[int, dict[str, dict]] = {}
    for (resolution, family), fmt in video_only.items():
        by_resolution.setdefault(resolution, {})[family] = fmt

    for resolution in sorted(by_resolution.keys(), reverse=True):
        families = by_resolution[resolution]
        h264 = families.get("h264")
        vp9 = families.get("vp9")
        av1 = families.get("av1")
        efficient = vp9 or av1

        if h264:
            audio = _pick_audio(audio_formats, prefer_opus=False)
            option = _make_video_option(
                video_fmt=h264,
                audio=audio,
                resolution=resolution,
                family="h264",
                container="MP4",
                codec_rank=3,
                container_rank=2,
            )
            key = (option.format_selector, option.container, option.codec)
            if key not in seen_keys:
                seen_keys.add(key)
                options.append(option)

        if efficient:
            family = "vp9" if vp9 else "av1"
            audio = _pick_audio(audio_formats, prefer_opus=True)
            # Native high-efficiency download as MKV (good for subs + VP9/AV1 + Opus)
            option = _make_video_option(
                video_fmt=efficient,
                audio=audio,
                resolution=resolution,
                family=family,
                container="MKV",
                remux_video="mkv",
                embed_subtitles=True,
                codec_rank=2 if family == "vp9" else 1,
                container_rank=1,
            )
            key = (option.format_selector, option.container, option.codec)
            if key not in seen_keys:
                seen_keys.add(key)
                options.append(option)

            # When YouTube has no H.264 at this tier (typical for 4K/1440p), offer MP4 via re-encode
            if h264 is None:
                aac_audio = _pick_audio(audio_formats, prefer_opus=False)
                source = _short_codec(efficient.get("vcodec", ""))
                option = _make_video_option(
                    video_fmt=efficient,
                    audio=aac_audio or audio,
                    resolution=resolution,
                    family=family,
                    container="MP4",
                    codec_label=f"H.264 + AAC (from {source})",
                    recode_video="mp4",
                    codec_rank=2 if family == "vp9" else 1,
                    container_rank=0,
                )
                key = (option.format_selector, option.container, option.codec)
                if key not in seen_keys:
                    seen_keys.add(key)
                    options.append(option)

    # Progressive fallbacks when adaptive streams are missing
    covered = set(by_resolution.keys())
    for fmt in formats:
        if fmt.get("vcodec") == "none" or fmt.get("acodec") == "none":
            continue
        family = _codec_family(fmt.get("vcodec", ""))
        if family not in {"h264", "vp9", "av1"}:
            continue
        resolution = _resolution_pixels(fmt)
        if resolution is None or resolution in covered:
            continue
        covered.add(resolution)
        container = "MP4" if family == "h264" else "MKV"
        option = _make_video_option(
            video_fmt=fmt,
            audio=None,
            resolution=resolution,
            family=family,
            container=container,
            remux_video="mkv" if container == "MKV" else None,
            embed_subtitles=container == "MKV",
            codec_rank=3 if family == "h264" else (2 if family == "vp9" else 1),
            container_rank=1,
        )
        key = (option.format_selector, option.container, option.codec)
        if key not in seen_keys:
            seen_keys.add(key)
            options.append(option)

    for fmt in sorted(audio_formats, key=lambda item: item.get("abr") or 0, reverse=True):
        selector = str(fmt["format_id"])
        key = (selector, (fmt.get("ext") or "?").upper(), _short_codec(fmt.get("acodec", "")))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        abr = fmt.get("abr")
        quality = f"Audio {int(abr)}kbps" if abr else "Audio"
        options.append(
            FormatOption(
                label=quality,
                quality=quality,
                codec=_short_codec(fmt.get("acodec", "")),
                container=(fmt.get("ext") or "?").upper(),
                size_label=_format_bytes(_format_size(fmt)),
                format_selector=selector,
                sort_key=(-1, 0, 0, int(fmt.get("abr") or 0)),
                is_audio_only=True,
            )
        )

    options.sort(key=lambda item: item.sort_key, reverse=True)
    return options


# Dark theme palette
COLORS = {
    "bg": "#0a0a0a",
    "surface": "#141414",
    "surface_alt": "#1a1a1a",
    "border": "#2a2a2a",
    "text": "#f2f2f2",
    "muted": "#9a9a9a",
    "accent": "#e11d48",
    "accent_hover": "#be123c",
    "accent_text": "#ffffff",
    "entry_bg": "#111111",
    "select_bg": "#e11d48",
    "select_fg": "#ffffff",
    "tree_bg": "#111111",
    "tree_alt": "#161616",
    "progress_trough": "#1f1f1f",
    "progress_bar": "#e11d48",
}


class YouTubeDownloaderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("YouTube Downloader")
        self.root.geometry("780x680")
        self.root.minsize(640, 560)
        self.root.configure(bg=COLORS["bg"])

        self.url_var = tk.StringVar()
        self.output_dir_var = tk.StringVar(value=str(Path.home() / "Downloads"))
        self.status_var = tk.StringVar(value="Paste a URL, click Load qualities, then pick one to download.")
        self.progress_var = tk.DoubleVar(value=0.0)

        self._format_options: list[FormatOption] = []
        self._video_title = ""
        self._busy = False

        self._apply_theme()
        self._build_ui()

    def _apply_theme(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        c = COLORS
        style.configure(".", background=c["bg"], foreground=c["text"], fieldbackground=c["entry_bg"])
        style.configure("TFrame", background=c["bg"])
        style.configure("Card.TFrame", background=c["surface"])
        style.configure(
            "TLabel",
            background=c["bg"],
            foreground=c["text"],
            font=("SF Pro Text", 12) if self.root.tk.call("tk", "windowingsystem") == "aqua" else ("Segoe UI", 11),
        )
        style.configure(
            "Muted.TLabel",
            background=c["bg"],
            foreground=c["muted"],
            font=("SF Pro Text", 11) if self.root.tk.call("tk", "windowingsystem") == "aqua" else ("Segoe UI", 10),
        )
        style.configure(
            "Title.TLabel",
            background=c["bg"],
            foreground=c["text"],
            font=("SF Pro Display", 22, "bold")
            if self.root.tk.call("tk", "windowingsystem") == "aqua"
            else ("Segoe UI", 20, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=c["bg"],
            foreground=c["muted"],
            font=("SF Pro Text", 11) if self.root.tk.call("tk", "windowingsystem") == "aqua" else ("Segoe UI", 10),
        )
        style.configure(
            "Section.TLabel",
            background=c["bg"],
            foreground=c["muted"],
            font=("SF Pro Text", 10) if self.root.tk.call("tk", "windowingsystem") == "aqua" else ("Segoe UI", 9),
        )
        style.configure(
            "TEntry",
            fieldbackground=c["entry_bg"],
            foreground=c["text"],
            insertcolor=c["text"],
            bordercolor=c["border"],
            lightcolor=c["border"],
            darkcolor=c["border"],
            padding=8,
        )
        style.map(
            "TEntry",
            fieldbackground=[("focus", c["surface_alt"])],
            bordercolor=[("focus", c["accent"])],
            lightcolor=[("focus", c["accent"])],
            darkcolor=[("focus", c["accent"])],
        )
        style.configure(
            "TButton",
            background=c["surface_alt"],
            foreground=c["text"],
            bordercolor=c["border"],
            lightcolor=c["surface_alt"],
            darkcolor=c["surface_alt"],
            focuscolor=c["surface_alt"],
            padding=(14, 8),
            font=("SF Pro Text", 11) if self.root.tk.call("tk", "windowingsystem") == "aqua" else ("Segoe UI", 10),
        )
        style.map(
            "TButton",
            background=[("active", c["border"]), ("disabled", c["surface"])],
            foreground=[("disabled", "#555555")],
            bordercolor=[("active", c["muted"])],
        )
        style.configure(
            "Accent.TButton",
            background=c["accent"],
            foreground=c["accent_text"],
            bordercolor=c["accent"],
            lightcolor=c["accent"],
            darkcolor=c["accent"],
            focuscolor=c["accent"],
            padding=(14, 10),
            font=("SF Pro Text", 12, "bold")
            if self.root.tk.call("tk", "windowingsystem") == "aqua"
            else ("Segoe UI", 11, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("active", c["accent_hover"]), ("disabled", "#3a151c")],
            foreground=[("disabled", "#7a4a55")],
            bordercolor=[("active", c["accent_hover"]), ("disabled", "#3a151c")],
            lightcolor=[("active", c["accent_hover"]), ("disabled", "#3a151c")],
            darkcolor=[("active", c["accent_hover"]), ("disabled", "#3a151c")],
        )
        style.configure(
            "Treeview",
            background=c["tree_bg"],
            fieldbackground=c["tree_bg"],
            foreground=c["text"],
            bordercolor=c["border"],
            lightcolor=c["border"],
            darkcolor=c["border"],
            rowheight=28,
            font=("SF Pro Text", 11) if self.root.tk.call("tk", "windowingsystem") == "aqua" else ("Segoe UI", 10),
        )
        style.configure(
            "Treeview.Heading",
            background=c["surface_alt"],
            foreground=c["muted"],
            bordercolor=c["border"],
            relief="flat",
            padding=6,
            font=("SF Pro Text", 10) if self.root.tk.call("tk", "windowingsystem") == "aqua" else ("Segoe UI", 9),
        )
        style.map(
            "Treeview",
            background=[("selected", c["select_bg"])],
            foreground=[("selected", c["select_fg"])],
        )
        style.map(
            "Treeview.Heading",
            background=[("active", c["border"])],
            foreground=[("active", c["text"])],
        )
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor=c["progress_trough"],
            background=c["progress_bar"],
            bordercolor=c["border"],
            lightcolor=c["progress_bar"],
            darkcolor=c["progress_bar"],
            thickness=8,
        )
        style.configure(
            "Vertical.TScrollbar",
            background=c["surface_alt"],
            troughcolor=c["bg"],
            bordercolor=c["bg"],
            arrowcolor=c["muted"],
            lightcolor=c["surface_alt"],
            darkcolor=c["surface_alt"],
        )
        style.map(
            "Vertical.TScrollbar",
            background=[("active", c["border"])],
        )
        style.configure(
            "Card.TLabelframe",
            background=c["surface"],
            foreground=c["muted"],
            bordercolor=c["border"],
            lightcolor=c["border"],
            darkcolor=c["border"],
            relief="flat",
        )
        style.configure(
            "Card.TLabelframe.Label",
            background=c["surface"],
            foreground=c["muted"],
            font=("SF Pro Text", 10) if self.root.tk.call("tk", "windowingsystem") == "aqua" else ("Segoe UI", 9),
        )

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=(20, 18, 20, 16))
        main.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main, text="YouTube Downloader", style="Title.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 2)
        )
        ttk.Label(
            main,
            text="Download videos and audio in the quality you choose.",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 16))

        ttk.Label(main, text="YOUTUBE URL", style="Section.TLabel").grid(
            row=2, column=0, sticky="w", pady=(0, 4)
        )

        url_row = ttk.Frame(main)
        url_row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        url_row.columnconfigure(0, weight=1)

        url_entry = ttk.Entry(url_row, textvariable=self.url_var)
        url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        url_entry.focus_set()

        self.load_btn = ttk.Button(url_row, text="Load qualities", command=self._start_fetch_formats)
        self.load_btn.grid(row=0, column=1, sticky="e")

        ttk.Label(main, text="SAVE TO", style="Section.TLabel").grid(
            row=4, column=0, sticky="w", pady=(0, 4)
        )
        path_row = ttk.Frame(main)
        path_row.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        path_row.columnconfigure(0, weight=1)
        ttk.Entry(path_row, textvariable=self.output_dir_var).grid(
            row=0, column=0, sticky="ew", padx=(0, 8)
        )
        ttk.Button(path_row, text="Browse…", command=self._browse_output_dir).grid(
            row=0, column=1, sticky="e"
        )

        ttk.Label(main, text="AVAILABLE QUALITIES", style="Section.TLabel").grid(
            row=6, column=0, sticky="w", pady=(0, 2)
        )
        ttk.Label(
            main,
            text="H.264 → MP4. VP9/AV1 → MKV (English subs embedded into single MKV file). “From VP9/AV1” MP4 is re-encoded to H.264.",
            style="Muted.TLabel",
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 6))

        quality_frame = ttk.Frame(main, style="Card.TFrame")
        quality_frame.grid(row=8, column=0, columnspan=2, sticky="nsew")

        self.quality_tree = ttk.Treeview(
            quality_frame,
            columns=("quality", "codec", "container", "size"),
            show="headings",
            height=10,
            selectmode="browse",
        )
        self.quality_tree.heading("quality", text="Quality")
        self.quality_tree.heading("codec", text="Codec")
        self.quality_tree.heading("container", text="Container")
        self.quality_tree.heading("size", text="Size")
        self.quality_tree.column("quality", width=170, stretch=False)
        self.quality_tree.column("codec", width=170, stretch=True)
        self.quality_tree.column("container", width=90, stretch=False)
        self.quality_tree.column("size", width=90, stretch=False, anchor="e")
        self.quality_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        quality_scroll = ttk.Scrollbar(quality_frame, command=self.quality_tree.yview)
        quality_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.quality_tree.configure(yscrollcommand=quality_scroll.set)

        self.progress = ttk.Progressbar(
            main,
            variable=self.progress_var,
            maximum=100,
            mode="determinate",
        )
        self.progress.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(16, 10))

        self.download_btn = ttk.Button(
            main,
            text="Download selected quality",
            style="Accent.TButton",
            command=self._start_download,
            state="disabled",
        )
        self.download_btn.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        ttk.Label(main, textvariable=self.status_var, style="Muted.TLabel", wraplength=720).grid(
            row=11, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        log_frame = ttk.LabelFrame(main, text="Log", style="Card.TLabelframe", padding=10)
        log_frame.grid(row=12, column=0, columnspan=2, sticky="nsew", pady=(4, 0))

        self.log_text = tk.Text(
            log_frame,
            height=8,
            wrap="word",
            state="disabled",
            bg=COLORS["tree_bg"],
            fg=COLORS["muted"],
            insertbackground=COLORS["text"],
            selectbackground=COLORS["select_bg"],
            selectforeground=COLORS["select_fg"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=("Menlo", 11) if self.root.tk.call("tk", "windowingsystem") == "aqua" else ("Consolas", 10),
            padx=6,
            pady=4,
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=log_scroll.set)

        main.columnconfigure(0, weight=1)
        main.rowconfigure(8, weight=2)
        main.rowconfigure(12, weight=1)

    def _browse_output_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_dir_var.get())
        if selected:
            self.output_dir_var.set(selected)

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)

    def _set_progress(self, value: float) -> None:
        self.progress_var.set(max(0.0, min(100.0, value)))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.load_btn.configure(state=state)
        self.download_btn.configure(state="disabled" if busy else ("normal" if self._format_options else "disabled"))

    def _clear_quality_list(self) -> None:
        self.quality_tree.delete(*self.quality_tree.get_children())
        self._format_options = []
        self.download_btn.configure(state="disabled")

    def _populate_quality_list(self, title: str, options: list[FormatOption]) -> None:
        self._clear_quality_list()
        self._video_title = title
        self._format_options = options

        for index, option in enumerate(options):
            self.quality_tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(option.quality, option.codec, option.container, option.size_label),
            )

        if options:
            self.quality_tree.selection_set("0")
            self.quality_tree.focus("0")
            self.download_btn.configure(state="normal")

    def _get_selected_format(self) -> FormatOption | None:
        selection = self.quality_tree.selection()
        if not selection:
            return None
        index = int(selection[0])
        if index < 0 or index >= len(self._format_options):
            return None
        return self._format_options[index]

    def _start_fetch_formats(self) -> None:
        if self._busy:
            return

        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Missing URL", "Please enter a YouTube URL.")
            return

        self._set_busy(True)
        self._set_progress(0)
        self._clear_quality_list()
        self._set_status("Fetching available qualities…")
        self._append_log(f"Fetching formats: {url}")

        threading.Thread(target=self._fetch_formats_worker, args=(url,), daemon=True).start()

    def _fetch_formats_worker(self, url: str) -> None:
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "skip_download": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if not info:
                raise RuntimeError("Could not read video information.")

            options = _build_format_options(info)
            if not options:
                raise RuntimeError("No downloadable formats were found for this video.")

            title = info.get("title", "Video")
            self.root.after(0, self._populate_quality_list, title, options)
            self.root.after(0, self._set_status, f"Loaded {len(options)} qualities for: {title}")
            self.root.after(0, self._append_log, f"Found {len(options)} qualities for: {title}")
        except Exception as exc:
            self.root.after(0, self._set_status, "Could not load qualities.")
            self.root.after(0, self._append_log, f"Error: {exc}")
            self.root.after(
                0,
                messagebox.showerror,
                "Could not load qualities",
                str(exc),
            )
        finally:
            self.root.after(0, self._set_busy, False)

    def _start_download(self) -> None:
        if self._busy:
            return

        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Missing URL", "Please enter a YouTube URL.")
            return

        selected = self._get_selected_format()
        if selected is None:
            messagebox.showwarning("No quality selected", "Load qualities and choose one from the list.")
            return

        output_dir = Path(self.output_dir_var.get()).expanduser()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Invalid folder", f"Could not use save folder:\n{exc}")
            return

        self._set_busy(True)
        self._set_progress(0)
        self._set_status(f"Downloading {selected.quality}…")
        self._append_log(f"URL: {url}")
        self._append_log(f"Quality: {selected.quality} ({selected.format_selector})")
        self._append_log(f"Folder: {output_dir}")

        threading.Thread(
            target=self._download_worker,
            args=(url, output_dir, selected),
            daemon=True,
        ).start()

    def _download_worker(self, url: str, output_dir: Path, selected: FormatOption) -> None:
        last_update = 0.0

        def progress_hook(data: dict) -> None:
            nonlocal last_update
            status = data.get("status")
            if status == "downloading":
                now = time.time()
                downloaded = data.get("downloaded_bytes", 0) or 0
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                is_complete = bool(total and downloaded >= total)
                if now - last_update < 0.1 and not is_complete:
                    return
                last_update = now

                speed = _format_speed(data.get("speed"))
                eta = _format_eta(data.get("eta"))
                if total:
                    percent = downloaded / total * 100
                    self.root.after(0, self._set_progress, percent)
                    self.root.after(
                        0,
                        self._set_status,
                        f"Downloading {selected.quality}… {percent:.1f}% · {speed} · ETA {eta}",
                    )
                else:
                    self.root.after(
                        0,
                        self._set_status,
                        f"Downloading {selected.quality}… {_format_bytes(downloaded)} · {speed}",
                    )
            elif status == "finished":
                self.root.after(0, self._set_progress, 100)
                filename = data.get("filename", "file")
                self.root.after(0, self._append_log, f"Download finished: {filename}")

        title_hint = self._video_title or "video"
        stem = _unique_output_stem(output_dir, sanitize_filename(title_hint, restricted=False))
        self.root.after(0, self._append_log, f"Saving as: {stem}")

        ydl_opts: dict = {
            "format": selected.format_selector,
            "outtmpl": str(output_dir / f"{stem}.%(ext)s"),
            "progress_hooks": [progress_hook],
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "overwrites": False,
        }

        # VP9/AV1 → MP4: download/merge as MKV, then re-encode ourselves (compatible H.264).
        if selected.recode_video:
            ydl_opts["merge_output_format"] = "mkv"
        elif selected.merge_output_format:
            ydl_opts["merge_output_format"] = selected.merge_output_format
            if selected.remux_video:
                ydl_opts["remuxvideo"] = selected.remux_video

        if selected.embed_subtitles and not selected.recode_video:
            self.root.after(
                0,
                self._append_log,
                "Subtitles: English (embedded directly into single MKV file)",
            )
            ydl_opts.update(
                {
                    "writesubtitles": True,
                    "writeautomaticsub": True,
                    "embedsubtitles": True,
                    "subtitleslangs": ["en.*", "en"],
                    "postprocessors": [
                        {
                            "key": "FFmpegEmbedSubtitle",
                            "already_have_subtitle": False,
                        }
                    ],
                }
            )

        if selected.is_audio_only:
            ydl_opts.pop("merge_output_format", None)
            ydl_opts.pop("remuxvideo", None)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

            if selected.embed_subtitles:
                _cleanup_external_subtitles(output_dir, stem)

            saved_path = _resolve_downloaded_path(info, output_dir, stem)
            title = (info.get("title") if info else None) or self._video_title or "video"

            if selected.recode_video:
                if saved_path is None:
                    raise RuntimeError("Download finished but the video file could not be found for conversion.")
                mp4_path = saved_path.with_suffix(".mp4")
                if mp4_path.exists() and mp4_path.resolve() != saved_path.resolve():
                    mp4_path = output_dir / f"{_unique_output_stem(output_dir, stem)}.mp4"
                self.root.after(0, self._set_status, "Converting to compatible MP4 (H.264)…")
                self.root.after(0, self._append_log, f"Converting to MP4: {mp4_path.name}")
                _convert_to_compatible_mp4(saved_path, mp4_path)
                if saved_path.resolve() != mp4_path.resolve() and saved_path.is_file():
                    try:
                        saved_path.unlink()
                    except OSError:
                        pass
                saved_path = mp4_path

            final_name = saved_path.name if saved_path else output_dir.name
            self.root.after(0, self._set_status, f"Done: {title} ({selected.quality})")
            self.root.after(0, self._append_log, f"Saved: {final_name}")
            self.root.after(
                0,
                messagebox.showinfo,
                "Download complete",
                f"Saved to:\n{saved_path if saved_path else output_dir}",
            )
        except Exception as exc:
            self.root.after(0, self._set_status, "Download failed.")
            self.root.after(0, self._append_log, f"Error: {exc}")
            self.root.after(
                0,
                messagebox.showerror,
                "Download failed",
                str(exc),
            )
        finally:
            self.root.after(0, self._set_busy, False)


def main() -> None:
    root = tk.Tk()
    YouTubeDownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
