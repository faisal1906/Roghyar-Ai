import { useCallback, useEffect, useRef, useState, type SetStateAction } from 'react';
import { createRoot } from 'react-dom/client';
import { Assessment } from './assessment';
import { emptyDraft, type AssessmentDraft } from './assessment-draft';
import { useConnection } from './connection';
import { EmergencyAlert, EmergencyOpen } from './emergency-alert';
import { activateEmergency, type EmergencyState } from './emergency';
import { Home } from './home';
import { Hospitals } from './hospitals';
import { Icon } from './icons';
import { PwaUpdate } from './pwa-update';
import { readRoute, to, unavailable, type Route } from './routes';
import { AssistantShortcut, EmergencyNotice, Footer, Header } from './shell';
import { VoiceInput } from './voice-input';
import './style.css';

function UnavailableView({ route }: { route: keyof typeof unavailable | null }) {
  const [title, message] = route ? unavailable[route] : [
    'Page unavailable', 'This address does not lead to a ROGHYAR AI page.',
  ];
  return (
    <section className="unavailable-view" aria-labelledby="page-title">
      <span className="action-icon"><Icon name="info" /></span>
      <h1 id="page-title" tabIndex={-1}>{title}</h1>
      <p>{message}</p>
      <a className="button button-outline" href={to('/')}>Back to home</a>
    </section>
  );
}

function App() {
  const [route, setRoute] = useState(readRoute);
  const [draft, setDraft] = useState<AssessmentDraft>(emptyDraft);
  const [emergency, setEmergency] = useState<EmergencyState>(null);
  const emergencyOpener = useRef<HTMLButtonElement>(null);
  const setTranscript = useCallback((value: SetStateAction<string>) => {
    setDraft(previous => ({ ...previous, symptoms: typeof value === 'function' ? value(previous.symptoms) : value }));
  }, []);
  const main = useRef<HTMLElement>(null);
  const connection = useConnection();

  const openEmergency = useCallback((opener: HTMLButtonElement) => {
    if (emergencyOpener.current) return;
    emergencyOpener.current = opener;
    setEmergency(current => activateEmergency(current, { source: 'manual' }));
  }, []);

  function acknowledgeEmergency() {
    setEmergency(null);
    const opener = emergencyOpener.current;
    if (opener?.isConnected) opener.focus({ preventScroll: true });
    else main.current?.focus({ preventScroll: true });
    emergencyOpener.current = null;
  }

  useEffect(() => {
    const onHashChange = () => setRoute(readRoute());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  const previousRoute = useRef(route);
  useEffect(() => {
    const title = route === '/' ? '' : route === '/ai-agent' ? 'AI Agent' :
      route === '/assess' ? 'Symptom assessment' : route ? unavailable[route][0] : 'Page unavailable';
    document.title = title ? title + ' | ROGHYAR AI' : 'ROGHYAR AI';
    if (previousRoute.current !== route) {
      previousRoute.current = route;
      if (!emergency) {
        main.current?.querySelector('h1')?.focus({ preventScroll: true });
        window.scrollTo(0, 0);
      }
    }
  }, [route, emergency]);

  return (
    <EmergencyOpen.Provider value={emergency !== null}>
      <a className="skip-link" href="#main-content" onClick={(event) => {
        event.preventDefault();
        main.current?.focus({ preventScroll: true });
        main.current?.scrollIntoView({ block: 'start' });
      }}>Skip to main content</a>
      <Header route={route} onEmergencyHelp={openEmergency} />
      <main className="container content" id="main-content" tabIndex={-1} ref={main}>
        {route === '/' ? <Home transcript={draft.symptoms} setTranscript={setTranscript} /> :
          route === '/assess' ? <Assessment draft={draft} setDraft={setDraft} setTranscript={setTranscript}
            onEmergencyHelp={openEmergency} /> :
          route === '/hospitals' ? <Hospitals /> :
          route === '/ai-agent' ? (
            <section className="agent-page" aria-labelledby="page-title">
              <h1 id="page-title" tabIndex={-1}>AI Agent</h1>
              <VoiceInput transcript={draft.symptoms} setTranscript={setTranscript} />
              <a className="button button-outline" href={to('/')}>Back to home</a>
            </section>
          ) : <UnavailableView route={route} />}
        <EmergencyNotice onEmergencyHelp={openEmergency} />
      </main>
      <PwaUpdate hasDraft={Object.values(draft).some(Boolean)} />
      <Footer connection={connection} />
      {route !== '/ai-agent' && <AssistantShortcut />}
      {emergency && <EmergencyAlert offline={connection === 'offline'} onAcknowledge={acknowledgeEmergency} />}
    </EmergencyOpen.Provider>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
