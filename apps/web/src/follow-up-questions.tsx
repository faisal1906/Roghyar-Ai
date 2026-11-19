import { useId } from 'react';
import { ChoiceChips } from './choice-chips';

export interface FollowUpQuestion {
  id: string;
  prompt: string;
  selection: 'single' | 'multiple';
  options: readonly { value: string; label: string }[];
}

export function FollowUpQuestions({ questions, answers, onAnswer }: {
  questions: readonly FollowUpQuestion[];
  answers: Readonly<Record<string, readonly string[]>>;
  onAnswer: (id: string, values: readonly string[]) => void;
}) {
  const title = useId();
  if (!questions.length) return null;
  return (
    <section aria-labelledby={title}>
      <h2 id={title}>Follow-up questions</h2>
      {questions.slice(0, 5).map(question => {
        const selected = answers[question.id] ?? [];
        return question.selection === 'single' ? (
          <ChoiceChips key={question.id} legend={question.prompt}
            options={[{ value: '', label: 'Skip this question' }, ...question.options]}
            value={selected[0] ?? ''} onChange={value => onAnswer(question.id, value ? [value] : [])} />
        ) : (
          <fieldset className="choice-field" key={question.id}>
            <legend>{question.prompt}</legend>
            <div className="choice-list">
              {question.options.map(option => (
                <label className="choice-chip" key={option.value}>
                  <input type="checkbox" checked={selected.includes(option.value)}
                    onChange={event => onAnswer(question.id, event.target.checked
                      ? [...selected, option.value]
                      : selected.filter(value => value !== option.value))} />
                  {option.label}
                </label>
              ))}
            </div>
          </fieldset>
        );
      })}
    </section>
  );
}
