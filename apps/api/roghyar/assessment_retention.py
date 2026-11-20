"""Explicit, synthetic-environment retention operation. No web route or scheduler."""

from dataclasses import dataclass

from sqlalchemy import Engine, text

from .assessment import DraftUnavailable, require_disposable
from .settings import Settings

ELIGIBLE = """
    a.anonymized_at IS NULL AND a.created_at <= now() - interval '90 days'
    AND NOT EXISTS (SELECT FROM roghyar.appointments b WHERE b.assessment_id = a.id)
"""
CHAT_ELIGIBLE = """
    c.patient_id IS NULL AND c.session_id IN (SELECT session_id FROM targets)
    AND NOT EXISTS (
        SELECT FROM roghyar.assessments a WHERE a.session_id=c.session_id
        AND a.anonymized_at IS NULL AND a.id NOT IN (SELECT id FROM targets)
    )
"""


@dataclass(frozen=True)
class RetentionReport:
    assessments: int
    chat_messages: int
    dry_run: bool


def anonymize_assessments(engine: Engine, settings: Settings, *, dry_run: bool = True) -> RetentionReport:
    require_disposable(engine, settings=settings)
    with engine.begin() as connection:
        if connection.scalar(text("SELECT current_user")) != "roghyar_maintenance":
            raise DraftUnavailable("Retention requires the maintenance role")
        if dry_run:
            counts = connection.execute(text(f"""
                WITH targets AS (
                    SELECT a.id, a.session_id FROM roghyar.assessments a WHERE {ELIGIBLE}
                )
                SELECT (SELECT count(*) FROM targets),
                       (SELECT count(*) FROM roghyar.chat_messages c WHERE {CHAT_ELIGIBLE})
            """)).one()
            return RetentionReport(counts[0], counts[1], True)
        # Block appointment writes during selection and scrub. BK-03 must also
        # reject attaching a booking to an already anonymized assessment.
        connection.execute(text("SELECT pg_advisory_xact_lock(71092, 4)"))
        connection.execute(text("LOCK TABLE roghyar.appointments IN SHARE MODE"))
        result = connection.execute(text(f"""
            WITH targets AS MATERIALIZED (
                SELECT a.id, a.session_id FROM roghyar.assessments a WHERE {ELIGIBLE} FOR UPDATE
            ), scrubbed AS (
                UPDATE roghyar.assessments a SET anonymized_at=now(), session_id=NULL,
                    patient_id=NULL, text_ciphertext=NULL, answers_ciphertext=NULL,
                    safety_details_ciphertext=NULL, age=NULL, severity=NULL, duration_hours=NULL,
                    more_than_one_year=false, language=NULL, level=NULL, condition_code=NULL,
                    specialty=NULL, red_flags=NULL
                FROM targets t WHERE a.id=t.id RETURNING a.id
            ), removed_chat AS (
                DELETE FROM roghyar.chat_messages c WHERE {CHAT_ELIGIBLE} RETURNING c.id
            )
            SELECT (SELECT count(*) FROM scrubbed), (SELECT count(*) FROM removed_chat)
        """)).one()
        return RetentionReport(result[0], result[1], False)
