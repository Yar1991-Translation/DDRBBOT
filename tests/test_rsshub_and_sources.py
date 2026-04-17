from __future__ import annotations

import pytest

from ddrbbot.database import SQLiteRepository
from ddrbbot.main import _rsshub2_seed_sources
from ddrbbot.rsshub import validate_rsshub_feed_url


def test_validate_rsshub_accepts_marker_host() -> None:
    u = validate_rsshub_feed_url(
        "https://rsshub2.asailor.org/twitter/user/roblox",
        host_markers=frozenset({"rsshub"}),
        extra_hosts=frozenset({"localhost"}),
    )
    assert "rsshub2.asailor.org" in u


def test_validate_rsshub_accepts_extra_host() -> None:
    u = validate_rsshub_feed_url(
        "http://localhost:1200/test/feed",
        host_markers=frozenset({"rsshub"}),
        extra_hosts=frozenset({"localhost"}),
    )
    assert "localhost" in u


def test_validate_rsshub_rejects_unknown_host() -> None:
    with pytest.raises(ValueError):
        validate_rsshub_feed_url(
            "https://evil.example/feed.xml",
            host_markers=frozenset({"rsshub"}),
            extra_hosts=frozenset({"localhost"}),
        )


def test_touch_and_list_sources(tmp_path) -> None:
    db = SQLiteRepository(tmp_path / "s.db")
    db.initialize()
    db.touch_source_feed(source_type="rss", source_name="TestSrc", feed_url="https://rsshub2.asailor.org/x")
    rows = db.list_sources(limit=10)
    assert len(rows) == 1
    assert rows[0]["name"] == "TestSrc"
    assert rows[0]["url"] == "https://rsshub2.asailor.org/x"


def test_upsert_source_registration(tmp_path) -> None:
    db = SQLiteRepository(tmp_path / "u.db")
    db.initialize()
    r1 = db.upsert_source_registration(
        source_type="rss",
        name="Wiki",
        feed_url="https://rsshub2.asailor.org/wiki/feed",
        credibility_level="community",
    )
    assert r1["name"] == "Wiki"
    r2 = db.upsert_source_registration(
        source_type="rss",
        name="Wiki",
        feed_url="https://rsshub2.asailor.org/wiki/feed2",
        credibility_level="official",
    )
    assert r2["url"] == "https://rsshub2.asailor.org/wiki/feed2"
    assert r2["credibility_level"] == "official"


def test_rsshub2_seed_sources_contains_requested_feeds() -> None:
    seeds = _rsshub2_seed_sources()
    urls = {item["feed_url"] for item in seeds}
    assert "https://rsshub2.asailor.org/x/user/forsaken2024" in urls
    assert "https://rsshub2.asailor.org/x/user/doorsgame" in urls
    assert "https://rsshub2.asailor.org/fandom/wiki/forsaken2024" in urls
    assert "https://rsshub2.asailor.org/fandom/wiki/doors-game" in urls
