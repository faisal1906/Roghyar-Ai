"""Read a routing destination from the current governed provider snapshot."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from .directory_governance import fingerprint
from .doctor_search import ELIGIBLE
from .doctor_specialties import SpecialtyVocabulary
from .hospital_search import SearchModel
from .routing_provider import Point
from .scoped_session import AuthorizationDenied, CredentialVerifier, scoped_session


class Destination(SearchModel):
    hospital_id: UUID
    revision: int
    name: str
    address: str
    point: Point
    verified_on: datetime
    expires_at: datetime
    doctor_id: UUID | None = None


class DestinationDenied(ValueError):
    pass


HOSPITAL = text("""
    SELECT h.id AS hospital_id,c.revision,h.name,h.address,h.verified_on,c.expires_at,
           ST_X(h.location::geometry) AS longitude,ST_Y(h.location::geometry) AS latitude
    FROM roghyar.hospitals h JOIN roghyar.directory_current c ON c.hospital_id=h.id
    WHERE h.id=:hospital AND h.verified AND h.published AND h.verified_on IS NOT NULL AND c.expires_at>:now
""")
AFFILIATION = text("SELECT least(c.expires_at,hc.expires_at) " + ELIGIBLE +
                   " AND c.doctor_id=:doctor AND c.hospital_id=:hospital")


class DestinationReader:
    def __init__(self, engine: Engine, *, vocabulary: SpecialtyVocabulary | None = None):
        if engine.echo or not engine.hide_parameters:
            raise ValueError("Unsafe routing database configuration")
        self.engine = engine.execution_options(isolation_level="REPEATABLE READ")
        self.vocabulary = vocabulary

    def resolve(self, hospital: UUID, revision: int, doctor: UUID | None, now: datetime):
        try:
            with scoped_session(self.engine, CredentialVerifier(None), None) as session:
                session.execute(text("SET LOCAL statement_timeout = '1500ms'"))
                row = session.execute(HOSPITAL, {"hospital": hospital, "now": now}).mappings().one_or_none()
                if row is None:
                    raise DestinationDenied("destination_unverified")
                values = dict(row)
                if values["revision"] != revision:
                    raise DestinationDenied("destination_changed")
                if doctor is not None:
                    vocabulary = self.vocabulary
                    if vocabulary is None or not vocabulary.active(now):
                        raise DestinationDenied("affiliation_unavailable")
                    expires = session.scalar(AFFILIATION, {"doctor": doctor, "hospital": hospital, "now": now,
                        "version": vocabulary.version, "digest": fingerprint(vocabulary.model_dump(mode="json"))})
                    if expires is None:
                        raise DestinationDenied("affiliation_unavailable")
                    values["expires_at"] = expires
                return Destination(**values | {"point": Point(latitude=values.pop("latitude"),
                    longitude=values.pop("longitude")), "doctor_id": doctor})
        except (AuthorizationDenied, SQLAlchemyError):
            raise DestinationDenied("destination_database_unavailable") from None
