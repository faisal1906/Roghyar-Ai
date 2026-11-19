import { useEffect, useState } from 'react';
import { getLiveness } from './api';

export type Connection = 'checking' | 'reachable' | 'unavailable' | 'offline';

export function useConnection() {
  const [status, setStatus] = useState<Connection>(() => navigator.onLine ? 'checking' : 'offline');
  useEffect(() => {
    let request: AbortController | undefined;
    async function check() {
      request?.abort();
      request = undefined;
      if (!navigator.onLine) {
        setStatus('offline');
        return;
      }
      const controller = new AbortController();
      request = controller;
      setStatus('checking');
      const timeout = window.setTimeout(() => controller.abort(), 4000);
      try {
        await getLiveness(controller.signal);
        if (request === controller) setStatus('reachable');
      } catch {
        if (request === controller) setStatus(navigator.onLine ? 'unavailable' : 'offline');
      } finally {
        window.clearTimeout(timeout);
      }
    }
    void check();
    window.addEventListener('online', check);
    window.addEventListener('offline', check);
    return () => {
      window.removeEventListener('online', check);
      window.removeEventListener('offline', check);
      request?.abort();
      request = undefined;
    };
  }, []);
  return status;
}
