"""LOC-03 synthetic-only validation and restricted PostgreSQL tests."""
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from roghyar.hospital_autocomplete import FUZZY_THRESHOLD, MATCHES, HospitalAutocomplete, HospitalAutocompleteQuery
from roghyar.main import app, engine
from roghyar.scoped_session import scoped_session
from test_database_security import VERIFIER, database, token

FIXTURE = "SYNTHETIC—DEVELOPMENT ONLY"
INSERT = text("""
    INSERT INTO roghyar.hospitals
        (id,name,address,city,district,province,location,aliases,verified,published,verified_on,verified_by,source)
    VALUES (:id,:name,:marker,:city,:district,:province,
        ST_SetSRID(ST_MakePoint(0,0),4326)::geography,:aliases,:verified,:published,:date,:reviewer,:source)
""")


@pytest.fixture
def hospital(database):
    ids = []

    def add(name="Synthetic Northbridge Center", *, aliases=None, city="Synthetic East",
            district="Synthetic District", province="Synthetic Province", identity=None,
            verified=True, published=True, date=True, source=True):
        item = identity or uuid4()
        with engine.begin() as conn:
            conn.execute(INSERT, dict(
                id=item, name=name, marker=FIXTURE, city=city, district=district, province=province,
                aliases=aliases, verified=verified, published=published,
                date=datetime.now(UTC) if verified and date else None,
                reviewer=uuid4() if verified else None,
                source=FIXTURE if verified and source else None,
            ))
        ids.append(item)
        return item

    yield add
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM roghyar.hospitals WHERE id = ANY(:ids)"), {"ids": ids})


@pytest.mark.parametrize(("changes", "expected"), [
    pytest.param({"query": ""}, "invalid_query", id="empty"),
    pytest.param({"query": "  "}, "invalid_query", id="whitespace"),
    pytest.param({"query": "Pa"}, "query_too_short", id="short"),
    pytest.param({"query": "a" * 121}, "invalid_query", id="length"),
    pytest.param({"query": "Hospital\x00"}, "invalid_query", id="null"),
    pytest.param({"query": "Hospital\u202e"}, "invalid_query", id="control"),
    pytest.param({"query": "Hospital\ud800"}, "invalid_query", id="surrogate"),
    pytest.param({"query": "a" * 24}, "invalid_query", id="repeat"),
    pytest.param({"query": "a" + "\u0300" * 35}, "invalid_query", id="combining"),
    pytest.param({"query": "%_%"}, "invalid_query", id="wildcard"),
    pytest.param({"limit": 26}, "invalid_query", id="limit"),
    pytest.param({"limit": True}, "invalid_query", id="boolean"),
    pytest.param({"city": "  "}, "invalid_query", id="bad_city"),
    pytest.param({"district": "x_%"}, "invalid_query", id="bad_district"),
    pytest.param({"latitude": 0}, "invalid_query", id="unknown_field"),
])
def test_loc03_validation(changes, expected):
    assert HospitalAutocompleteQuery(query="  Synthetic   Northbridge  ").query == "Synthetic Northbridge"
    assert HospitalAutocomplete(engine).search({"query": "Synthetic Northbridge"} | changes).status == expected


@pytest.mark.parametrize(("term", "match"), [
    pytest.param("Synthetic Northbridge Center", "exact_name", id="name"),
    pytest.param("synthetic NORTHBRIDGE center", "exact_name", id="case"),
    pytest.param("Synthetic Northbridge Cen", "name_prefix", id="prefix"),
    pytest.param("North Bridge Outpost", "exact_alias", id="alias"),
    pytest.param("North Bridge Out", "alias_prefix", id="alias_prefix"),
    pytest.param("Synthetic Northbrdge Center", "fuzzy_name", id="omission"),
    pytest.param("Synthetic Norrthbridge Center", "fuzzy_name", id="insertion"),
    pytest.param("Synthetic Nrothbridge Center", "fuzzy_name", id="transpose"),
    pytest.param("Synthetic Northbridge, Center", "fuzzy_name", id="punctuation"),
])
def test_loc03_matching(term, match, database, hospital):
    identifier = hospital(aliases=["North Bridge Outpost", "North Bridge Outpost"])
    result = HospitalAutocomplete(database).search({"query": term})
    assert result.status == "found"
    assert [(row.id, row.match) for row in result.suggestions] == [(identifier, match)]
    assert result.suggestions[0].name == "Synthetic Northbridge Center"


@pytest.mark.parametrize(("stored", "query", "aliases", "expected"), [
    pytest.param("آزمائشی ہسپتال", "آزمائشی", [], "name_prefix", id="urdu"),
    pytest.param("ازمېښتي روغتون", "ازمېښتي روغتون", [], "exact_name", id="pashto"),
    pytest.param("آزمائشی ہسپتال", "Synthetic آزمائشی", ["Synthetic آزمائشی"], "exact_alias", id="mixed"),
    pytest.param("Synthétique Centre", "Synthe\u0301tique   Centre", [], "exact_name", id="nfc"),
    pytest.param("آزمائشی ہسپتال", "Azmaishi Hospital", [], None, id="no_transliteration"),
    pytest.param("Synthetic\u00a0Spaced", "Synthetic Spaced", [], "exact_name", id="spaces"),
])
def test_loc03_unicode(stored, query, aliases, expected, database, hospital):
    identifier = hospital(stored, aliases=aliases)
    service = HospitalAutocomplete(database)
    assert service.search({"query": stored}).suggestions[0].id == identifier
    result = service.search({"query": query})
    if expected:
        assert [(row.id, row.name, row.match) for row in result.suggestions] == [(identifier, stored, expected)]
    else:
        assert result.status == "no_matches"


def test_loc03_ranking(database, hospital):
    term = "Synthetic Northbridge"
    exact = hospital(term, identity=UUID(int=9))
    alias = hospital("Synthetic Southern Annex", aliases=[term], identity=UUID(int=8))
    prefix = hospital(term + " Center", identity=UUID(int=7))
    alias_prefix = hospital("Synthetic Coastal Annex", aliases=[term + " Relief"], identity=UUID(int=6))
    closer = hospital("Synthetic Northbrdge", identity=UUID(int=5))
    farther = hospital("Synthetic Nortbrdge", identity=UUID(int=4))
    service = HospitalAutocomplete(database)
    result = service.search({"query": term})
    assert [row.id for row in result.suggestions] == [exact, alias, prefix, alias_prefix, closer, farther]
    assert service.search({"query": term, "limit": 2}).suggestions == result.suggestions[:2]
    assert service.search({"query": term}) == result
    assert service.search({"query": "Unrelated Alpine Cabin"}).status == "no_matches"


def test_loc03_same_name_across_cities(database, hospital):
    first = hospital(city="Synthetic West", identity=UUID(int=1))
    second = hospital(city="Synthetic East", identity=UUID(int=2))
    service = HospitalAutocomplete(database)
    result = service.search({"query": "Synthetic Northbridge Center"})
    assert [(row.id, row.city) for row in result.suggestions] == [(first, "Synthetic West"), (second, "Synthetic East")]
    assert [row.id for row in service.search({"query": "Synthetic Northbridge", "city": " synthetic EAST ",
        "district": "Synthetic District", "province": "Synthetic Province"}).suggestions] == [second]
    assert service.search({"query": "Synthetic Northbridge", "city": "Synthetic North"}).status == "no_matches"


def test_loc03_eligibility_and_withdrawal(database, hospital):
    visible = hospital()
    hidden = hospital(published=False, aliases=["Private Seaside Infirmary"])
    hospital("Unverified Lake Clinic", verified=False, published=False)
    with pytest.raises(IntegrityError):
        hospital(date=False)
    with pytest.raises(IntegrityError):
        hospital(source=False)
    admin = uuid4()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO roghyar.admin_users(hospital_id,auth_subject,email,role,active)
            VALUES (:hospital,:admin,'synthetic@example.invalid','hospital_admin',true)
        """), {"hospital": hidden, "admin": admin})
    try:
        with scoped_session(database, VERIFIER, token("hospital_admin", admin)) as session:
            assert session.scalar(text("SELECT id FROM roghyar.hospitals WHERE id=:id"), {"id": hidden}) == hidden
            assert session.scalar(text("SELECT current_setting('transaction_read_only')")) == "on"
        service = HospitalAutocomplete(database)
        result = service.search({"query": "Synthetic Northbridge Center"})
        assert [row.id for row in result.suggestions] == [visible]
        assert set(result.suggestions[0].model_dump()) == {
            "id", "name", "city", "district", "province", "verified_on", "match"}
        assert service.search({"query": "Private Seaside Infirmary"}).status == "no_matches"
        assert service.search({"query": "Unverified Lake Clinik"}).status == "no_matches"
        with engine.begin() as conn:
            conn.execute(text("UPDATE roghyar.hospitals SET published=false WHERE id=:id"), {"id": visible})
        assert service.search({"query": "Synthetic Northbridge Center"}).status == "directory_unavailable"
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM roghyar.admin_users WHERE auth_subject=:admin"), {"admin": admin})


def test_loc03_multiple_aliases_same_term_and_literal_input(database, hospital):
    second = hospital("Synthetic East Annex", aliases=["Synthetic Shared Alias"], identity=UUID(int=2))
    first = hospital("Synthetic West Annex", aliases=["Synthetic Shared Alias", "Synthetic Shared Alias"],
                     identity=UUID(int=1))
    service = HospitalAutocomplete(database)
    result = service.search({"query": "Synthetic Shared Alias"})
    assert [(row.id, row.match) for row in result.suggestions] == [(first, "exact_alias"), (second, "exact_alias")]
    assert service.search({"query": "ZZZ' OR 'QQQ"}).status == "no_matches"


def test_loc03_identity_change_requires_withdrawal(database, hospital):
    identifier = hospital()
    service = HospitalAutocomplete(database)
    assert service.search({"query": "Synthetic Northbridge"}).suggestions[0].id == identifier
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("UPDATE roghyar.hospitals SET name='Synthetic Changed' WHERE id=:id"),
                         {"id": identifier})
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE roghyar.hospitals SET name='Synthetic Changed',verified=false,published=false WHERE id=:id
        """), {"id": identifier})
    assert service.search({"query": "Synthetic Changed"}).status == "directory_unavailable"


def test_loc03_runtime_and_timeout(database, hospital):
    identifier = hospital()
    service = HospitalAutocomplete(database)
    query = {"query": "Synthetic Northbridge"}
    assert HospitalAutocomplete(engine).search(query).status == "untrusted_connection"
    with pytest.raises(DBAPIError):
        with database.begin() as conn:
            conn.execute(text("UPDATE roghyar.hospitals SET aliases=ARRAY['Unauthorized'] WHERE id=:id"),
                         {"id": identifier})
    with engine.begin() as conn:
        conn.execute(text("LOCK TABLE roghyar.hospitals IN ACCESS EXCLUSIVE MODE"))
        assert service.search(query).status == "query_timeout"
    assert service.search(query).suggestions[0].id == identifier


def test_loc03_outage_and_closed_route():
    from psycopg import OperationalError
    def unavailable():
        raise OperationalError("synthetic outage")
    broken = create_engine("postgresql+psycopg://", creator=unavailable, hide_parameters=True)
    try:
        assert HospitalAutocomplete(broken).search({"query": "Synthetic Northbridge"}).status == "database_unavailable"
    finally:
        broken.dispose()
    client = TestClient(app)
    for path in ("/hospitals/search", "/api/v1/hospitals/search"):
        response = client.get(path, params={"q": "Synthetic private search sentinel"})
        assert response.status_code == 404 and "sentinel" not in response.text
    assert not any("hospitals/search" in path for path in app.openapi()["paths"])


def test_loc03_index_plans(database, hospital):
    hospital()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO roghyar.hospitals(name,address,city,district,province,location,verified,published,
                                          verified_on,verified_by,source)
            SELECT CASE WHEN i % 4 = 0 THEN 'Synthetic Northbridge ' ELSE 'Synthetic Unrelated ' END || i,
                   :marker,:marker,:marker,:marker,
                   ST_SetSRID(ST_MakePoint(0,0),4326)::geography,true,true,
                   now(),gen_random_uuid(),:marker
            FROM generate_series(1,800) i
        """), {"marker": FIXTURE + " PLAN"})
        conn.execute(text("ANALYZE roghyar.hospitals"))
    try:
        with scoped_session(database, VERIFIER, None) as session:
            session.execute(text("SET LOCAL statement_timeout = '1500ms'"))
            session.execute(text("SELECT set_config('pg_trgm.similarity_threshold', :value, true)"),
                            {"value": str(FUZZY_THRESHOLD)})
            assert session.scalar(text("SELECT count(*) FROM pg_indexes WHERE schemaname='roghyar' "
                                       "AND indexname IN ('ix_hospitals_search_prefix','ix_hospitals_search_trgm')")) == 2
            plans = {}
            for label, query in (("common", "Synthetic North"), ("uncommon", "Synthetic Northbridge Center"),
                                 ("fuzzy", "Synthetic Nortbrdge Center")):
                params = dict(query=query, city=None, district=None, province=None, limit=10, threshold=FUZZY_THRESHOLD)
                plan = session.execute(text("EXPLAIN (ANALYZE, FORMAT JSON) " + MATCHES.text), params).scalar_one()
                plans[label] = (str(plan), plan[0]["Execution Time"])
        # Separate a query-shape failure from access-policy effects in a disposable diagnostic.
        with engine.begin() as owner:
            owner.execute(text("SET LOCAL enable_seqscan = off"))
            owner.execute(text("SELECT set_config('pg_trgm.similarity_threshold', :value, true)"),
                          {"value": str(FUZZY_THRESHOLD)})
            diagnostic = owner.execute(text("EXPLAIN (FORMAT JSON) " + MATCHES.text), params).scalar_one()
        print("\nLOC03 owner diagnostic prefix="
              + str("ix_hospitals_search_prefix" in str(diagnostic)).lower()
              + " trigram=" + str("ix_hospitals_search_trgm" in str(diagnostic)).lower())
        print("\nLOC03 plans " + " ".join(
            f"{label}={int('ix_hospitals_search_prefix' in plan)},{int('ix_hospitals_search_trgm' in plan)},{ms:.3f}"
            for label, (plan, ms) in plans.items()))
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM roghyar.hospitals WHERE source=:marker"), {"marker": FIXTURE + " PLAN"})
