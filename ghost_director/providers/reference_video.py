"""Verified multimodal reference endpoint; reject unsupported media before spending."""
from __future__ import annotations

from fractions import Fraction
import hashlib
import json
import math
import re

from PIL import Image

from ghost_director.media import process
from ghost_director.providers.http import data_uri

REFERENCE_VIDEO_MODEL = "bytedance/seedance-2.5/reference-to-video"
SCHEMA_URL = "https://fal.ai/api/openapi/queue/openapi.json?endpoint_id=bytedance/seedance-2.5/reference-to-video"
PRICING_URL = "https://fal.ai/models/bytedance/seedance-2.5/reference-to-video"
MAX_INLINE_FILE_BYTES = 20_000_000
MAX_INLINE_TOTAL_BYTES = 24_000_000


async def probe_video(path, *, decode=True):
    raw = json.loads(await process("ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path, timeout=30))
    streams = [stream for stream in raw.get("streams", []) if stream.get("codec_type") == "video"]
    if len(streams) != 1:
        raise ValueError("Reference video must contain exactly one video stream")
    stream = streams[0]
    try:
        duration = float(stream.get("duration") or raw["format"]["duration"])
        fps = float(Fraction(stream["avg_frame_rate"]))
        width, height = int(stream["width"]), int(stream["height"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        raise ValueError("Video metadata is incomplete") from None
    if not all(math.isfinite(value) for value in (duration, fps)) or not 0 < duration <= 35 or not 0 < fps <= 120 or not 0 < width <= 8192 or not 0 < height <= 8192:
        raise ValueError("Video metadata exceeds the bounded decoder limits")
    if "mp4" not in raw.get("format", {}).get("format_name", "").split(","):
        raise ValueError("Reference video must be an MP4 container")
    if decode:
        await process("ffmpeg", "-v", "error", "-xerror", "-i", path, "-f", "null", "-", timeout=60)
    return {"width": width, "height": height, "duration": duration, "fps": fps, "codec": stream.get("codec_name")}


def estimate_reference_cost(width, height, output_seconds, input_seconds, *, resolution):
    """Current provider's token formula. An estimate, never a reported invoice."""
    tokens = width*height*(input_seconds+output_seconds)*24/1024
    per_thousand = .0234 if resolution == "1080p" else .0214
    return tokens/1000*per_thousand*(.6 if input_seconds else 1)


async def compile_reference_video(store, prompt, image_ids, video_ids, *, duration, resolution):
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 12000:
        raise ValueError("Reference video prompt must contain 1–12000 characters")
    if not isinstance(duration, int) or isinstance(duration, bool) or not 4 <= duration <= 30 or resolution not in {"480p", "720p", "1080p"}:
        raise ValueError("Reference video requires 4–30 integer seconds and a supported resolution")
    if not 1 <= len(image_ids) <= 30 or not 1 <= len(video_ids) <= 10 or len(image_ids)+len(video_ids) > 50:
        raise ValueError("Reference video requires 1–30 images and 1–10 motion videos, at most 50 files")
    if len(set([*image_ids, *video_ids])) != len(image_ids)+len(video_ids):
        raise ValueError("Reference files must be unique")
    for kind, index in re.findall(r"@(Image|Video)(\d+)", prompt):
        if not 1 <= int(index) <= (len(image_ids) if kind == "Image" else len(video_ids)):
            raise ValueError("Prompt references a media tag absent from the submission")
    if re.search(r"@Audio\d+", prompt):
        raise ValueError("Audio reference tags are unsupported by this submission")
    sources, total_bytes, input_seconds = [], 0, 0.0
    for identifier in [*image_ids, *video_ids]:
        artifact = store.artifact(identifier)
        path = store.artifact_path(identifier)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
            raise ValueError("Reference artifact no longer matches its recorded hash")
        total_bytes += len(raw)
        if len(raw) > MAX_INLINE_FILE_BYTES or total_bytes > MAX_INLINE_TOTAL_BYTES:
            raise ValueError("Reference uploads exceed the local inline payload limit (20 MB/file, 24 MB total)")
        if identifier in image_ids:
            if artifact["kind"] != "image" or artifact["mime_type"] not in {"image/png", "image/jpeg", "image/webp"}:
                raise ValueError("Appearance references must be registered PNG, JPEG, or WebP images")
            with Image.open(path) as image:
                width, height = image.size
                if not 300 <= min(width, height) <= max(width, height) <= 6000 or not .4 <= width/height <= 2.5:
                    raise ValueError("Reference image dimensions are outside the supported bounds")
                image.verify()
        else:
            if artifact["kind"] != "video" or artifact["mime_type"] != "video/mp4":
                raise ValueError("Motion references must be registered MP4 videos")
            metadata = await probe_video(path)
            if not 1.8 <= metadata["duration"] <= 30.2 or not 24 <= metadata["fps"] <= 60 or not 300 <= min(metadata["width"], metadata["height"]) <= max(metadata["width"], metadata["height"]) <= 6000 or not .4 <= metadata["width"]/metadata["height"] <= 2.5:
                raise ValueError("Motion video violates duration, FPS, dimensions, or aspect bounds")
            input_seconds += metadata["duration"]
        sources.append({"id": identifier, "sha256": artifact["sha256"], "bytes": len(raw)})
    if input_seconds > 30.2:
        raise ValueError("Combined motion-reference duration exceeds 30.2 seconds")
    payload = {"prompt": prompt, "task": "reference", "duration": str(duration), "resolution": resolution,
               "aspect_ratio": "16:9", "generate_audio": False, "codec": "H264",
               "image_urls": [data_uri(store.artifact_path(item), store.artifact(item)["mime_type"]) for item in image_ids],
               "video_urls": [data_uri(store.artifact_path(item), "video/mp4") for item in video_ids]}
    # These are conservative provider-aligned output dimensions. The final cost
    # is recomputed from the actual output dimensions and duration after decoding.
    width, height = {"480p": (864, 496), "720p": (1280, 720), "1080p": (1920, 1080)}[resolution]
    estimated = estimate_reference_cost(width, height, duration, input_seconds, resolution=resolution)
    pricing = {"estimated_usd": estimated, "reserve_usd": estimated*1.2, "input_video_seconds": input_seconds,
               "output_seconds": duration, "resolution": resolution, "output_dimensions": [width, height],
               "source": PRICING_URL, "checked_at": "2026-11-03", "schema": SCHEMA_URL, "input_artifacts": sources}
    return payload, pricing
