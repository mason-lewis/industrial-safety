# /// script
# requires-python = ">=3.12"
# dependencies = ["yt-dlp[default]", "imageio-ffmpeg"]
# ///
"""Download individual YouTube videos as MP4 test footage."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlparse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="+", help="YouTube video URLs (quote each URL)")
    parser.add_argument(
        "-o", "--output-dir", type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "input",
        help="Destination directory (default: project data/input)",
    )
    parser.add_argument("--max-height", type=int, default=1080, help="Maximum resolution height (default: 1080)")
    args = parser.parse_args()
    if args.max_height < 1:
        parser.error("--max-height must be positive")
    for url in args.urls:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not (
            host in {"youtu.be", "youtube.com"} or host.endswith(".youtube.com")
        ):
            parser.error(f"Expected a YouTube video URL: {url}")

    import imageio_ffmpeg

    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    height = f"[height<={args.max_height}]"
    command = [
        sys.executable, "-m", "yt_dlp",
        "--ignore-config", "--no-playlist", "--no-overwrites",
        "--ffmpeg-location", imageio_ffmpeg.get_ffmpeg_exe(),
        "--format", f"bv[ext=mp4]{height}+ba[ext=m4a]/b[ext=mp4]{height}/bv[ext=mp4]{height}",
        "--format-sort", "vcodec:h264",
        "--merge-output-format", "mp4", "--remux-video", "mp4",
        "--paths", str(output),
        "--output", "%(title).120B [%(id)s].%(ext)s",
        "--print", "after_move:filepath", "--no-simulate",
    ]
    # Node is already used by the frontend; yt-dlp requires explicit opt-in.
    if node := shutil.which("node"):
        command.extend(["--js-runtimes", f"node:{node}"])
    elif not shutil.which("deno"):
        parser.error("YouTube downloads require Node.js 22+ or Deno; install a runtime and retry.")
    print(f"Saving MP4 footage to {output}", flush=True)
    return subprocess.call([*command, "--", *args.urls])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
