#!/usr/bin/env python3
"""Deterministically sanitize reviewed fixed-10 screenshots and videos.

The source root is supplied at runtime. Raw paths are never written to receipts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, __version__ as pillow_version

ROOT = Path(__file__).resolve().parents[1]
REVIEWS_PATH = ROOT / "scripts/fixed10_media_reviews.json"
VIDEO_ROWS = {4: "reduced-motion-scroll.mp4", 7: "reduced-motion-scroll.mp4", 10: "reduced-scroll.mp4"}
FFMPEG_ARGS = [
    "-v", "error", "-y", "-fflags", "+bitexact", "-i", "<source>",
    "-map", "0:v:0", "-an", "-sn", "-dn", "-map_metadata", "-1",
    "-map_chapters", "-1", "-vf",
    "scale='min(780,iw)':-2:flags=lanczos,format=yuv420p",
    "-c:v", "libx264", "-preset", "slow", "-crf", "32", "-maxrate",
    "500k", "-bufsize", "1000k", "-threads", "1", "-flags:v",
    "+bitexact", "-x264-params",
    "threads=1:lookahead_threads=1:sliced_threads=0:force-cfr=1",
    "-bsf:v", "filter_units=remove_types=6", "-fflags", "+bitexact",
    "-movflags", "+faststart", "<output>",
]
ALLOWED_FORMAT_TAGS = {"major_brand", "minor_version", "compatible_brands"}
ALLOWED_STREAM_TAGS = {"language", "handler_name", "encoder"}


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_private(root: Path) -> Path:
    root = root.resolve()
    candidate = root / "private"
    private = candidate if candidate.is_dir() else root
    for required in ("cohort.jsonl", "cohort-events.jsonl", "cohort-summary.json"):
        if not (private / required).is_file():
            raise SystemExit(f"source root is missing required private input: {required}")
    return private


def unique_glob(private: Path, pattern: str) -> Path:
    matches = sorted({match.resolve() for match in private.glob(pattern)})
    if len(matches) != 1:
        raise SystemExit(f"expected one source for allowlisted selector, found {len(matches)}")
    path = matches[0]
    path.relative_to(private.resolve())
    return path


def png_dimensions(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def encode_png(source: Path, output: Path) -> None:
    with Image.open(source) as image:
        rgb = image.convert("RGB")
        rgb.save(output, format="PNG", compress_level=9, optimize=True)


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=True)


def tool_version(tool: str) -> str:
    return run([tool, "-version"]).stdout.splitlines()[0].strip()


def encode_video(source: Path, output: Path) -> None:
    args = [str(source) if value == "<source>" else str(output) if value == "<output>" else value for value in FFMPEG_ARGS]
    run(["ffmpeg", *args])


def probe_video(path: Path) -> dict:
    result = run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-show_chapters", "-of", "json", str(path),
    ])
    return json.loads(result.stdout)


def validate_video(path: Path, source_bytes: int) -> dict:
    probe = probe_video(path)
    streams = probe.get("streams", [])
    chapters = probe.get("chapters", [])
    if len(streams) != 1 or streams[0].get("codec_type") != "video":
        raise SystemExit("sanitized video must contain exactly one video stream")
    stream = streams[0]
    if stream.get("codec_name") != "h264" or stream.get("pix_fmt") != "yuv420p":
        raise SystemExit("sanitized video codec or pixel format is invalid")
    if int(stream.get("width", 0)) <= 0 or int(stream.get("height", 0)) <= 0:
        raise SystemExit("sanitized video dimensions are invalid")
    if int(stream.get("width", 0)) > 780 or int(stream.get("height", 0)) > 780:
        raise SystemExit("sanitized video dimensions exceed the bound")
    frame_count = int(stream.get("nb_frames", 0))
    duration = float(probe.get("format", {}).get("duration", 0))
    if frame_count <= 0 or duration <= 0 or chapters:
        raise SystemExit("sanitized video frame count, duration, or chapters are invalid")
    format_tags = set(probe.get("format", {}).get("tags", {}))
    stream_tags = set(stream.get("tags", {}))
    if format_tags - ALLOWED_FORMAT_TAGS or stream_tags - ALLOWED_STREAM_TAGS:
        raise SystemExit("sanitized video has disallowed metadata tags")
    output_bytes = path.stat().st_size
    if output_bytes >= source_bytes:
        raise SystemExit("sanitized video is not smaller than its source")
    run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"])
    return {
        "codec": "h264",
        "decodeVerified": True,
        "durationMs": round(duration * 1000),
        "frameCount": frame_count,
        "height": int(stream["height"]),
        "metadataVerified": True,
        "pixelFormat": "yuv420p",
        "playbackVerified": True,
        "streamCount": 1,
        "width": int(stream["width"]),
    }


def passed(review: dict, video: bool = False) -> bool:
    if review.get("ocrReview") != "passed" or review.get("visualReview") != "passed":
        return False
    return not video or set(review.get("frameReviews", {}).values()) == {"passed"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--output-root", type=Path, default=ROOT / "journey-pilot")
    args = parser.parse_args()

    private = source_private(args.source_root)
    output_root = args.output_root.resolve()
    media_dir = output_root / "media"
    data_dir = output_root / "data"
    reviews = json.loads(REVIEWS_PATH.read_text(encoding="utf-8"))
    if reviews.get("schemaVersion") != 1:
        raise SystemExit("unsupported review schema")

    work = Path(tempfile.mkdtemp(prefix="fixed10-media-"))
    receipts: list[dict] = []
    try:
        staged_media = work / "media"
        staged_media.mkdir()
        for ordinal in range(1, 11):
            source = unique_glob(private, f"{ordinal:04d}-*/*/evidence/flow/step-01-baseline-load.png")
            review = reviews["screenshots"][str(ordinal)]
            source_sha = sha256_file(source)
            if source_sha != review.get("sourceSha256"):
                raise SystemExit(f"row {ordinal}: screenshot source hash does not match reviewed input")
            if not passed(review):
                raise SystemExit(f"row {ordinal}: screenshot has not passed OCR and visual review")
            first = work / f"screenshot-{ordinal}-a.png"
            second = work / f"screenshot-{ordinal}-b.png"
            encode_png(source, first)
            encode_png(source, second)
            if first.read_bytes() != second.read_bytes():
                raise SystemExit(f"row {ordinal}: PNG transform is not deterministic")
            output_sha = sha256_file(first)
            filename = f"{output_sha}.png"
            shutil.copyfile(first, staged_media / filename)
            width, height = png_dimensions(first)
            receipts.append({
                "height": height,
                "kind": "screenshot",
                "mediaId": f"row-{ordinal:02d}-baseline-screenshot",
                "ocrReview": "passed",
                "ordinal": ordinal,
                "outputBytes": first.stat().st_size,
                "outputFile": f"media/{filename}",
                "outputSha256": output_sha,
                "sourceBytes": source.stat().st_size,
                "sourceSha256": source_sha,
                "transformId": "pillow-rgb-png-level9-v1",
                "visualReview": "passed",
                "width": width,
            })

        for ordinal, basename in VIDEO_ROWS.items():
            source = unique_glob(private, f"{ordinal:04d}-*/*/evidence/**/{basename}")
            review = reviews["videos"][str(ordinal)]
            source_sha = sha256_file(source)
            if source_sha != review.get("sourceSha256"):
                raise SystemExit(f"row {ordinal}: video source hash does not match reviewed input")
            if not passed(review, video=True):
                raise SystemExit(f"row {ordinal}: video frames have not passed OCR and visual review")
            first = work / f"video-{ordinal}-a.mp4"
            second = work / f"video-{ordinal}-b.mp4"
            encode_video(source, first)
            encode_video(source, second)
            if first.read_bytes() != second.read_bytes():
                raise SystemExit(f"row {ordinal}: video transform is not deterministic")
            facts = validate_video(first, source.stat().st_size)
            output_sha = sha256_file(first)
            filename = f"{output_sha}.mp4"
            shutil.copyfile(first, staged_media / filename)
            receipts.append({
                **facts,
                "frameReview": {"first": "passed", "last": "passed", "middle": "passed"},
                "kind": "video",
                "mediaId": f"row-{ordinal:02d}-reduced-motion-video",
                "ocrReview": "passed",
                "ordinal": ordinal,
                "outputBytes": first.stat().st_size,
                "outputFile": f"media/{filename}",
                "outputSha256": output_sha,
                "sourceBytes": source.stat().st_size,
                "sourceSha256": source_sha,
                "transformId": "ffmpeg-fixed10-h264-v1",
                "visualReview": "passed",
            })

        manifest = {
            "omissions": {
                "privateDashboardScreenshots": 2,
                "reason": "Raw files not selected and privacy-reviewed remain private; no raw browser artifact is copied.",
                "rawSiteScreenshotsNotSelected": 51,
                "videosOmitted": 0,
            },
            "receipts": sorted(receipts, key=lambda item: (item["ordinal"], item["kind"])),
            "reviewMethod": reviews["reviewMethod"],
            "reviewScope": reviews["reviewScope"],
            "schemaVersion": 1,
            "transforms": {
                "screenshot": {
                    "args": ["convert RGB", "PNG", "compress_level=9", "optimize=true", "metadata omitted"],
                    "pillowVersion": pillow_version,
                    "transformId": "pillow-rgb-png-level9-v1",
                },
                "video": {
                    "ffmpegArgs": FFMPEG_ARGS,
                    "ffmpegVersion": tool_version("ffmpeg"),
                    "ffprobeVersion": tool_version("ffprobe"),
                    "transformId": "ffmpeg-fixed10-h264-v1",
                },
            },
        }
        media_bytes = canonical(manifest)
        (work / "media-manifest.json").write_bytes(media_bytes)

        if media_dir.exists():
            shutil.rmtree(media_dir)
        media_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staged_media, media_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(work / "media-manifest.json", data_dir / "media-manifest.json")

        # Verify every final public asset after its move.
        for receipt in receipts:
            final = output_root / receipt["outputFile"]
            if sha256_file(final) != receipt["outputSha256"] or final.stat().st_size != receipt["outputBytes"]:
                raise SystemExit("final media hash or size mismatch")
            if receipt["kind"] == "video":
                validate_video(final, receipt["sourceBytes"])
        print(f"processed {len(receipts)} reviewed media receipts")
        print(f"manifest sha256 {sha256_bytes(media_bytes)}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
