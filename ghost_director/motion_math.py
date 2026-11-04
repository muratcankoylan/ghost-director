"""Pure interpolation shared by the validated harness and Blender's Python runtime."""
from __future__ import annotations
from bisect import bisect_right


def sample_keyframes(keyframes: list[dict], seconds: float) -> dict:
    """Hold endpoints; interpolate numeric scalar/vector channels linearly."""
    if not keyframes:
        raise ValueError("Cannot sample an empty motion track")
    index = bisect_right([key["time_seconds"] for key in keyframes], seconds)
    if index == 0:
        return {**keyframes[0], "time_seconds": seconds}
    if index >= len(keyframes):
        return {**keyframes[-1], "time_seconds": seconds}
    first, last = keyframes[index - 1], keyframes[index]
    alpha = (seconds - first["time_seconds"]) / (last["time_seconds"] - first["time_seconds"])
    result = {"time_seconds": seconds}
    for field, start in first.items():
        if field == "time_seconds":
            continue
        end = last[field]
        result[field] = [a + (b-a)*alpha for a,b in zip(start,end)] if isinstance(start,(list,tuple)) else start+(end-start)*alpha
    return result


def sample_motion(motion: dict, seconds: float) -> dict:
    return {
        "time_seconds": seconds,
        "camera": sample_keyframes(motion["camera_keyframes"], seconds),
        "entities": [{"entity_id": track["entity_id"], **sample_keyframes(track["keyframes"], seconds)} for track in motion["tracks"]],
    }
