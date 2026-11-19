import type { Dispatch, SetStateAction } from 'react';
import { Icon, type IconName } from './icons';
import { to, type Route } from './routes';
import { VoiceInput } from './voice-input';

const actions = [
  { route: '/assess', title: 'Check symptoms', description: 'Symptom assessment', icon: 'pulse' },
  { route: '/ai-agent', title: 'Open AI Agent', description: 'Record or type a symptom description', icon: 'mic' },
  { route: '/hospitals', title: 'Find hospitals', description: 'Nearby hospital directory', icon: 'hospital' },
  { route: '/doctors', title: 'Find doctors', description: 'Verified clinician directory', icon: 'doctor' },
] satisfies { route: Route; title: string; description: string; icon: IconName }[];

export function Home({ transcript, setTranscript }: {
  transcript: string;
  setTranscript: Dispatch<SetStateAction<string>>;
}) {
  return (
    <>
      <section className="hero" aria-labelledby="page-title">
        <div className="hero-copy">
          <h1 id="page-title" tabIndex={-1}>Your health, <span>understood.</span></h1>
          <p>ROGHYAR AI is being built to help you understand your symptoms and find hospitals and doctors in Khyber Pakhtunkhwa.</p>
          <a className="button button-light" href={to('/assess')}>Start symptom assessment</a>
        </div>
        <VoiceInput transcript={transcript} setTranscript={setTranscript} />
      </section>
      <section className="quick-actions" aria-labelledby="quick-title">
        <h2 id="quick-title">Quick actions</h2>
        <ul className="action-grid">
          {actions.map(({ route, title, description, icon }) => (
            <li key={route}>
              <a className="action-card" href={to(route)}>
                <span className="action-icon"><Icon name={icon} /></span>
                <span className="action-title">{title}</span>
                <span className="action-description">{description}</span>
                {route !== '/ai-agent' && route !== '/assess' && <span className="action-status">Unavailable</span>}
              </a>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
