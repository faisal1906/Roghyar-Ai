import { useId } from 'react';

interface ChoiceChipsProps<Value extends string> {
  legend: string;
  options: readonly { value: Value; label: string }[];
  value: Value | '';
  onChange: (value: Value) => void;
  error?: string;
  required?: boolean;
}

export function ChoiceChips<Value extends string>({
  legend, options, value, onChange, error, required,
}: ChoiceChipsProps<Value>) {
  const id = useId();
  return (
    <fieldset className="choice-field" aria-describedby={error ? id : undefined}>
      <legend>{legend}{required && <span className="field-note">Required</span>}</legend>
      <div className="choice-list">
        {options.map(option => (
          <label className="choice-chip" key={option.value}>
            <input type="radio" name={id} value={option.value} checked={value === option.value}
              required={required} aria-invalid={error ? true : undefined}
              aria-describedby={error ? id : undefined} onChange={() => onChange(option.value)} />
            {option.label}
          </label>
        ))}
      </div>
      {error && <p className="field-error" id={id}>{error}</p>}
    </fieldset>
  );
}
