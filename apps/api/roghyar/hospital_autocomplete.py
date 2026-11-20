"""Private hospital autocomplete over the verified directory search scope."""

from collections import Counter
from datetime import datetime
from itertools import groupby
from typing import Annotated, Literal
from unicodedata import category, normalize
from uuid import UUID

from pydantic import Field, ValidationError, field_validator
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from .hospital_search import HAS_DIRECTORY, SearchModel
from .scoped_session import AuthorizationDenied, CredentialVerifier, scoped_session

FUZZY_THRESHOLD = 0.45
MIN_QUERY = 3
SearchText = Annotated[str, Field(min_length=1, max_length=120)]


def normalize_search(value: str) -> str:
    if any(category(char).startswith("C") or char in "%_\\" for char in value):
        raise ValueError("Unsupported search character")
    value = " ".join(normalize("NFC", value).split())
    if not any(char.isalnum() for char in value):
        raise ValueError("Search text requires letters or numbers")
    # Keep genuine script marks, while bounding abusive combining sequences and repeated terms.
    marks = sum(category(char).startswith("M") for char in value)
    groups = (sum(1 for _ in run) for _, run in groupby(value.casefold()))
    repeated = Counter(value.casefold().split())
    if marks > 24 or max(groups) > 8 or max(repeated.values()) > 6:
        raise ValueError("Excessive search repetition")
    return value


class HospitalAutocompleteQuery(SearchModel):
    query: SearchText = Field(repr=False)
    city: SearchText | None = Field(default=None, repr=False)
    district: SearchText | None = Field(default=None, repr=False)
    province: SearchText | None = Field(default=None, repr=False)
    limit: Annotated[int, Field(ge=1, le=25)] = 10

    @field_validator("query", "city", "district", "province")
    @classmethod
    def normalize_text(cls, value):
        return normalize_search(value) if value is not None else None


class Suggestion(SearchModel):
    id: UUID
    name: str
    city: str
    district: str
    province: str
    verified_on: datetime
    match: Literal["exact_name", "exact_alias", "name_prefix", "alias_prefix", "fuzzy_name"]


class AutocompleteResult(SearchModel):
    status: Literal["found", "no_matches", "directory_unavailable", "invalid_query", "query_too_short",
                    "database_unavailable", "query_timeout", "untrusted_connection"]
    suggestions: tuple[Suggestion, ...] = ()


# The predicates stay explicit in each independently planned branch.
MATCHES = text("""
    WITH params AS NOT MATERIALIZED (
        SELECT roghyar.hospital_search_key(:query) AS q,
               roghyar.hospital_search_key(:city) AS city,
               roghyar.hospital_search_key(:district) AS district,
               roghyar.hospital_search_key(:province) AS province
    ), eligible AS NOT MATERIALIZED (
        SELECT h.id,h.name,h.city,h.district,h.province,h.verified_on,h.aliases,
               roghyar.hospital_search_key(h.name) AS key, p.q
        FROM roghyar.hospitals h JOIN roghyar.directory_current c ON c.hospital_id=h.id
            AND c.expires_at > statement_timestamp() CROSS JOIN params p
        WHERE h.verified AND h.published AND h.verified_on IS NOT NULL
          AND (p.city IS NULL OR roghyar.hospital_search_key(h.city) = p.city)
          AND (p.district IS NULL OR roghyar.hospital_search_key(h.district) = p.district)
          AND (p.province IS NULL OR roghyar.hospital_search_key(h.province) = p.province)
    ), matches AS (
        SELECT id, name, city, district, province, verified_on,
               CASE WHEN key = q THEN 0 ELSE 2 END AS rank, 0::real AS score
        FROM eligible WHERE key LIKE q || '%'
        UNION ALL
        SELECT e.id, e.name, e.city, e.district, e.province, e.verified_on, a.rank, 0::real
        FROM eligible e CROSS JOIN LATERAL (
            SELECT min(CASE WHEN k = e.q THEN 1 ELSE 3 END) AS rank
            FROM unnest(e.aliases) AS alias(value)
            CROSS JOIN LATERAL (SELECT roghyar.hospital_search_key(alias.value) AS k) key
            WHERE k LIKE e.q || '%'
        ) a WHERE a.rank IS NOT NULL
        UNION ALL
        SELECT id, name, city, district, province, verified_on, 4, public.similarity(key, q)
        FROM eligible
        WHERE key OPERATOR(public.%) q AND NOT key LIKE q || '%'
          AND public.similarity(key, q) >= :threshold
    ), deduplicated AS (
        SELECT DISTINCT ON (id) id,name,city,district,province,verified_on,rank,score
        FROM matches ORDER BY id,rank,score DESC
    )
    SELECT id,name,city,district,province,verified_on,
           (ARRAY['exact_name','exact_alias','name_prefix','alias_prefix','fuzzy_name'])[rank + 1] AS match
    FROM deduplicated
    ORDER BY rank, score DESC, id
    LIMIT :limit
""")


class HospitalAutocomplete:
    def __init__(self, runtime_engine: Engine):
        if not runtime_engine.hide_parameters or runtime_engine.echo:
            raise ValueError("Autocomplete requires an engine with hidden parameters and SQL echo disabled")
        self._engine = runtime_engine.execution_options(isolation_level="REPEATABLE READ")

    def search(self, query: object) -> AutocompleteResult:
        try:
            query = HospitalAutocompleteQuery.model_validate(
                query.model_dump() if isinstance(query, HospitalAutocompleteQuery) else query)
        except ValidationError:
            return AutocompleteResult(status="invalid_query")
        if sum(char.isalnum() for char in query.query) < MIN_QUERY:
            return AutocompleteResult(status="query_too_short")
        try:
            with scoped_session(self._engine, CredentialVerifier(None), None) as session:
                session.execute(text("SET LOCAL statement_timeout = '1500ms'"))
                session.execute(text("SELECT set_config('pg_trgm.similarity_threshold', :value, true)"),
                                {"value": str(FUZZY_THRESHOLD)})
                rows = session.execute(MATCHES, query.model_dump() | {"threshold": FUZZY_THRESHOLD}).mappings().all()
                if rows:
                    return AutocompleteResult(status="found", suggestions=tuple(
                        Suggestion.model_validate(dict(row)) for row in rows))
                return AutocompleteResult(status="no_matches" if session.scalar(HAS_DIRECTORY) else "directory_unavailable")
        except AuthorizationDenied:
            return AutocompleteResult(status="untrusted_connection")
        except DBAPIError as exc:
            reason = "query_timeout" if getattr(exc.orig, "sqlstate", None) == "57014" else "database_unavailable"
            return AutocompleteResult(status=reason)
        except (SQLAlchemyError, ValidationError):
            return AutocompleteResult(status="database_unavailable")
