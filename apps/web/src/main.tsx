import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { useConnection } from './connection';
import { Home } from './home';
import { Icon } from './icons';
import { readRoute, to, unavailable, type Route } from './routes';
import { EmergencyNotice, Footer, Header } from './shell';
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
  const [transcript, setTranscript] = useState('');
  const main = useRef<HTMLElement>(null);
  const connection = useConnection();

  useEffect(() => {
    const onHashChange = () => setRoute(readRoute());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  const previousRoute = useRef(route);
  useEffect(() => {
    const title = route === '/' ? '' : route === '/ai-agent' ? 'AI Agent' : route ? unavailable[route][0] : 'Page unavailable';
    document.title = title ? title + ' | ROGHYAR AI' : 'ROGHYAR AI';
    if (previousRoute.current !== route) {
      previousRoute.current = route;
      main.current?.querySelector('h1')?.focus({ preventScroll: true });
      window.scrollTo(0, 0);
    }
  }, [route]);

  return (
    <>
      <a className="skip-link" href="#main-content" onClick={(event) => {
        event.preventDefault();
        main.current?.focus();
      }}>Skip to main content</a>
      <Header route={route} />
      <main className="container content" id="main-content" tabIndex={-1} ref={main}>
        {route === '/' ? <Home transcript={transcript} setTranscript={setTranscript} /> :
          route === '/ai-agent' ? (
            <section className="agent-page" aria-labelledby="page-title">
              <h1 id="page-title" tabIndex={-1}>AI Agent</h1>
              <VoiceInput transcript={transcript} setTranscript={setTranscript} />
              <a className="button button-outline" href={to('/')}>Back to home</a>
            </section>
          ) : <UnavailableView route={route} />}
        <EmergencyNotice />
      </main>
      <Footer connection={connection} />
    </>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
