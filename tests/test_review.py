import json
import threading
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image

from photo_pipeline.review import (
    DecisionsStore,
    build_review_items,
    build_server,
)


def _make_cluster(output_dir, chapter, cluster_id, n=2):
    cluster_dir = output_dir / chapter / cluster_id
    cluster_dir.mkdir(parents=True)
    for i in range(n):
        Image.new("RGB", (50, 50), (i * 20, 0, 0)).save(cluster_dir / f"photo_{i}.jpg")
    return cluster_dir


def test_build_review_items_prefers_curated_layers(tmp_path):
    cluster_dir = _make_cluster(tmp_path, "2018-07", "c1", n=2)
    best_dir = cluster_dir / "best"
    best_dir.mkdir()
    Image.new("RGB", (10, 10)).save(best_dir / "photo_0.jpg")

    items = build_review_items(tmp_path)
    assert len(items) == 1
    assert items[0]["source"] == "best"
    assert items[0]["rel_path"] == "2018-07/c1/best/photo_0.jpg"


def test_decisions_store_roundtrip(tmp_path):
    path = tmp_path / "review_decisions.json"
    store = DecisionsStore(path)
    store.set_decision("a.jpg", "keep")
    store.set_decision("b.jpg", "skip")
    store.set_current_index(1)

    reloaded = DecisionsStore(path)
    assert reloaded.decisions["a.jpg"]["decision"] == "keep"
    assert reloaded.decisions["b.jpg"]["decision"] == "skip"
    assert reloaded.current_index == 1


def test_decisions_store_clear_decision(tmp_path):
    store = DecisionsStore(tmp_path / "d.json")
    store.set_decision("a.jpg", "keep")
    store.set_decision("a.jpg", None)
    assert "a.jpg" not in store.decisions


def _get(url):
    with urllib.request.urlopen(url) as resp:
        return resp.status, resp.read()


def _post(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read())


def test_server_endpoints_end_to_end(tmp_path):
    _make_cluster(tmp_path, "2018-07", "c1", n=2)

    server, items, store = build_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        status, body = _get(base + "/")
        assert status == 200
        assert b"<html" in body.lower()

        status, body = _get(base + "/api/items")
        data = json.loads(body)
        assert len(data["items"]) == 2
        rel_path = data["items"][0]["rel_path"]

        status, body = _get(base + "/api/image?path=" + urllib.parse.quote(rel_path))
        assert status == 200

        status, resp = _post(base + "/api/decide", {"rel_path": rel_path, "decision": "keep"})
        assert status == 200 and resp["ok"]

        status, resp = _post(base + "/api/index", {"index": 1})
        assert status == 200 and resp["ok"]

        # path traversal must be rejected
        try:
            _get(base + "/api/image?path=" + urllib.parse.quote("../../etc/passwd"))
            assert False, "expected HTTPError"
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        server.server_close()

    reloaded = DecisionsStore(tmp_path / "review_decisions.json")
    assert reloaded.decisions[rel_path]["decision"] == "keep"
    assert reloaded.current_index == 1
