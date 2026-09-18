from __future__ import annotations

from types import SimpleNamespace

from mtgproxy.cache import ImageCache, is_image_url


def test_is_image_url():
    assert is_image_url("https://cards.scryfall.io/png/front/a/b/abc.png?1700000000")
    assert is_image_url("https://api.scryfall.com/cards/znr/90/?format=image&version=png")
    assert not is_image_url("https://api.scryfall.com/cards/collection")


def test_put_get_roundtrip_and_stats(tmp_path):
    c = ImageCache(tmp_path / "cache")
    assert c.get("u") is None
    c.put("u", b"png-bytes")
    assert c.get("u") == b"png-bytes"
    c.put("empty", b"")  # never store empty bodies
    assert c.get("empty") is None
    s = c.stats()
    assert (s.files, s.bytes) == (1, len(b"png-bytes"))
    assert c.clear() == 1 and not c.dir.exists()


def test_api_image_entries_expire_but_cdn_entries_do_not(tmp_path):
    import os
    import time

    c = ImageCache(tmp_path)
    api = "https://api.scryfall.com/cards/znr/90/?format=image&version=png"
    cdn = "https://cards.scryfall.io/png/front/x.png?1700000000"
    c.put(api, b"a")
    c.put(cdn, b"b")
    old = time.time() - 40 * 24 * 3600
    os.utime(c.path(api), (old, old))
    os.utime(c.path(cdn), (old, old))
    assert c.get(api) is None and c.get(cdn) == b"b"


def test_reinstall_repoints_the_wrapper_to_the_new_instance(tmp_path):
    mod = SimpleNamespace(request_scryfall=lambda url: SimpleNamespace(content=b"x", status_code=200))
    first, second = ImageCache(tmp_path / "1"), ImageCache(tmp_path / "2")
    first.install(mod)
    second.install(mod)
    mod.request_scryfall("https://cards.scryfall.io/png/front/y.png?1")
    assert (first.hits, first.misses) == (0, 0) and (second.hits, second.misses) == (0, 1)
    assert list((tmp_path / "2").rglob("*.png")) and not (tmp_path / "1").exists()


def test_install_serves_images_from_disk_and_passes_api_calls_through(tmp_path):
    calls = []

    def real(url):
        calls.append(url)
        return SimpleNamespace(content=b"IMG:" + url.encode(), status_code=200)

    mod = SimpleNamespace(request_scryfall=real)
    c = ImageCache(tmp_path)
    c.install(mod)
    c.install(mod)  # idempotent — no double wrapping
    img = "https://cards.scryfall.io/png/front/x.png?1"
    assert mod.request_scryfall(img).content == b"IMG:" + img.encode()
    assert mod.request_scryfall(img).content == b"IMG:" + img.encode()
    assert calls == [img]  # second call was a cache hit
    mod.request_scryfall("https://api.scryfall.com/cards/collection")
    mod.request_scryfall("https://api.scryfall.com/cards/collection")
    assert calls[1:] == ["https://api.scryfall.com/cards/collection"] * 2  # API calls never cached
    assert (c.hits, c.misses) == (1, 1)


def test_concurrent_puts_of_the_same_url_never_corrupt_the_entry(tmp_path):
    import threading

    c = ImageCache(tmp_path)
    payload = b"P" * 200_000
    errors = []

    def writer():
        try:
            for _ in range(20):
                c.put("same-url", payload)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert c.get("same-url") == payload
    assert not list(tmp_path.rglob("*.part"))
