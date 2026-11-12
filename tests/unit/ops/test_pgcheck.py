"""The pure parts of kbot.ops.pgcheck (stage E4b)."""

from __future__ import annotations

from kbot.ops import pgcheck


def test_catalog_differences_are_named() -> None:
    a = {
        "signature": "market.seal_day(date)",
        "owner": "kbot_owner",
        "config": [],
        "body_sha256": "1",
    }
    b = {**a, "signature": "market.prepare_partitions()"}
    assert pgcheck.catalog_problems([a, b], [a, b]) == []
    assert pgcheck.catalog_problems([a], [a, b]) == [
        "missing definer function: market.prepare_partitions()"
    ]
    assert pgcheck.catalog_problems([a, b], [a]) == [
        "unexpected definer function: market.prepare_partitions()"
    ]
    changed = {**a, "body_sha256": "2"}
    assert pgcheck.catalog_problems([changed], [a]) == [
        "definer function differs: market.seal_day(date)"
    ]


def _rows() -> list[tuple[object, ...]]:
    return [
        (name, login, limit, True, False, config)
        for name, (login, limit, config) in pgcheck.EXPECTED_ATTRIBUTES.items()
    ]


def test_role_attributes_must_match_the_table_of_02() -> None:
    assert pgcheck.attribute_problems(_rows()) == []
    rows = _rows()
    rows[2] = (rows[2][0], rows[2][1], 99, rows[2][3], rows[2][4], rows[2][5])
    assert any("attributes" in p for p in pgcheck.attribute_problems(rows))
    with_password = [
        (r[0], r[1], r[2], False, r[4], r[5]) if r[0] == "kbot_ingest" else r for r in _rows()
    ]
    assert "kbot_ingest has a password" in pgcheck.attribute_problems(with_password)
    powerful = [
        (r[0], r[1], r[2], r[3], True, r[5]) if r[0] == "kbot_backup" else r for r in _rows()
    ]
    assert "kbot_backup has a superuser-like attribute" in pgcheck.attribute_problems(powerful)
    assert "role missing: kbot_archive" in pgcheck.attribute_problems(_rows()[:-1])
    extra = [*_rows(), ("kbot_extra", True, 5, True, False, [])]
    assert "unexpected role: kbot_extra" in pgcheck.attribute_problems(extra)


def test_pg_hba_only_local_peer_and_no_map_on_the_server() -> None:
    assert pgcheck.hba_problems([("local", "peer", None, None)], allow_map=False) == []
    assert pgcheck.hba_problems([], allow_map=False) == ["pg_hba.conf has no rules"]
    assert pgcheck.hba_problems([("host", "md5", None, None)], allow_map=False)
    mapped = [("local", "peer", ["map=kbot_ci"], None)]
    assert pgcheck.hba_problems(mapped, allow_map=True) == []
    assert pgcheck.hba_problems(mapped, allow_map=False) == [
        "pg_hba rule has options on the server: ['map=kbot_ci']"
    ]
    assert pgcheck.hba_problems([("local", "peer", None, "bad line")], allow_map=False)
