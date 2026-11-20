"""Disposable real-role directory composition used by LOC-02/03/04 tests."""
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg import sql
from sqlalchemy import create_engine, text

from roghyar.directory_governance import Directory, DirectoryReviewer, HospitalRevision, ImportBatch, Review, Source
from roghyar.main import engine

EVIDENCE = b"SYNTHETIC DEVELOPMENT ONLY fixture evidence"
SOURCE = "synthetic"


@pytest.fixture(scope="module")
def directory_actors(database):
    private = Ed25519PrivateKey.generate()
    logins, connections = {}, {}
    now = datetime.now(UTC)
    for role in ("importer", "reviewer", "operator"):
        login = f"dir_{role}_{uuid4().hex[:12]}"
        logins[role] = login
        with engine.begin() as owner:
            pg = owner.connection.driver_connection
            pg.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}").format(
                sql.Identifier(login), sql.Literal("synthetic-disposable-directory")))
            pg.execute(sql.SQL("GRANT {} TO {}").format(
                sql.Identifier(f"roghyar_directory_{role}"), sql.Identifier(login)))
        connections[role] = create_engine(engine.url.set(username=login, password="synthetic-disposable-directory"),
                                           hide_parameters=True)
    authority = DirectoryReviewer(logins["reviewer"], uuid4(), "synthetic_review", frozenset({SOURCE}),
                                  private.public_key(), now - timedelta(days=1), now + timedelta(days=1))
    credential = "synthetic_" + uuid4().hex
    try:
        yield logins, connections, private, authority, credential
    finally:
        for connection in connections.values():
            connection.dispose()
        with engine.begin() as owner:
            for login in logins.values():
                owner.connection.driver_connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(login)))


class Fixture:
    def __init__(self, actors):
        logins, connections, self.key, self.authority, self.credential = actors
        self.connections = connections
        self.logins = logins
        self.services = {role: Directory(conn, reviewers={self.credential: self.authority},
                                         sources=frozenset({SOURCE}), logins=frozenset({logins[role]}))
                         for role, conn in connections.items()}
        self.ids = set()
        self.revocations = set()

    def candidate(self, hospital=None, **values):
        hospital = hospital or uuid4()
        self.ids.add(hospital)
        fields = dict(schema_version=1, hospital_id=hospital, revision=1, name="Synthetic Directory Center",
                      source_name="Synthetic Directory Center", address="Synthetic road", city="Synthetic city",
                      district="Synthetic district", province="Synthetic province", latitude=0.0, longitude=140.0,
                      emergency=False, source=Source(
                          organization=SOURCE, record="hospital_" + hospital.hex, version=1,
                          kind="official_register", reference="synthetic-evidence",
                          observed_at=datetime.now(UTC) - timedelta(seconds=2),
                          evidence_digest=sha256(EVIDENCE).hexdigest()))
        return HospitalRevision(**(fields | values))

    def payload(self, *candidates):
        return ImportBatch(schema_version=1,batch_id=uuid4(),records=candidates).model_dump_json().encode()

    def approval(self, candidate, **overrides):
        reviewed_at = datetime.now(UTC) - timedelta(milliseconds=5)
        fields = dict(hospital_id=candidate.hospital_id, revision=candidate.revision,
                      digest=candidate.digest(), evidence_digest=candidate.source.evidence_digest,
                      field_digests=candidate.field_digests(), decision="approved",
                      reviewer_id=self.authority.reviewer_id, credential=self.credential,
                      organization=self.authority.organization, reviewed_at=reviewed_at,
                      expires_at=reviewed_at + timedelta(minutes=10), signature="0"*128)
        approval = Review(**(fields | overrides))
        return approval.model_copy(update={"signature": self.key.sign(approval.signing_bytes()).hex()})

    def submit(self, *candidates):
        return self.services["importer"].submit(self.payload(*candidates), {"synthetic-evidence": EVIDENCE})

    def publish(self, candidate):
        self.submit(candidate)
        self.services["reviewer"].review(self.approval(candidate), evidence=EVIDENCE)
        self.services["operator"].publish(candidate.hospital_id, candidate.revision)

    def enroll(self, hospital):
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT name,address,city,district,province,aliases,is_emergency_24_7,
                    ST_X(location::geometry) AS longitude,ST_Y(location::geometry) AS latitude
                FROM roghyar.hospitals WHERE id=:id
            """), {"id": hospital}).mappings().one()
        data = dict(row)
        data["emergency"] = data.pop("is_emergency_24_7")
        # Legacy LOC-03 fixtures intentionally exercise duplicate stored aliases. Normalize them
        # for governed publication, then keep duplicate behavior covered at LOC-04 import.
        data["aliases"] = tuple(dict.fromkeys(data["aliases"] or ()))
        self.publish(self.candidate(hospital, **data))

    def revoke(self, kind, subject):
        self.revocations.add((kind, subject))
        self.services["operator"].revoke(kind, subject)


@pytest.fixture
def governed_fixtures(directory_actors):
    fixture = Fixture(directory_actors)
    yield fixture
    with engine.begin() as owner:
        params = {"ids": list(fixture.ids)}
        for table in ("directory_current", "directory_events", "directory_reviews", "directory_revisions"):
            owner.execute(text(f"DELETE FROM roghyar.{table} WHERE hospital_id=ANY(:ids)"), params)
        owner.execute(text("DELETE FROM roghyar.hospitals WHERE id=ANY(:ids)"), params)
        for kind, subject in fixture.revocations:
            owner.execute(text("DELETE FROM roghyar.directory_revocations WHERE kind=:kind AND subject=:subject"),
                          {"kind": kind, "subject": subject})
