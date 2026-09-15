# YouTube Downloader GUI

A simple desktop app to download YouTube videos and audio. Paste a URL, load available qualities, pick one, and save it to a folder of your choice.

Built with Python, Tkinter, and [yt-dlp](https://github.com/yt-dlp/yt-dlp). Includes a cross-platform `build.py` script to compile into a standalone executable for Windows, macOS, or Linux.

---

## Features

- Load available video and audio qualities for a YouTube URL
- Clear quality labels such as `4K (2160p)`, `1080p (Full HD)`, `720p (HD)`
- Choose **H.264 (MP4)** or **VP9 (WebM/MKV)** when both are available
- Automatic **English subtitle downloading and embedding** into a single `.mkv` container (no extra `.vtt`/`.srt` clutter on disk)
- Smart rate-limit protection (`subtitleslangs: ["en.*", "en"]`) to prevent `HTTP Error 429: Too Many Requests`
- Progress bar with readable speed and ETA
- Custom save folder (defaults to your Downloads folder)
- Dark, modern interface

---

## Prerequisites & Installation

### 1. Install FFmpeg & System Packages

FFmpeg is required to merge high-definition video and audio streams and embed subtitles.

* **Windows**:
  Install via `winget` in PowerShell:
  ```powershell
  winget install FFmpeg
  ```
  Or download from [ffmpeg.org](https://ffmpeg.org/download.html) and add `ffmpeg.exe` to your system `PATH`.

* **Linux (Debian / Ubuntu)**:
  Install FFmpeg and Python Tkinter:
  ```bash
  sudo apt update
  sudo apt install ffmpeg python3-tk python3-pip
  ```

* **Linux (Fedora / RHEL)**:
  ```bash
  sudo dnf install ffmpeg python3-tkinter python3-pip
  ```

* **macOS**:
  ```bash
  brew install ffmpeg
  ```

---

### 2. Install Python Dependencies

Clone or download this repository, then install requirements:

**On Windows (PowerShell):**
```powershell
python -m pip install -r requirements.txt
```

**On Linux & macOS:**
```bash
python3 -m pip install -r requirements.txt
```

*(Optional: Virtual environment setup)*
```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

# Windows (PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

---

## Running & Building

### Run directly from source

* **Windows**:
  ```powershell
  python main.py
  ```

* **Linux & macOS**:
  ```bash
  python3 main.py
  ```

---

### Build a Standalone Executable (`build.py`)

You can build a standalone executable locally for your operating system using `build.py`:

* **Windows**:
  ```powershell
  python build.py
  ```
  *Generates:* `dist/YT-Downloader.exe`

* **Linux**:
  ```bash
  python3 build.py
  ```
  *Generates:* `dist/YT-Downloader`

* **macOS**:
  ```bash
  python3 build.py
  ```
  *Generates:* `dist/YT-Downloader.app`

The build output will be placed in the `dist/` directory (which is gitignored).

---

## How to Use

1. Paste a YouTube video URL into the **YouTube URL** field.
2. Click **Load qualities**.
3. Select a row from the quality list.
   - Prefer **MP4 / H.264** for maximum player compatibility.
   - Prefer **MKV / VP9** when you want a smaller file and embedded English subtitles in a single file.
4. Optionally change the **Save to** folder with **Browse…**.
5. Click **Download selected quality**.
6. Wait for the progress bar to finish. Files are saved to the chosen folder.

---

## Quality & Container Guide

### Resolution Guide

| Label | Vertical pixels |
| --- | --- |
| 8K (4320p) | 4320 |
| 4K (2160p) | 2160 |
| 1440p (QHD) | 1440 |
| 1080p (Full HD) | 1080 |
| 720p (HD) | 720 |
| 480p / 360p / … | matching height |

### Containers

| Choice | Typical result | Notes |
| --- | --- | --- |
| H.264 → **MP4** | Native YouTube H.264 + AAC | Best compatibility across older devices |
| VP9/AV1 → **MKV** | Remuxed with Opus audio | Embeds English subtitles directly inside the single `.mkv` file |
| **MP4** at 4K/1440p | Re-encoded to H.264 | Offered when YouTube has no native H.264 at that resolution |

---

## Troubleshooting

| Problem | What to try |
| --- | --- |
| `command not found: python` | Use `python3` (or install Python from python.org on Windows) |
| `Could not load qualities` / SSL errors | Run `pip install -r requirements.txt` again (includes `certifi`) |
| `HTTP Error 429: Too Many Requests` | Fixed automatically in this version by requesting English subtitles (`en.*`, `en`) instead of fetching 100+ languages |
| Download finishes but video has no audio / merge fails | Install FFmpeg (`winget install FFmpeg` on Windows, `apt install ffmpeg` on Linux) |
| App window does not open / `_tkinter` error | Install Tkinter package (`sudo apt install python3-tk` on Ubuntu/Debian) |
| Very old or failing downloads | Update yt-dlp: `pip install -U yt-dlp` |

---

## Testing

Run the test suite:

```bash
python3 -m unittest discover -s tests
```

---

## Project Layout

```
yt-downloader-gui/
├── main.py              # Tkinter GUI Application
├── build.py             # PyInstaller build script for creating executables
├── requirements.txt     # Python dependencies
├── tests/               # Unit test suite
├── .gitignore           # Git ignore rules
└── README.md            # Project documentation
```

---

## License

Use and modify freely for personal projects. Respect YouTube’s terms of service and copyright law when downloading content.
