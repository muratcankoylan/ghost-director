"""Register a completed SAM person-query result to its original image.

The provider query must target people: SAM records carry object IDs, not class
labels. We select the latest mask per object, then the largest foreground area.
Parsing and compressed-mask decoding belong to Meta's official parser; this
module owns resource limits, source registration and the binary PNG contract.
"""

from __future__ import annotations

from io import BytesIO
import re

from meta_sam_parser import (
    CompletedOutcome,
    SegmentationMaskRecord,
    decode_mask_to_raster,
    image_segmentation_format,
)
from PIL import Image


_MAX_PIXELS = 16_777_216
_MAX_TOTAL_MASK_PIXELS = 33_554_432
_MAX_TEXT_CHARS = 8_000_000
_MAX_MASK_RECORDS = 128
_TOKEN = re.compile(r"<\|(box|mask);([^|]*)\|>")
_DECIMAL = re.compile(r"[0-9]{1,8}\Z")
_SAM_WHITESPACE = "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"


def _positive_dimension(value: str) -> int:
    if not _DECIMAL.fullmatch(value) or not 0 < int(value) <= _MAX_PIXELS:
        raise ValueError("Invalid SAM mask dimensions")
    return int(value)


def _check_resource_limits(text: str, source_size: tuple[int, int]) -> None:
    # The official decoder validates the encoding but allocates from its stated
    # raster size. Bound every record before passing untrusted text to it.
    if not isinstance(text, str) or len(text) > _MAX_TEXT_CHARS:
        raise ValueError("SAM response exceeds text limit")
    if (len(source_size) != 2 or any(type(v) is not int or v <= 0 for v in source_size)
            or source_size[0] * source_size[1] > _MAX_PIXELS):
        raise ValueError("Source dimensions exceed image limit")
    count = total = 0
    for token in _TOKEN.finditer(text):
        fields = [tuple(value.strip(_SAM_WHITESPACE) for value in part.partition("=")) for part in token.group(2).split(";")]
        if token.group(1) == "box":
            # The parser exposes registered bounds, but not original w/h. Check
            # the declared source here so a resized reference cannot slip in.
            for key, _, value in fields:
                if key in {"w", "h"}:
                    dimension = _positive_dimension(value)
                    if dimension != source_size[0 if key == "w" else 1]:
                        raise ValueError("SAM source dimensions do not match reference image")
        else:
            count += 1
            if count > _MAX_MASK_RECORDS:
                raise ValueError("SAM response exceeds mask count limit")
            for key, _, value in fields:
                if key == "data":
                    dimensions = value.split(",", 2)
                    if len(dimensions) != 3:
                        raise ValueError("Invalid SAM mask data")
                    height, width = map(_positive_dimension, dimensions[:2])
                    area = width * height
                    total += area
                    if area > _MAX_PIXELS or total > _MAX_TOTAL_MASK_PIXELS:
                        raise ValueError("SAM response exceeds mask pixel limit")


def decode_person_mask(text: str, source_size: tuple[int, int]) -> bytes:
    """Return an 8-bit, full-source-size PNG with foreground 255/background 0.

    ``text`` is concatenated ``response.output_text.delta`` text from a completed
    image-segmentation response, not the SSE envelope. A missing, malformed,
    empty, oversized or differently registered mask raises ``ValueError``.
    """
    _check_resource_limits(text, source_size)
    parser = image_segmentation_format().create_parser()
    parser.push(text)
    result = parser.finish(CompletedOutcome()).result
    errors = [item.code for item in result.diagnostics if item.severity == "error"]
    if errors:
        # Diagnostics contain raw provider text; expose only finite error codes.
        raise ValueError("Invalid SAM segmentation: " + ", ".join(sorted(set(errors))))

    latest = {}
    for record in result.records:
        if isinstance(record, SegmentationMaskRecord):
            latest[record.identity] = record
    if not latest:
        raise ValueError("SAM returned no person masks")

    selected = None
    largest_area = 0
    for record in latest.values():
        bounds = record.bounds
        coordinates = (bounds.left, bounds.top, bounds.right, bounds.bottom)
        if any(not float(value).is_integer() for value in coordinates):
            raise ValueError("SAM bounds are not integer image coordinates")
        left, top, right, bottom = map(int, coordinates)
        if not (0 <= left < right <= source_size[0] and 0 <= top < bottom <= source_size[1]):
            raise ValueError("SAM mask bounds exceed source image")

        raster = decode_mask_to_raster(record.mask)
        crop = Image.frombytes("L", (record.mask.width, record.mask.height), raster)
        crop = crop.point([0] + [255] * 255)
        # Meta boxes use inclusive x2/y2. The official parser already converts
        # to half-open bounds: adding one again shifts/scales the subject.
        crop = crop.resize((right - left, bottom - top), Image.Resampling.NEAREST)
        area = crop.histogram()[255]
        if area > largest_area:
            largest_area = area
            selected = (crop, left, top)

    if selected is None:
        raise ValueError("SAM returned only empty person masks")
    crop, left, top = selected
    image = Image.new("L", source_size, 0)
    image.paste(crop, (left, top))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
