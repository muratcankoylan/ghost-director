"""Independent pinhole oracle for the shared horizontal-fit camera contract."""
import math


def _sub(a, b): return tuple(x - y for x, y in zip(a, b))
def _dot(a, b): return sum(x * y for x, y in zip(a, b))
def _cross(a, b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def _unit(a):
    length = math.sqrt(_dot(a, a))
    if length < 1e-10: raise ValueError("Camera direction/up must define a basis")
    return tuple(v / length for v in a)


def project(camera, point, width, height):
    """Return top-left pixel coordinates for Blender's horizontal sensor fit."""
    forward = _unit(_sub(camera["target"], camera["position"]))
    right = _unit(_cross(forward, (0, 0, 1)))
    up = _cross(right, forward)
    delta = _sub(point, camera["position"])
    depth = _dot(delta, forward)
    if depth <= 0: raise ValueError("Point is on or behind camera plane")
    focal_pixels = width * camera["focal_length_mm"] / camera["sensor_width_mm"]
    return (width / 2 + focal_pixels * _dot(delta, right) / depth,
            height / 2 - focal_pixels * _dot(delta, up) / depth)


def to_browser(point): return (point[0], point[2], -point[1])
def to_canonical(point): return (point[0], -point[2], point[1])
