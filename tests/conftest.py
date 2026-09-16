from datetime import datetime

import piexif
import pytest
from PIL import Image, ImageDraw


def make_photo(
    path,
    dt: datetime = None,
    *,
    sharp: bool = True,
    camera: bool = True,
    gps: tuple[float, float] = None,
    size: tuple[int, int] = (800, 600),
    variation: int = 0,
    fmt: str = "jpeg",
):
    img = Image.new("RGB", size, (30, 30, 30))
    draw = ImageDraw.Draw(img)
    if sharp:
        for i in range(0, size[0], 15):
            draw.line([(i, 0), (i, size[1])], fill=(i % 255, (i * 3 + variation * 7) % 255, 10), width=2)
        for i in range(0, size[1], 15):
            draw.line([(0, i), (size[0], i)], fill=(10, i % 255, (i * 2 + variation * 5) % 255), width=2)
    else:
        draw.rectangle([0, 0, size[0], size[1]], fill=(80 + variation, 80, 80))

    exif_bytes = None
    if camera or gps or dt:
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}}
        if camera:
            exif_dict["0th"][piexif.ImageIFD.Make] = b"TestCam"
            exif_dict["0th"][piexif.ImageIFD.Model] = b"TestModel X"
        if dt:
            exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = dt.strftime("%Y:%m:%d %H:%M:%S").encode()
        if gps:
            lat, lon = gps

            def to_dms(deg):
                d = int(deg)
                m_float = (deg - d) * 60
                m = int(m_float)
                s = (m_float - m) * 60
                return ((d, 1), (m, 1), (int(s * 100), 100))

            exif_dict["GPS"][piexif.GPSIFD.GPSLatitude] = to_dms(abs(lat))
            exif_dict["GPS"][piexif.GPSIFD.GPSLatitudeRef] = b"N" if lat >= 0 else b"S"
            exif_dict["GPS"][piexif.GPSIFD.GPSLongitude] = to_dms(abs(lon))
            exif_dict["GPS"][piexif.GPSIFD.GPSLongitudeRef] = b"E" if lon >= 0 else b"W"
        exif_bytes = piexif.dump(exif_dict)

    if fmt == "png":
        img.save(path, "png")
    elif exif_bytes:
        img.save(path, "jpeg", exif=exif_bytes)
    else:
        img.save(path, "jpeg")

    return path


@pytest.fixture
def photo_factory(tmp_path):
    counter = {"n": 0}

    def _make(**kwargs):
        counter["n"] += 1
        ext = "png" if kwargs.get("fmt") == "png" else "jpg"
        default_path = tmp_path / f"photo_{counter['n']}.{ext}"
        path = kwargs.pop("path", default_path)
        return make_photo(path, **kwargs)

    return _make
