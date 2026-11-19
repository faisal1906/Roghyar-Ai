import pytest

from roghyar.seed import build_seed_manifest
from roghyar.settings import Settings


def test_seed_manifest_is_labeled():
    settings = Settings(_env_file=None, environment="local", synthetic_fixtures=True)
    assert build_seed_manifest(settings).label == "SYNTHETIC—DEVELOPMENT ONLY"


@pytest.mark.parametrize(("environment", "enabled"), [("local", False), ("test", True)])
def test_seed_manifest_requires_local_opt_in(environment, enabled):
    settings = Settings(_env_file=None, environment=environment, synthetic_fixtures=enabled)
    with pytest.raises(ValueError, match="local opt-in"):
        build_seed_manifest(settings)
