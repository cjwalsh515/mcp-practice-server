from photo_pipeline.exif_utils import read_metadata
from photo_pipeline.screenshot_filter import classify_screenshot


def test_png_no_exif_known_resolution_is_screenshot(tmp_path, photo_factory):
    path = photo_factory(fmt="png", camera=False, dt=None, size=(1170, 2532))
    meta = read_metadata(path)
    verdict = classify_screenshot(meta)
    assert verdict.is_screenshot
    assert "known_screen_resolution" in verdict.reasons
    assert "png_format" in verdict.reasons


def test_filename_pattern_alone_flags_screenshot(tmp_path, photo_factory):
    path = photo_factory(path=tmp_path / "Screenshot_2020-01-01.jpg", camera=False, dt=None)
    meta = read_metadata(path)
    verdict = classify_screenshot(meta)
    assert verdict.is_screenshot


def test_real_camera_photo_is_not_screenshot(photo_factory):
    path = photo_factory(camera=True, size=(4032, 3024))
    meta = read_metadata(path)
    verdict = classify_screenshot(meta)
    assert not verdict.is_screenshot
