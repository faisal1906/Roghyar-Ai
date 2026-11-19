import { Icon, type IconName } from './icons';
import { to, type Route } from './routes';

const actions = [
  { route: '/assess', title: 'Check symptoms', description: 'Symptom assessment', icon: 'pulse' },
  { route: '/ai-agent', title: 'Contact the AI Agent', description: 'Voice and text assistance', icon: 'mic' },
  { route: '/hospitals', title: 'Find hospitals', description: 'Nearby hospital directory', icon: 'hospital' },
  { route: '/doctors', title: 'Find doctors', description: 'Verified clinician directory', icon: 'doctor' },
] satisfies { route: Route; title: string; description: string; icon: IconName }[];

export function Home() {
  return (
    <>
      <section className="hero" aria-labelledby="page-title">
        <div className="hero-copy">
          <h1 id="page-title" tabIndex={-1}>Your health, <span>understood.</span></h1>
          <p>ROGHYAR AI is being built to help you understand your symptoms and find hospitals and doctors in Khyber Pakhtunkhwa.</p>
          <a className="button button-light" href={to('/assess')}>View assessment availability</a>
        </div>
        <section className="agent-card" aria-labelledby="agent-title">
          <div className="agent-visual" aria-hidden="true">
            <div className="orb-ring"><div className="orb"><Icon name="mic" /></div></div>
          </div>
          <div className="agent-copy">
            <h2 id="agent-title">AI Agent</h2>
            <p>Voice and text assistance is currently unavailable.</p>
            <a className="button button-outline" href={to('/ai-agent')}>View AI Agent status</a>
          </div>
        </section>
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
                <span className="action-status">Unavailable</span>
              </a>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
