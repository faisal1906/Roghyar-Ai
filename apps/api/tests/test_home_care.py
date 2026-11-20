"""SYNTHETIC—DEVELOPMENT ONLY. No clinical wording or production keys."""
import hashlib
from dataclasses import replace
from datetime import timedelta
from statistics import median
from time import perf_counter_ns

import pytest

from roghyar.home_care import ContentBinding, HomeCareBody, HomeCareContentRepository, HomeCareSelection, SignedHomeCare
from test_reply_writer import NOW, signed


@pytest.fixture
def home_fixture(signed):
    private, authority, _ = signed
    authority = replace(authority, scopes=frozenset({"home_care"}))
    def create(**updates):
        body = HomeCareBody.model_validate(dict(
            schema_version=1, content_id="synthetic", version=1, level="HOME CARE", category="non_medication",
            condition="synthetic_condition", applicability="synthetic_scope", review_status="approved",
            active=True, reviewer="synthetic", key_id="sample_key", reviewed_at=NOW, source_ref="synthetic_source",
            followup_hours=10, variants=tuple(dict(locale=locale, review_ref="synthetic_review",
            summary=text, steps=("Synthetic step A", "Synthetic step B"), see_doctor_if=("Synthetic warning",))
                for locale, text in (("en", "Synthetic only"), ("ur", "آزمائش"), ("ps", "ازموینه"))),
        ) | updates)
        payload = body.signing_bytes()
        return SignedHomeCare(body=body, digest=hashlib.sha256(payload).hexdigest(),
                              signature=private.sign(payload).hex())
    return private, authority, create


def selection(item, **fields):
    return HomeCareSelection.model_validate(dict(binding=ContentBinding(
        content_id=item.body.content_id, version=item.body.version, digest=item.digest),
        condition=item.body.condition, applicability=item.body.applicability, locale="en", now=NOW) | fields)


@pytest.mark.parametrize("locale", ["en", "ur", "ps"])
def test_exact_variant_and_immutable_order(home_fixture, locale):
    _, authority, create = home_fixture
    item = create()
    repo = HomeCareContentRepository((item,), authorities={"sample_key": authority})
    result = repo.resolve(selection(item, locale=locale))
    expected = next(v for v in item.body.variants if v.locale == locale)
    assert result.status == "verified" and result.variant == expected and result.followup_hours == 10
    with pytest.raises(ValueError):
        item.body.version = 3
    with pytest.raises(ValueError):
        HomeCareContentRepository((item, item))


@pytest.mark.parametrize("updates", [
    {"schema_version": 2}, {"content_id": "../synthetic"}, {"medicine": "forged"},
    {"followup_hours": 0}, {"expires_at": NOW}, {"level": "URGENT"},
    {"variants": (dict(locale="en", review_ref="sample", summary="<script>", steps=("Sample",),
                        see_doctor_if=("Sample",)),)},
    {"variants": (dict(locale="en", review_ref="sample", summary="x" * 601, steps=("Sample",),
                        see_doctor_if=("Sample",)),)},
    {"variants": (dict(locale="en", review_ref="sample", summary="Sample", steps=("Sample",) * 9,
                        see_doctor_if=("Sample",)),)},
    {"variants": (dict(locale="en", review_ref="sample", summary="Sample", steps=("Sample",),
                        see_doctor_if=()),)},
])
def test_invalid_bundle(home_fixture, updates):
    with pytest.raises(ValueError):
        home_fixture[2](**updates)


@pytest.mark.parametrize("field,value", [
    ("summary", "Changed summary"), ("steps", ("Changed step",)),
    ("see_doctor_if", ("Changed warning",)), ("locale", "ps"),
    ("followup_hours", 11), ("condition", "changed"),
])
def test_tampered_bundle(home_fixture, field, value):
    _, authority, create = home_fixture
    item = create()
    body = item.body.model_dump()
    if field in ("summary", "steps", "see_doctor_if", "locale"):
        body["variants"][0][field] = value
        body["variants"] = (body["variants"][0],)
    else:
        body[field] = value
    updated = item.model_copy(update={"body": HomeCareBody.model_validate(body)})
    result = HomeCareContentRepository((updated,), authorities={"sample_key": authority}).resolve(selection(updated))
    assert result.status == "invalid_artifact" and result.variant is None


@pytest.mark.parametrize("mode,status", [
    ("empty", "empty"), ("inactive", "inactive"), ("expired", "expired"),
    ("wrong_scope", "wrong_scope"), ("binding", "wrong_binding"), ("unknown_version", "unknown_version"),
    ("unknown_id", "unknown_content"), ("conflict", "conflicting_active_versions"),
    ("wrong_domain", "invalid_signature"), ("revoked", "revoked_authority"),
    ("expired_key", "expired_authority"), ("product", "invalid_authority"), ("missing_locale", "missing_locale"),
])
def test_repository_denials(home_fixture, mode, status):
    private, authority, create = home_fixture
    item = create(active=mode != "inactive", expires_at=NOW + timedelta(seconds=1) if mode == "expired" else None)
    if mode == "missing_locale":
        item = create(variants=(item.body.variants[0],))
    request = selection(item, locale="ps" if mode == "missing_locale" else "en")
    if mode == "wrong_scope":
        request = selection(item, condition="other")
    if mode in ("binding", "unknown_id", "unknown_version"):
        field, value = {"binding": ("digest", "a" * 64), "unknown_id": ("content_id", "other"),
                        "unknown_version": ("version", 3)}[mode]
        request = selection(item, binding=request.binding.model_copy(update={field: value}))
    if mode == "expired":
        request = selection(item, now=NOW + timedelta(seconds=1))
    if mode == "wrong_domain":
        item = item.model_copy(update={"signature": private.sign(b"wrong" + item.body.signing_bytes()).hex()})
    if mode == "revoked":
        authority = replace(authority, revoked=True)
    if mode == "expired_key":
        authority = replace(authority, valid_until=NOW)
    if mode == "product":
        authority = replace(authority, scopes=frozenset({"product"}))
    rows = () if mode == "empty" else (item, create(version=2)) if mode == "conflict" else (item,)
    result = HomeCareContentRepository(rows, authorities={"sample_key": authority}).resolve(request)
    assert result.status == status and result.variant is None and result.binding is None


def test_history_and_benchmark(home_fixture):
    private, authority, create = home_fixture
    item = create()
    history = create(version=2, active=False)
    repo = HomeCareContentRepository((item, history), authorities={"sample_key": authority})
    request = selection(item)
    assert repo.resolve(request).status == "verified"
    assert repo.resolve(selection(history)).status == "inactive"
    assert HomeCareContentRepository((item, history), authorities={
        "sample_key": replace(authority, revoked=True),
    }).resolve(request).status == "revoked_authority"
    payload = item.body.signing_bytes()
    raw = item.model_dump()
    for name, operation in {
        "schema": lambda: SignedHomeCare.model_validate(raw),
        "digest": lambda: hashlib.sha256(item.body.signing_bytes()).hexdigest(),
        "signature": lambda: private.public_key().verify(bytes.fromhex(item.signature), payload),
        "lookup_and_render": lambda: repo.resolve(request),
        "empty_denial": lambda: empty.resolve(request),
    }.items():
        empty = HomeCareContentRepository()
        samples = []
        for _ in range(205):
            start = perf_counter_ns()
            operation()
            samples.append((perf_counter_ns() - start) / 1e6)
        values = sorted(samples[5:])
        print(f"GD-02 synthetic benchmark {name} n=200 p50={median(values):.4f} ms p95={values[189]:.4f} ms")
