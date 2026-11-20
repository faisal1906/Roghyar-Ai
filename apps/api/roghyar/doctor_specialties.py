"""Injected reviewed specialty taxonomy. No implicit clinical equivalences."""
from datetime import datetime
from typing import Annotated

from pydantic import AwareDatetime, Field, model_validator

from .directory_governance import Key, Label
from .hospital_search import SearchModel


class Specialty(SearchModel):
    key: Key
    display: Label
    aliases: Annotated[tuple[Key, ...], Field(max_length=12)] = ()


class SpecialtyVocabulary(SearchModel):
    version: Annotated[int, Field(gt=0)]
    source_ref: Key
    reviewer_ref: Key
    expires_at: AwareDatetime
    entries: Annotated[tuple[Specialty, ...], Field(min_length=1, max_length=64)]
    revoked: bool = False

    @model_validator(mode="after")
    def unique_mapping(self):
        keys = [key for entry in self.entries for key in (entry.key, *entry.aliases)]
        if len(keys) != len(set(keys)):
            raise ValueError("Ambiguous specialty vocabulary")
        return self

    def active(self, now: datetime):
        return not self.revoked and now < self.expires_at

    def resolve(self, token: str):
        return next((entry.key for entry in self.entries if token in (entry.key, *entry.aliases)), None)
