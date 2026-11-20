"""Preserve historical signed-chain controls with the pre-governance screening fixture.

New acceptance cases instantiate the real governed MedicationSafetyService directly.
"""
from roghyar import medication_safety
from roghyar.medication_safety import MedicationSafetyService


class HistoricalMedicationService(MedicationSafetyService):
    def _screen_products(self, row, values, policy, now):
        if not set(values.current_medicines) <= self._vocabulary.medicines:
            return "invalid_vocabulary", None
        screened = medication_safety.screen_verified_formulary(row, values, self._vocabulary)
        if screened == "contraindicated":
            return "known_contraindication", screened
        ingredients = {entry.product: set(entry.ingredients) for entry in policy.products}
        products = {row.medicine, *values.current_medicines}
        if len(ingredients) != len(policy.products) or not products <= ingredients.keys():
            return "interaction_coverage_missing", None
        if any(ingredients[row.medicine] & ingredients[other] for other in values.current_medicines):
            return "duplicate_ingredient_risk", None
        if len(values.current_medicines) > 1:
            return "interaction_coverage_missing", None
        return None, screened
