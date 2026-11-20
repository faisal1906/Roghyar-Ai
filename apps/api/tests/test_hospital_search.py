"""Synthetic-only LOC-02 validation and actual disposable PostGIS acceptance."""
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from roghyar.hospital_search import HospitalSearch, NEARBY
from roghyar.main import app, engine
from roghyar.scoped_session import scoped_session
from test_database_security import VERIFIER, database, token

# Independently known WGS84 equatorial scale (semi-major axis 6378137 m).
EQUATOR_METERS_PER_DEGREE = 111319.49079327358
ORIGIN = {"latitude": 0, "longitude": 140}
INSERT = text("""
    INSERT INTO roghyar.hospitals
      (id,name,address,city,district,province,location,verified,published,verified_on,verified_by,source,
       is_emergency_24_7)
    VALUES (:id,:marker,:marker,:marker,:marker,:marker,
       ST_SetSRID(ST_MakePoint(:longitude,:latitude),4326)::geography,
       :verified,:published,:verified_on,:reviewer,:source,:emergency)
""")


@pytest.fixture
def add_hospital(database):
    from datetime import UTC, datetime

    ids = []
    def add(meters=0, *, longitude=None, latitude=0, emergency=False, verified=True, published=True,
            metadata=True, identity=None):
        identifier = identity or uuid4()
        with engine.begin() as conn:
            conn.execute(INSERT, {
                "id": identifier, "marker": "SYNTHETIC—DEVELOPMENT ONLY",
                "longitude": 140 + meters / EQUATOR_METERS_PER_DEGREE if longitude is None else longitude,
                "latitude": latitude, "verified": verified, "published": published,
                "verified_on": datetime.now(UTC) if metadata and verified else None,
                "reviewer": uuid4() if metadata and verified else None,
                "source": "SYNTHETIC—DEVELOPMENT ONLY" if metadata and verified else None,
                "emergency": emergency,
            })
        ids.append(identifier)
        return identifier

    yield add
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM roghyar.hospitals WHERE id = ANY(:ids)"), {"ids": ids})


@pytest.mark.parametrize("case,edits", [
    ("latitude", {"latitude": 91}), ("longitude", {"longitude": -181}),
    ("nan", {"latitude": float("nan")}), ("infinity", {"longitude": float("inf")}),
    ("boolean", {"latitude": True}), ("numeric_string", {"longitude": "140.00000000000000000001"}),
    ("missing", {"latitude": None}), ("radius", {"radius_km": 0}), ("radius_float", {"radius_km": 5.0}),
    ("sort", {"sort": "farthest"}), ("injection", {"specialty": "a'; SELECT 1"}),
    ("limit", {"limit": 51}), ("unknown_field", {"verified": True}),
], ids=("latitude", "longitude", "nan", "infinity", "boolean", "numeric_string", "missing",
        "radius", "radius_float", "sort", "injection", "limit", "unknown_field"))
def test_loc02_invalid_query(case, edits):
    assert HospitalSearch(engine).search(ORIGIN | edits).status == "invalid_query"


@pytest.mark.parametrize("case,edits,expected", [
    ("type", {"type": "government"}, ("type",)),
    ("open_now", {"open_now": False}, ("open_now",)),
    ("specialty", {"specialty": "cardiology"}, ("specialty",)),
    ("specialty_sort", {"sort": "specialty_match"}, ("specialty_match",)),
    ("combined", {"type": "new_type", "open_now": True, "specialty": "icu"}, ("type", "open_now", "specialty")),
], ids=("type", "open_now", "specialty", "specialty_sort", "combined"))
def test_loc02_unsupported_filters(case, edits, expected):
    result = HospitalSearch(engine).search(ORIGIN | edits)
    assert result.status == "unsupported_filter" and result.unsupported_filters == expected


@pytest.mark.parametrize("radius", [5, 15, 30, 60])
def test_loc02_radius_boundary(radius, database, add_hospital):
    inner = add_hospital(radius * 1000 - 0.05)
    outer = add_hospital(radius * 1000 + 0.05)
    query = HospitalSearch(database)
    result = query.search(ORIGIN | {"radius_km": radius})
    assert result.status == "found" and result.radius_km == radius and not result.expanded
    assert result.qualifying_count == 1 and [row.id for row in result.hospitals] == [inner]
    assert result.hospitals[0].distance_km == pytest.approx(radius - 0.00005, abs=0.000001)
    assert outer not in [row.id for row in result.hospitals]


@pytest.mark.parametrize("meters,radius,status", [
    (1000, 5, "found"), (8000, 15, "found"), (21000, 30, "found"),
    (45000, 60, "found"), (61000, 60, "no_matches"),
], ids=("initial", "once", "twice", "to_sixty", "empty"))
def test_loc02_expansion(meters, radius, status, database, add_hospital):
    identifier = add_hospital(meters, emergency=True)
    distractor = add_hospital(500, emergency=False)
    service = HospitalSearch(database)
    result = service.search(ORIGIN | {"emergency_24_7": True})
    assert (result.status, result.radius_km, result.expanded) == (status, radius, radius != 5)
    assert [row.id for row in result.hospitals] == ([identifier] if status == "found" else [])
    assert result.qualifying_count == len(result.hospitals)
    assert distractor not in [row.id for row in result.hospitals]
    if status == "found":
        assert result.hospitals[0].distance_km == pytest.approx(meters / 1000, abs=0.000001)


def test_loc02_explicit_radius_and_empty_directory(database, add_hospital):
    service = HospitalSearch(database)
    assert service.search(ORIGIN).status == "directory_unavailable"
    add_hospital(6000)
    result = service.search(ORIGIN | {"radius_km": 5})
    assert (result.status, result.radius_km, result.expanded, result.hospitals) == ("no_matches", 5, False, ())


def test_loc02_sort_limit_count_zero_distance_and_null_filters(database, add_hospital):
    zero = add_hospital()
    second = add_hospital(1000, emergency=True, identity=UUID(int=2))
    first = add_hospital(1000, emergency=True, identity=UUID(int=1))
    service = HospitalSearch(database)
    default = service.search(ORIGIN | {"limit": 2, "type": None, "open_now": None, "specialty": None,
                                       "emergency_24_7": None})
    assert default.qualifying_count == 3
    assert [row.id for row in default.hospitals] == [zero, first]
    assert default.hospitals[0].distance_km == 0
    assert not {"latitude", "longitude", "location", "verified_by", "source"} & default.hospitals[0].model_dump().keys()
    selected = service.search(ORIGIN | {"sort": "emergency_first"})
    assert [row.id for row in selected.hospitals] == [first, second, zero]
    non_emergency = service.search(ORIGIN | {"emergency_24_7": False})
    assert [row.id for row in non_emergency.hospitals] == [zero]


@pytest.mark.parametrize("case,origin,point,distance", [
    ("antimeridian", (0, 179.99), (0, -179.99), 2.22639),
    ("near_pole", (89.9, 0), (89.9, 90), 15.79591),
], ids=("antimeridian", "near_pole"))
def test_loc02_geographic_edges(case, origin, point, distance, database, add_hospital):
    identifier = add_hospital(latitude=point[0], longitude=point[1])
    result = HospitalSearch(database).search({"latitude": origin[0], "longitude": origin[1]})
    assert result.hospitals[0].id == identifier
    # Published WGS84 equatorial conversion and polar curvature estimate, within 25 m.
    assert result.hospitals[0].distance_km == pytest.approx(distance, abs=0.025)


def test_loc02_runtime_rls_eligibility_and_revocation(database, add_hospital):
    visible = add_hospital(1000)
    private = add_hospital(20, published=False)
    unverified = add_hospital(10, verified=False, published=False)
    with pytest.raises(IntegrityError):
        add_hospital(verified=False, published=True)
    with pytest.raises(IntegrityError):
        add_hospital(metadata=False)

    admin = uuid4()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO roghyar.admin_users (hospital_id,auth_subject,email,role,active)
            VALUES (:hospital,:actor,'synthetic@example.invalid','hospital_admin',true)
        """), {"hospital": private, "actor": admin})
    try:
        with scoped_session(database, VERIFIER, token("hospital_admin", admin)) as session:
            assert session.scalar(text("SELECT current_setting('transaction_read_only')")) == "on"
            assert session.scalar(text("SELECT id FROM roghyar.hospitals WHERE id=:id"), {"id": private}) == private
            assert session.scalar(text("SELECT id FROM roghyar.hospitals WHERE id=:id"), {"id": unverified}) is None
        service = HospitalSearch(database)
        results = service.search(ORIGIN)
        assert [row.id for row in results.hospitals] == [visible]
        with engine.begin() as conn:
            conn.execute(text("UPDATE roghyar.hospitals SET published=false WHERE id=:id"), {"id": visible})
        assert service.search(ORIGIN).status == "directory_unavailable"
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM roghyar.admin_users WHERE auth_subject=:actor"), {"actor": admin})


def test_loc02_runtime_cannot_publish(database, add_hospital):
    identifier = add_hospital()
    with pytest.raises(DBAPIError):
        with database.begin() as conn:
            conn.execute(text("UPDATE roghyar.hospitals SET published=false WHERE id=:id"), {"id": identifier})
    assert HospitalSearch(database).search(ORIGIN).hospitals[0].id == identifier
    assert HospitalSearch(engine).search(ORIGIN).status == "untrusted_connection"


def test_loc02_query_timeout(database, add_hospital):
    identifier = add_hospital()
    with engine.begin() as conn:
        conn.execute(text("LOCK TABLE roghyar.hospitals IN ACCESS EXCLUSIVE MODE"))
        assert HospitalSearch(database).search(ORIGIN).status == "query_timeout"
    assert HospitalSearch(database).search(ORIGIN).hospitals[0].id == identifier


def test_loc02_database_outage():
    from psycopg import OperationalError

    def unavailable():
        raise OperationalError("synthetic disconnected database")

    broken = create_engine("postgresql+psycopg://", creator=unavailable, hide_parameters=True)
    try:
        assert HospitalSearch(broken).search(ORIGIN).status == "database_unavailable"
    finally:
        broken.dispose()


def test_loc02_index_plan(database, add_hospital):
    add_hospital(2000)
    params = ORIGIN | {"radius_m": 5000, "emergency": None, "emergency_first": False, "limit": 20}
    with scoped_session(database, VERIFIER, None) as session:
        natural = session.execute(text("EXPLAIN (FORMAT JSON) " + NEARBY.text), params).scalar_one()
        session.execute(text("SET LOCAL enable_seqscan = off"))
        index = session.execute(text("EXPLAIN (FORMAT JSON) " + NEARBY.text), params).scalar_one()
        assert session.scalar(text("""
            SELECT EXISTS (
                SELECT 1 FROM pg_indexes WHERE schemaname='roghyar'
                AND indexname='ix_hospitals_location' AND indexdef LIKE '%USING gist (location)%'
            )
        """))
    print("LOC02 plan natural index=" + str("ix_hospitals_location" in str(natural)).lower()
          + " diagnostic index=" + str("ix_hospitals_location" in str(index)).lower())


def test_loc02_private_route_and_no_coordinate_output():
    client = TestClient(app)
    for url in ("/hospitals/nearby", "/api/v1/hospitals/nearby"):
        response = client.get(url + "?lat=12.3456789&lng=23.4567891")
        assert response.status_code == 404
        assert "12.3456789" not in response.text and "23.4567891" not in response.text
    assert not any("hospitals/nearby" in route for route in app.openapi()["paths"])
