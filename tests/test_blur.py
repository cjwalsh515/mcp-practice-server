from photo_pipeline.blur import compute_blur_score, is_blurry


def test_sharp_photo_scores_higher_than_blurry(photo_factory):
    sharp_path = photo_factory(sharp=True)
    blurry_path = photo_factory(sharp=False)

    sharp_score = compute_blur_score(sharp_path)
    blurry_score = compute_blur_score(blurry_path)

    assert sharp_score > blurry_score


def test_is_blurry_threshold():
    assert is_blurry(10.0, threshold=60.0)
    assert not is_blurry(100.0, threshold=60.0)
