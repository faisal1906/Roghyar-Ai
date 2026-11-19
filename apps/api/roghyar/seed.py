"""Print a local fixture manifest. Phase 0 has no patient or directory tables."""

from typing import Literal

from pydantic import BaseModel

from .settings import Settings


class SeedManifest(BaseModel):
    label: Literal["SYNTHETIC—DEVELOPMENT ONLY"] = "SYNTHETIC—DEVELOPMENT ONLY"
    scenario: Literal["empty_database"] = "empty_database"


def build_seed_manifest(settings: Settings) -> SeedManifest:
    if settings.environment != "local" or not settings.synthetic_fixtures:
        raise ValueError("Synthetic fixtures require local opt-in")
    return SeedManifest()


if __name__ == "__main__":
    try:
        print(build_seed_manifest(Settings()).model_dump_json())
    except ValueError as error:
        raise SystemExit(str(error)) from None
