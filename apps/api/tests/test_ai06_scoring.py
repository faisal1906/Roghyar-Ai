"""Synthetic metric arithmetic checks."""
import pytest

from evaluation.score import Manifest, Predictions, Reference, Prediction, score


def manifest(**overrides):
    base = dict(schema_version=1, dataset_id="synthetic", version="1", tier="synthetic",
                source_ref="fixture", usage_ref="test", licensing="synthetic",
                records=(
                    Reference(id="one", group="ur+en", languages=("en", "ur"), categories=("symptom", "wellness"),
                              annotation_ref="fixture", reviewed=True, negated=True),
                    Reference(id="two", group="ur+en", languages=("en",), categories=("off_topic",),
                              annotation_ref="fixture", reviewed=True),
                ))
    return Manifest(**(base | overrides))


def predictions(*rows):
    return Predictions(schema_version=1, manifest_sha256="a" * 64, model_version="fixture",
                       configuration_ref="fixture", rows=rows)


def test_metric_mutation_and_partial_results():
    one = Prediction(id="one", languages=("en", "ur"), categories=("symptom", "wellness"), refused=False,
                     negated=True, unauthorized=False)
    two = Prediction(id="two", languages=("en",), categories=("off_topic",), refused=True)
    def metrics(*rows):
        return score(manifest(), predictions(*rows))["groups"]["ur+en"]["metrics"]
    correct = metrics(one, two)
    assert correct["languages"]["ur"]["recall"]["value"] == 1
    assert correct["off_topic_refusal"]["value"] == 1
    altered = one.model_copy(update=dict(languages=("en",), categories=("off_topic",), refused=True,
                                         negated=False, unauthorized=True))
    changed = metrics(altered, two)
    assert changed["categories"]["off_topic"]["precision"]["value"] == 0.5
    assert changed["wrongful_refusal"]["value"] == 1
    assert changed["negated_error"]["value"] == 1
    assert changed["unauthorized_output"]["value"] == 1
    assert metrics(one)["off_topic_refusal"] == dict(numerator=0, denominator=0, eligible=1, value=None)
    with pytest.raises(ValueError):
        metrics(one, one)


def test_invalid_approval_and_annotation():
    with pytest.raises(ValueError):
        manifest(tier="representative")
    with pytest.raises(ValueError):
        Reference(id="one", group="unknown", languages=(), categories=(), annotation_ref="fixture", reviewed=False)
    with pytest.raises(ValueError):
        Prediction(id="one", languages=(), categories=(), confidence=float("nan"))
