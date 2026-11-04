import json
import math
from pathlib import Path

import pytest

from ghost_director.blender.camera_math import project, to_browser, to_canonical


FIXTURES = Path(__file__).parent / "fixtures"


def test_asymmetric_coordinate_round_trip_preserves_handedness():
    point = (1.25, -2.75, .47)
    assert to_browser(point) == (1.25, .47, 2.75)
    assert to_canonical(to_browser(point)) == point
    # +X cross canonical +Y becomes browser +X cross -Z = +Y (canonical +Z).
    assert to_browser((0, 0, 1)) == (0, 1, 0)


def test_actual_blender_camera_projection_matches_independent_oracle():
    # Captured Blender measurements travel with the test, independently of the
    # private production archive; this does not pretend to run Blender in CI.
    path = FIXTURES / "blender-5.2.3-camera-proof.json"
    proof = json.loads(path.read_text())
    assert proof["blender_version"] == "5.2.3 LTS"
    worst = 0
    for record in proof["cameras"]:
        for point, expected in zip(record["points_world"], record["pixels_top_left"]):
            actual = project(record["camera"], point, *proof["resolution"])
            worst = max(worst, math.dist(actual, expected))
    assert len(proof["cameras"]) == 3
    assert worst <= .01, f"Blender reprojection mismatch {worst:.5f}px"


def test_invalid_camera_and_behind_camera_points_are_rejected():
    camera = {"position": [0, -2, 1], "target": [0, 0, 1], "focal_length_mm": 50, "sensor_width_mm": 36}
    with pytest.raises(ValueError): project(camera, [0, -3, 1], 1920, 1080)
    with pytest.raises(ValueError): project({**camera, "target": camera["position"]}, [0, 0, 1], 1920, 1080)
