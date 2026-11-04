from io import BytesIO
import unittest
from unittest.mock import patch

from PIL import Image

from ghost_director.providers.segmentation import decode_person_mask


# Fixed one-bit codec fixtures, decoded by the real official parser in every
# test. Each is 2x2 row-major: diagonal, full, one foreground pixel, empty.
DIAGONAL = "!!!!!&T:u9`"
FULL = "!!!!!&t.SKi"
SINGLE = "!!!!!&O.lVP"
EMPTY = "!!!!!&!!!!!"


def record(object_id=0, box=(1, 2, 4, 5), payload=DIAGONAL, source=(10, 10)):
    left, top, right, bottom = box
    return (f"{object_id}<|box;x1={left};y1={top};x2={right};y2={bottom};"
            f"w={source[0]};h={source[1]}|><|mask;x=0;y=0;data=2,2,{payload}|>")


def decode(text, source=(10, 10)):
    image = Image.open(BytesIO(decode_person_mask(text, source)))
    image.load()
    return image


class SegmentationTests(unittest.TestCase):
    def test_full_resolution_registration_uses_mask_raster_dimensions(self):
        image = decode("<0f>" + record())
        self.assertEqual(image.size, (10, 10))
        self.assertEqual(image.mode, "L")
        self.assertEqual(set(image.tobytes()), {0, 255})
        self.assertEqual(image.getbbox(), (1, 2, 5, 6))
        self.assertEqual(image.histogram()[255], 8)
        self.assertEqual([image.getpixel(xy) for xy in [(1, 2), (4, 2), (1, 5), (4, 5)]], [255, 0, 0, 255])

    def test_inclusive_box_can_reach_last_source_pixel(self):
        image = decode("<0f>" + record(box=(8, 8, 9, 9), payload=FULL))
        self.assertEqual(image.getbbox(), (8, 8, 10, 10))
        self.assertEqual(image.getpixel((9, 9)), 255)
        self.assertEqual(image.histogram()[255], 4)

    def test_largest_foreground_selected_instead_of_first_or_largest_box(self):
        first = record(0, (0, 0, 3, 3), SINGLE)  # 4 foreground pixels in 16-pixel box.
        second = record(1, (7, 6, 9, 8), FULL)  # 9 foreground pixels.
        image = decode("<0f>" + first + "," + second)
        self.assertEqual(image.getbbox(), (7, 6, 10, 9))
        self.assertEqual(image.histogram()[255], 9)

    def test_latest_revision_replaces_same_object(self):
        image = decode("<0f>" + record(0, (0, 0, 5, 5), FULL) + "\n<0f>" + record(0, (7, 7, 8, 8), FULL))
        self.assertEqual(image.getbbox(), (7, 7, 9, 9))
        self.assertEqual(image.histogram()[255], 4)

    def test_rejects_different_source_registration(self):
        with self.assertRaisesRegex(ValueError, "source dimensions"):
            decode("<0f>" + record(source=(20, 20)))

    def test_rejects_malformed_record_even_alongside_valid_mask(self):
        with self.assertRaisesRegex(ValueError, "Invalid SAM segmentation"):
            decode("<0f>" + record() + ",1<|box;broken|>")

    def test_rejects_invalid_compressed_payload(self):
        with self.assertRaisesRegex(ValueError, "invalid_mask_payload"):
            decode("<0f>" + record(payload="!!!!!bad"))

    def test_rejects_empty_or_missing_person(self):
        for text in ("", "No people were detected.", "<0f>" + record(payload=EMPTY)):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "no person masks|empty person masks"):
                decode(text)

    def test_large_raster_rejected_before_official_parser_allocates(self):
        for field in ("data", "\ufeffdata\ufeff", " data "):
            text = ("<0f>" + record()).replace("data=2,2,", f"{field}=99999999,99999999,")
            with self.subTest(field=field), patch("ghost_director.providers.segmentation.image_segmentation_format") as parser:
                with self.assertRaisesRegex(ValueError, "dimensions|pixel limit"):
                    decode(text)
                parser.assert_not_called()

    def test_invalid_source_size_and_video_frame_fail(self):
        for source in ((0, 10), (True, 10), (10000, 10000)):
            with self.subTest(source=source), self.assertRaises(ValueError):
                decode_person_mask("", source)
        with self.assertRaisesRegex(ValueError, "Invalid SAM segmentation"):
            decode("<1f>" + record())


if __name__ == "__main__":
    unittest.main()
