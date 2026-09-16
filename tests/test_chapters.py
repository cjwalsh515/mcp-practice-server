from datetime import datetime

from photo_pipeline.chapters import label_for_cluster, load_chapters, slugify
from photo_pipeline.clustering import Cluster
from photo_pipeline.exif_utils import PhotoMetadata


def test_load_and_assign_chapter(tmp_path):
    config = tmp_path / "chapters.yaml"
    config.write_text(
        """
chapters:
  - name: "How We Met"
    start: "2007-01-01"
    end: "2009-12-31"
  - name: "The Wedding"
    start: "2015-01-01"
    end: "2015-12-31"
"""
    )
    chapters = load_chapters(config)

    cluster = Cluster(cluster_id="c1", photos=[PhotoMetadata(path="x", datetime=datetime(2015, 6, 1))])
    assert label_for_cluster(cluster, chapters) == slugify("The Wedding")

    cluster_outside = Cluster(cluster_id="c2", photos=[PhotoMetadata(path="y", datetime=datetime(2020, 1, 1))])
    assert label_for_cluster(cluster_outside, chapters) == "2020-01"


def test_no_chapters_falls_back_to_year_month():
    cluster = Cluster(cluster_id="c1", photos=[PhotoMetadata(path="x", datetime=datetime(2018, 3, 15))])
    assert label_for_cluster(cluster, None) == "2018-03"


def test_undated_cluster_falls_back_to_cluster_id():
    cluster = Cluster(cluster_id="undated", photos=[PhotoMetadata(path="x", datetime=None)])
    assert label_for_cluster(cluster, None) == "undated"
