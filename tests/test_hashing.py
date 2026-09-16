from PIL import Image, ImageDraw

from photo_pipeline.hashing import compute_phash, group_near_duplicates


def _make_scene(path, offset=0):
    img = Image.new("RGB", (400, 300), (50, 80, 120))
    draw = ImageDraw.Draw(img)
    draw.ellipse([50 + offset, 50, 200 + offset, 200], fill=(200, 50, 50))
    draw.rectangle([220, 100, 350, 250], fill=(50, 200, 50))
    img.save(path, "jpeg")
    return path


def test_near_identical_photos_group_together(tmp_path):
    a = _make_scene(tmp_path / "a.jpg", offset=0)
    b = _make_scene(tmp_path / "b.jpg", offset=2)
    c = _make_scene(tmp_path / "c.jpg", offset=3)
    different = _make_scene(tmp_path / "different.jpg", offset=120)

    hashes = {p: compute_phash(p) for p in [a, b, c, different]}
    groups = group_near_duplicates(hashes, max_distance=6)

    group_sets = [set(g) for g in groups]
    assert {a, b, c} in group_sets
    assert any(g == {different} for g in group_sets)


def test_single_photo_is_its_own_group(tmp_path):
    a = _make_scene(tmp_path / "a.jpg")
    groups = group_near_duplicates({a: compute_phash(a)})
    assert groups == [[a]]
