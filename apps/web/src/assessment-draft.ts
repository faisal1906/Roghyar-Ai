export const severities = ['Mild', 'Moderate', 'Severe'] as const;
export type Severity = typeof severities[number];

export const durationPresets = [
  { label: '1 hour', hours: 1 },
  { label: 'About 2 hours', hours: 2 },
  { label: 'Half a day', hours: 12 },
  { label: '1 day', hours: 24 },
  { label: '2 days', hours: 48 },
] as const;

type PresetHours = typeof durationPresets[number]['hours'];
export type DurationDraft =
  | { kind: 'preset'; hours: PresetHours }
  | { kind: 'custom'; days: string }
  | { kind: 'over-year' };

export type NormalizedDuration =
  | { kind: 'hours'; hours: number }
  | { kind: 'over-year' };

export const sexOptions = ['Female', 'Male', 'Intersex'] as const;
type Sex = typeof sexOptions[number];
export const safetyFields = [
  ['conditions', 'Existing medical conditions'],
  ['allergies', 'Known allergies'],
  ['medicines', 'Current medicines'],
] as const;

export interface AssessmentDraft {
  symptoms: string;
  severity: Severity | '';
  duration: DurationDraft | null;
  age: string;
  sex: Sex | '';
  conditions: string;
  allergies: string;
  medicines: string;
}

export const emptyDraft: AssessmentDraft = {
  symptoms: '', severity: '', duration: null, age: '', sex: '',
  conditions: '', allergies: '', medicines: '',
};

export type DraftErrors = Partial<Record<'symptoms' | 'severity' | 'duration' | 'days' | 'age', string>>;

function wholeNumber(value: string, minimum: number, maximum: number) {
  if (!/^[0-9]+$/.test(value)) return null;
  const number = Number(value);
  return number >= minimum && number <= maximum ? number : null;
}

export function normalizeDuration(duration: DurationDraft | null): NormalizedDuration | null {
  if (!duration) return null;
  if (duration.kind === 'over-year') return { kind: 'over-year' };
  if (duration.kind === 'preset') return { kind: 'hours', hours: duration.hours };
  const days = wholeNumber(duration.days, 1, 365);
  return days === null ? null : { kind: 'hours', hours: days * 24 };
}

export function validateDraft(draft: AssessmentDraft) {
  const errors: DraftErrors = {};
  const symptoms = draft.symptoms.trim();
  const duration = normalizeDuration(draft.duration);
  const age = wholeNumber(draft.age, 0, 120);

  if (!/\p{L}/u.test(symptoms)) errors.symptoms = 'Describe your symptoms using words.';
  if (!draft.severity) errors.severity = 'Choose how severe your symptoms feel.';
  if (!duration) {
    if (draft.duration?.kind === 'custom') errors.days = 'Enter a whole number from 1 to 365 days.';
    else errors.duration = 'Choose how long you have had these symptoms.';
  }
  if (age === null) errors.age = 'Enter a whole number from 0 to 120 years.';

  return {
    errors,
    assessment: Object.keys(errors).length === 0 && draft.severity && duration && age !== null
      ? {
          symptoms, severity: draft.severity, duration, age, sex: draft.sex,
          conditions: entries(draft.conditions), allergies: entries(draft.allergies), medicines: entries(draft.medicines),
        }
      : null,
  };
}

function entries(text: string) {
  return text.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
}
