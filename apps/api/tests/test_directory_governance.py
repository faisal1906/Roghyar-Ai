"""Live LOC-04 acceptance with independent disposable operators and source bytes."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from directory_fixtures import EVIDENCE, directory_actors, governed_fixtures
from roghyar.directory_governance import Directory, DirectoryDenied, validate_batch
from roghyar.hospital_autocomplete import FUZZY_THRESHOLD, MATCHES, HospitalAutocomplete
from roghyar.hospital_search import HospitalSearch
from roghyar.main import engine
from roghyar.scoped_session import CredentialVerifier, scoped_session
from test_database_security import database

TERM = {"query": "Synthetic Directory"}
ORIGIN = {"latitude": 0, "longitude": 140}


def assert_invisible(database):
    assert HospitalAutocomplete(database).search(TERM).status == "directory_unavailable"
    assert HospitalSearch(database).search(ORIGIN).status == "directory_unavailable"


@pytest.mark.parametrize("case", ["empty", "oversized", "invalid_utf8", "version", "coordinates", "formula",
                                  "duplicate", "unexpected", "missing_source"])
def test_loc04_invalid_import(case, governed_fixtures):
    import json
    fx = governed_fixtures
    candidate = fx.candidate()
    payload = fx.payload(candidate)
    assert validate_batch(payload).records == (candidate,)
    value = json.loads(payload)
    node = value["records"][0]
    if case == "version":
        value["schema_version"] = 99
    if case == "coordinates":
        node["latitude"] = 140
    if case == "formula":
        node["name"] = "=SYNTHETIC()"
    if case == "duplicate":
        value["records"].append(node.copy())
    if case == "unexpected":
        node["verified"] = True
    if case == "missing_source":
        node.pop("source")
    payload = {"empty": b"", "oversized": b"x" * 256_001, "invalid_utf8": b"\xff"}.get(
        case, json.dumps(value).encode())
    with pytest.raises(DirectoryDenied):
        validate_batch(payload)


def test_loc04_import_idempotency_and_transaction(database, governed_fixtures):
    fx = governed_fixtures
    first, second = fx.candidate(), fx.candidate()
    invalid_source = second.model_copy(update={"source": second.source.model_copy(update={"evidence_digest": "0"*64})})
    with pytest.raises(DirectoryDenied, match="invalid_source_evidence"):
        fx.submit(first, invalid_source)
    assert fx.submit(first) == ("submitted",)
    assert fx.submit(first) == ("unchanged",)
    assert fx.services["reviewer"].candidate_for_review(first.hospital_id, 1) == first
    with pytest.raises(DirectoryDenied, match="invalid_source_evidence"):
        fx.services["reviewer"].review(fx.approval(first), evidence=b"changed independent evidence")
    wrong_identity = second.model_copy(update={"source": first.source})
    with pytest.raises(DirectoryDenied, match="identity_conflict"):
        fx.submit(wrong_identity)
    assert fx.submit(second) == ("submitted",)
    assert_invisible(database)


@pytest.mark.parametrize("case", ["self", "revision", "digest", "scope", "organization", "signature", "revoked",
                                  "expired", "unprovisioned", "field", "evidence"])
def test_loc04_review_denials(case, database, governed_fixtures):
    fx = governed_fixtures
    candidate = fx.candidate(aliases=("Synthetic Alias",))
    fx.submit(candidate)
    reviewers = {fx.credential: fx.authority}
    changes = {
        "revision": {"revision": 2}, "digest": {"digest": "0"*64},
        "organization": {"organization": "wrong_org"}, "field": {"field_digests": {}},
        "evidence": {"evidence_digest": "0"*64},
    }
    approval = fx.approval(candidate, **changes.get(case, {}))
    if case == "signature":
        approval = approval.model_copy(update={"signature": "0"*128})
    if case in ("scope", "revoked", "expired", "self"):
        update = {
            "scope": {"sources": frozenset()}, "revoked": {"revoked": True},
            "expired": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)},
            "self": {"login": fx.logins["importer"]},
        }[case]
        reviewers[fx.credential] = replace(fx.authority, **update)
    if case == "unprovisioned":
        reviewers = {}
    service = Directory(fx.connections["reviewer"], reviewers=reviewers,
                        logins=frozenset({fx.logins["reviewer"]}))
    with pytest.raises(DirectoryDenied):
        service.review(approval, evidence=EVIDENCE)
    assert_invisible(database)
    fx.services["reviewer"].review(fx.approval(candidate), evidence=EVIDENCE)
    fx.services["operator"].publish(candidate.hospital_id, 1)
    assert HospitalSearch(database).search(ORIGIN).hospitals[0].id == candidate.hospital_id


def test_loc04_publish_and_withdraw_search_integration(database, governed_fixtures):
    fx = governed_fixtures
    candidate = fx.candidate(aliases=("Synthetic Approved Alias",))
    fx.submit(candidate)
    assert_invisible(database)
    with pytest.raises(DirectoryDenied, match="no_review"):
        fx.services["operator"].publish(candidate.hospital_id, 1)
    fx.services["reviewer"].review(fx.approval(candidate), evidence=EVIDENCE)
    assert_invisible(database)
    with pytest.raises(DirectoryDenied, match="unauthorized_operator"):
        fx.services["reviewer"].publish(candidate.hospital_id, 1)
    fx.services["operator"].publish(candidate.hospital_id, 1)
    assert HospitalSearch(database).search(ORIGIN).hospitals[0].distance_km == 0
    alias = HospitalAutocomplete(database).search({"query": "Synthetic Approved Alias"}).suggestions[0]
    assert (alias.id, alias.match) == (candidate.hospital_id, "exact_alias")
    fx.services["operator"].withdraw(candidate.hospital_id)
    assert_invisible(database)
    with pytest.raises(DirectoryDenied, match="transaction_conflict"):
        fx.services["operator"].publish(candidate.hospital_id, 1)
    assert {event["action"] for event in fx.services["operator"].inspection()
            if event["hospital_id"] == candidate.hospital_id} == {"published", "withdrawn"}


@pytest.mark.parametrize("kind", ["key", "source"])
def test_loc04_revocation_immediately_removes_both_searches(kind, database, governed_fixtures):
    fx = governed_fixtures
    candidate = fx.candidate()
    fx.publish(candidate)
    assert HospitalAutocomplete(database).search(TERM).status == "found"
    subject = fx.credential if kind == "key" else f"{candidate.source.organization}:{candidate.source.record}"
    fx.revoke(kind, subject)
    assert_invisible(database)
    with pytest.raises(DirectoryDenied, match="review_denied"):
        fx.services["operator"].publish(candidate.hospital_id, 1)


def test_loc04_freshness_runtime_and_legacy_flags(database, governed_fixtures):
    fx = governed_fixtures
    candidate = fx.candidate()
    fx.publish(candidate)
    assert HospitalSearch(database).search(ORIGIN).status == "found"
    with engine.begin() as conn:
        # Disposable owner clock fixture avoids waiting for a real validity window.
        conn.execute(text("UPDATE roghyar.directory_current SET expires_at=statement_timestamp()-interval '1 second' "
                          "WHERE hospital_id=:id"), {"id": candidate.hospital_id})
        assert conn.scalar(text("SELECT verified AND published FROM roghyar.hospitals WHERE id=:id"),
                           {"id": candidate.hospital_id})
    assert_invisible(database)
    due = fx.services["operator"].due_for_revalidation()
    assert due[0]["hospital_id"] == candidate.hospital_id and due[0]["status"] == "expired"


def test_loc04_replacement_removes_old_alias_and_rejects_old_approval(database, governed_fixtures):
    fx = governed_fixtures
    first = fx.candidate(aliases=("Obsolete Synthetic Alias",))
    fx.publish(first)
    second = type(first).model_validate(first.model_dump() | {"revision": 2, "longitude": 140.01,
        "aliases": ("Updated Synthetic Alias",), "source": first.source.model_copy(update={"version": 2})})
    fx.submit(second)
    with pytest.raises(DirectoryDenied, match="stale_revision"):
        fx.services["operator"].publish(first.hospital_id, 1)
    fx.services["reviewer"].review(fx.approval(second), evidence=EVIDENCE)
    fx.services["operator"].publish(second.hospital_id, 2)
    service = HospitalAutocomplete(database)
    assert service.search({"query": "Obsolete Synthetic Alias"}).status == "no_matches"
    assert service.search({"query": "Updated Synthetic Alias"}).suggestions[0].id == first.hospital_id
    assert HospitalSearch(database).search(ORIGIN).hospitals[0].distance_km == pytest.approx(1.113195, abs=0.00001)


def test_loc04_publication_race_one_winner(database, governed_fixtures):
    fx = governed_fixtures
    candidate = fx.candidate()
    fx.submit(candidate)
    fx.services["reviewer"].review(fx.approval(candidate), evidence=EVIDENCE)
    barrier = Barrier(2, timeout=10)
    def attempt(_):
        barrier.wait()
        try:
            fx.services["operator"].publish(candidate.hospital_id, 1)
        except DirectoryDenied as exc:
            assert str(exc) == "transaction_conflict"
            return "replay"
        return "published"
    with ThreadPoolExecutor(2) as threads:
        assert sorted(threads.map(attempt, range(2))) == ["published", "replay"]
    assert HospitalSearch(database).search(ORIGIN).qualifying_count == 1
    assert [e["action"] for e in fx.services["operator"].inspection()
            if e["hospital_id"] == candidate.hospital_id] == ["published"]


def test_loc04_database_roles_and_immutable_history(database, governed_fixtures):
    fx = governed_fixtures
    candidate = fx.candidate()
    fx.publish(candidate)
    for conn_engine, role, statement in (
        (database, None, "SELECT body FROM roghyar.directory_revisions"),
        (engine, "roghyar_maintenance", "UPDATE roghyar.hospitals SET published=true"),
        (fx.connections["operator"], "roghyar_directory_operator", "UPDATE roghyar.directory_reviews SET credential='fake'"),
        (fx.connections["operator"], "roghyar_directory_operator", "DELETE FROM roghyar.directory_events"),
        (fx.connections["importer"], "roghyar_directory_importer", "INSERT INTO roghyar.directory_current DEFAULT VALUES"),
    ):
        with pytest.raises(DBAPIError):
            with conn_engine.begin() as conn:
                if role:
                    conn.exec_driver_sql(f"SET LOCAL ROLE {role}")
                conn.execute(text(statement))
    assert HospitalAutocomplete(database).search(TERM).suggestions[0].id == candidate.hospital_id


@pytest.mark.parametrize("case", ["source", "key", "withdrawal"])
def test_loc04_invalidation_race(database, governed_fixtures, case):
    fx = governed_fixtures
    candidate = fx.candidate()
    fx.submit(candidate)
    fx.services["reviewer"].review(fx.approval(candidate), evidence=EVIDENCE)
    operand = fx.credential if case == "key" else f"{candidate.source.organization}:{candidate.source.record}"
    barrier = Barrier(2, timeout=10)
    def publication():
        barrier.wait()
        try:
            fx.services["operator"].publish(candidate.hospital_id, 1)
        except DirectoryDenied:
            return "denied"
        return "published"

    def invalidate():
        barrier.wait()
        if case == "withdrawal":
            subject = fx.services["operator"].withdraw(candidate.hospital_id)
            if subject:
                fx.revocations.add(("revision", subject))
        else:
            fx.revoke(case, operand)

    with ThreadPoolExecutor(2) as threads:
        publishing = threads.submit(publication)
        invalidating = threads.submit(invalidate)
        outcome = publishing.result(timeout=15)
        invalidating.result(timeout=15)
    assert outcome in ("published", "denied")
    assert_invisible(database)


def test_loc04_rejected_revision_and_unknown_emergency(database, governed_fixtures):
    fx = governed_fixtures
    rejected = fx.candidate()
    fx.submit(rejected)
    fx.services["reviewer"].review(fx.approval(rejected, decision="rejected"), evidence=EVIDENCE)
    with pytest.raises(DirectoryDenied, match="publication_denied"):
        fx.services["operator"].publish(rejected.hospital_id, 1)
    candidate = fx.candidate(emergency=None, aliases=("\u0622\u0632\u0645\u0627\u0626\u0634\u06cc \u06c1\u0633\u067e\u062a\u0627\u0644",
                                                    "\u0627\u0632\u0645\u06d0\u069a\u062a\u064a \u0631\u0648\u063a\u062a\u0648\u0646"))
    fx.publish(candidate)
    service = HospitalSearch(database)
    assert service.search(ORIGIN).hospitals[0].is_emergency_24_7 is None
    for flag in (False, True):
        assert service.search(ORIGIN | {"emergency_24_7": flag}).status == "no_matches"
    for alias in candidate.aliases:
        assert HospitalAutocomplete(database).search({"query": alias}).suggestions[0].match == "exact_alias"


def test_loc04_legacy_verified_row_has_no_search_eligibility(database, governed_fixtures):
    fx = governed_fixtures
    candidate = fx.candidate()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO roghyar.hospitals(id,name,address,city,district,province,location,verified,published,
                                         verified_on,verified_by,source)
            VALUES (:id,:marker,:marker,:marker,:marker,:marker,ST_SetSRID(ST_MakePoint(140,0),4326)::geography,
                    true,true,clock_timestamp(),gen_random_uuid(),:marker)
        """), {"id": candidate.hospital_id, "marker": "Synthetic Directory Legacy"})
    assert_invisible(database)


def test_loc04_individual_alias_withdrawal(database, governed_fixtures):
    fx = governed_fixtures
    candidate = fx.candidate(aliases=("Obsolete Branch Label", "Current Annex Label"))
    fx.publish(candidate)
    search = HospitalAutocomplete(database)
    assert search.search({"query": "Obsolete Branch Label"}).suggestions[0].match == "exact_alias"
    subject = fx.services["operator"].withdraw_alias(candidate.hospital_id, 1, "Obsolete Branch Label")
    fx.revocations.add(("alias", subject))
    assert search.search({"query": "Obsolete Branch"}).status == "no_matches"
    assert search.search({"query": "Current Annex Label"}).suggestions[0].id == candidate.hospital_id
    assert HospitalSearch(database).search(ORIGIN).hospitals[0].id == candidate.hospital_id


def test_loc04_late_old_review_cannot_publish(database, governed_fixtures):
    fx = governed_fixtures
    first = fx.candidate(aliases=("Former Directory Alias",))
    fx.submit(first)
    current = first.model_copy(update={"revision": 2, "source": first.source.model_copy(update={"version": 2})})
    signed_old = fx.approval(first)
    fx.submit(current)
    fx.services["reviewer"].review(signed_old, evidence=EVIDENCE)
    with pytest.raises(DirectoryDenied, match="stale_revision"):
        fx.services["operator"].publish(first.hospital_id, 1)
    assert_invisible(database)
    fx.services["reviewer"].review(fx.approval(current), evidence=EVIDENCE)
    fx.services["operator"].publish(first.hospital_id, 2)
    assert HospitalSearch(database).search(ORIGIN).hospitals[0].id == current.hospital_id


def test_loc04_source_withdrawal_blocks_later_import(database, governed_fixtures):
    fx = governed_fixtures
    first = fx.candidate()
    fx.publish(first)
    subject = f"{first.source.organization}:{first.source.record}"
    fx.revoke("source", subject)
    second = first.model_copy(update={"revision": 2, "source": first.source.model_copy(update={"version": 2})})
    with pytest.raises(DirectoryDenied, match="source_withdrawn"):
        fx.submit(second)
    assert_invisible(database)


def test_loc04_search_snapshot_during_alias_withdrawal_and_replacement(database, governed_fixtures):
    fx = governed_fixtures
    first = fx.candidate(aliases=("Former Outpost Alias",))
    fx.publish(first)
    replacement = first.model_copy(update={"revision": 2, "name": "Synthetic Updated Hospital",
        "aliases": ("Updated Outpost Alias",), "source": first.source.model_copy(update={"version": 2})})
    fx.submit(replacement)
    fx.services["reviewer"].review(fx.approval(replacement), evidence=EVIDENCE)
    def find(session, query):
        return list(session.execute(MATCHES, {
            "query": query, "city": None, "district": None, "province": None, "threshold": FUZZY_THRESHOLD,
            "limit": 10,
        }).mappings())
    with scoped_session(database.execution_options(isolation_level="REPEATABLE READ"),
                        CredentialVerifier(None), None) as session:
        assert find(session, "Former Outpost Alias")[0]["name"] == first.name
        subject = fx.services["operator"].withdraw_alias(first.hospital_id, 1, first.aliases[0])
        fx.revocations.add(("alias", subject))
        fx.services["operator"].publish(first.hospital_id, 2)
        assert find(session, "Former Outpost Alias")[0]["name"] == first.name
    service = HospitalAutocomplete(database)
    assert service.search({"query": "Former Outpost Alias"}).status == "no_matches"
    assert service.search({"query": "Updated Outpost Alias"}).suggestions[0].name == replacement.name
