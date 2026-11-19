import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { getLiveness } from './api';
import './style.css';

function App() {
  const [status, setStatus] = useState('Checking service connection…');

  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 4000);
    async function checkConnection() {
      try {
        await getLiveness(controller.signal);
        setStatus('API reachable');
      } catch {
        setStatus('API unavailable');
      } finally {
        window.clearTimeout(timeout);
      }
    }
    void checkConnection();
    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, []);

  return (
    <main>
      <h1>ROGHYAR AI</h1>
      <p>Health guidance and appointment booking are currently unavailable.</p>
      <a href="tel:1122">Call Rescue 1122</a>
      <p role="status">{status}</p>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
