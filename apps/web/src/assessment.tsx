import { useEffect, useRef, useState, type Dispatch, type FormEvent, type SetStateAction } from 'react';
import {
  durationPresets, emptyDraft, safetyFields, severities, sexOptions, validateDraft,
  type AssessmentDraft, type DraftErrors, type DurationDraft,
} from './assessment-draft';
import { ChoiceChips } from './choice-chips';
import { EmergencyHelpButton } from './emergency-alert';
import { VoiceInput } from './voice-input';

const durationOptions = [
  ...durationPresets.map(preset => ({ value: String(preset.hours), label: preset.label })),
  { value: 'custom', label: 'Custom days' },
  { value: 'over-year', label: 'More than a year' },
];
const severityOptions = severities.map(value => ({ label: value, value }));

function DurationField({ duration, onChange, errors }: {
  duration: DurationDraft | null;
  onChange: (duration: DurationDraft) => void;
  errors: DraftErrors;
}) {
  const value = duration?.kind === 'preset' ? String(duration.hours) : duration?.kind ?? '';
  const days = duration?.kind === 'custom' ? duration.days : null;
  function select(choice: string) {
    if (choice === 'custom') onChange({ kind: 'custom', days: '' });
    else if (choice === 'over-year') onChange({ kind: 'over-year' });
    else {
      const preset = durationPresets.find(item => String(item.hours) === choice);
      if (preset) onChange({ kind: 'preset', hours: preset.hours });
    }
  }

  return (
    <div className="assessment-field">
      <ChoiceChips legend="Duration" options={durationOptions} value={value} onChange={select}
        error={errors.duration} required />
      {days !== null && <div className="assessment-field custom-days">
        <label htmlFor="assessment-days">Number of days <span className="field-note">Required</span></label>
        <div className="day-stepper">
          <button className="button button-outline" aria-label="Decrease days" type="button"
            disabled={Number(days) <= 1} onClick={() => onChange({ kind: 'custom',
              days: String(Math.min(365, Math.max(1, Math.ceil(Number(days) || 1) - 1))) })}>−</button>
          <input id="assessment-days" type="number" inputMode="numeric" min={1} max={365} step={1} required
            value={days} onChange={event => onChange({ kind: 'custom', days: event.target.value })}
            aria-invalid={errors.days ? true : undefined}
            aria-describedby={errors.days ? 'days-help days-error' : 'days-help'} />
          <button className="button button-outline" aria-label="Increase days" type="button"
            disabled={Number(days) >= 365} onClick={() => onChange({ kind: 'custom',
              days: String(Math.min(365, Math.max(1, Math.floor(Number(days)) + 1))) })}>+</button>
        </div>
        <p id="days-help" className="voice-help">Enter a whole number from 1 to 365.</p>
        {errors.days && <p id="days-error" className="field-error">{errors.days}</p>}
      </div>}
    </div>
  );
}

type ValidAssessment = NonNullable<ReturnType<typeof validateDraft>['assessment']>;

function AssessmentReview({ assessment, duration, onEdit, onClear }: {
  assessment: ValidAssessment;
  duration: AssessmentDraft['duration'];
  onEdit: () => void;
  onClear: () => void;
}) {
  let durationLabel = 'More than a year';
  if (assessment.duration.kind === 'hours') {
    const { hours } = assessment.duration;
    durationLabel = duration?.kind === 'custom'
      ? `${hours / 24} ${hours === 24 ? 'day' : 'days'} (${hours} hours)`
      : durationPresets.find(preset => preset.hours === hours)?.label ?? `${hours} hours`;
  }

  return (
    <section className="assessment-card" aria-labelledby="assessment-review">
      <h2 id="assessment-review" tabIndex={-1}>Review your assessment</h2>
      <p className="assessment-notice">Clinical analysis is currently unavailable. Your assessment has not been sent.</p>
      <dl className="assessment-summary">
        <div className="summary-symptoms"><dt>Symptom description</dt><dd dir="auto">{assessment.symptoms}</dd></div>
        <div><dt>Severity</dt><dd>{assessment.severity}</dd></div>
        <div><dt>Duration</dt><dd>{durationLabel}</dd></div>
        <div><dt>Age in years</dt><dd>{assessment.age}</dd></div>
        <div><dt>Sex</dt><dd>{assessment.sex || 'Not provided'}</dd></div>
        {safetyFields.map(([name, label]) => (
          <div key={name}>
            <dt>{label}</dt>
            <dd>{assessment[name].length ? <ul>{assessment[name].map((entry, index) =>
              <li dir="auto" key={index}>{entry}</li>)}</ul> : 'Not provided'}</dd>
          </div>
        ))}
      </dl>
      <div className="voice-actions">
        <button className="button assessment-primary" type="button" onClick={onEdit}>Edit assessment</button>
        <button className="button button-outline" type="button" onClick={onClear}>Clear assessment</button>
      </div>
    </section>
  );
}

export function Assessment({ draft, setDraft, setTranscript, onEmergencyHelp }: {
  draft: AssessmentDraft;
  setDraft: Dispatch<SetStateAction<AssessmentDraft>>;
  setTranscript: Dispatch<SetStateAction<string>>;
  onEmergencyHelp: (opener: HTMLButtonElement) => void;
}) {
  const [attempts, setAttempts] = useState(0);
  const [reviewing, setReviewing] = useState(false);
  const [voiceReset, setVoiceReset] = useState(0);
  const form = useRef<HTMLFormElement>(null);
  const section = useRef<HTMLElement>(null);
  const focusAfterRender = useRef<'review' | 'form' | 'error' | undefined>(undefined);
  const { errors: validationErrors, assessment } = validateDraft(draft);
  const errors = attempts ? validationErrors : {};
  const hasData = Object.values(draft).some(Boolean);

  useEffect(() => {
    if (focusAfterRender.current === 'error') {
      form.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
    } else if (focusAfterRender.current === 'review') {
      section.current?.querySelector<HTMLElement>('#assessment-review')?.focus();
    } else if (focusAfterRender.current === 'form') {
      section.current?.querySelector<HTMLElement>('#page-title')?.focus();
    }
    focusAfterRender.current = undefined;
  }, [attempts, reviewing, voiceReset]);

  function change<Key extends keyof AssessmentDraft>(key: Key, value: AssessmentDraft[Key]) {
    setDraft(previous => ({ ...previous, [key]: value }));
  }

  function review(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    focusAfterRender.current = assessment ? 'review' : 'error';
    setAttempts(count => count + 1);
    setReviewing(Boolean(assessment));
  }

  function clear() {
    if (!hasData || !window.confirm('Clear your symptom description and all assessment details?')) return;
    focusAfterRender.current = 'form';
    setDraft(emptyDraft);
    setAttempts(0);
    setReviewing(false);
    setVoiceReset(count => count + 1);
  }

  return (
    <section className="assessment-page" aria-labelledby="page-title" ref={section}>
      <header className="assessment-intro">
        <h1 id="page-title" tabIndex={-1}>Symptom assessment</h1>
        <p>Describe how you feel and review your answers. Your draft stays in this tab until you clear it or reload.</p>
        <EmergencyHelpButton onOpen={onEmergencyHelp} />
      </header>
      {reviewing && assessment ? (
        <AssessmentReview assessment={assessment} duration={draft.duration} onClear={clear} onEdit={() => {
          focusAfterRender.current = 'form';
          setReviewing(false);
        }} />
      ) : (
        <form className="assessment-form" ref={form} onSubmit={review} noValidate autoComplete="off">
          <VoiceInput key={voiceReset} transcript={draft.symptoms} setTranscript={setTranscript}
            descriptionRequired descriptionError={errors.symptoms} />
          <section className="assessment-card" aria-labelledby="assessment-details">
            <h2 id="assessment-details">Symptom details</h2>
            <ChoiceChips legend="Severity" options={severityOptions} value={draft.severity}
              onChange={value => change('severity', value)} error={errors.severity} required />
            <DurationField duration={draft.duration} onChange={duration => change('duration', duration)} errors={errors} />
            <div className="assessment-field age-field">
              <label htmlFor="assessment-age">Age in years <span className="field-note">Required</span></label>
              <input id="assessment-age" type="number" inputMode="numeric" min={0} max={120} step={1} required
                value={draft.age} onChange={event => change('age', event.target.value)}
                aria-invalid={errors.age ? true : undefined} aria-describedby={errors.age ? 'age-error' : undefined} />
              {errors.age && <p id="age-error" className="field-error">{errors.age}</p>}
            </div>
          </section>
          <section className="assessment-card" aria-labelledby="safety-details">
            <h2 id="safety-details">Optional safety information</h2>
            <div className="assessment-field">
              <label htmlFor="assessment-sex">Sex <span className="field-note">Optional</span></label>
              <select id="assessment-sex" value={draft.sex}
                onChange={event => change('sex', sexOptions.find(value => value === event.target.value) ?? '')}>
                <option value="">Not provided</option>
                {sexOptions.map(value => <option key={value}>{value}</option>)}
              </select>
            </div>
            <p className="voice-help" id="safety-entry-help">Enter one item per line. Edit or delete a line to change an entry.</p>
            <div className="safety-fields">
              {safetyFields.map(([name, label]) => (
                <div className="assessment-field" key={name}>
                  <label htmlFor={name}>{label} <span className="field-note">Optional</span></label>
                  <textarea id={name} dir="auto" rows={2} value={draft[name]} spellCheck={false} autoCorrect="off"
                    aria-describedby="safety-entry-help" onChange={event => change(name, event.target.value)} />
                </div>
              ))}
            </div>
          </section>
          <div className="voice-actions">
            <button className="button assessment-primary" type="submit">Review assessment</button>
            <button className="button button-outline" type="button" onClick={clear} disabled={!hasData}>Clear assessment</button>
          </div>
        </form>
      )}
    </section>
  );
}
