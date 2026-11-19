import { useEffect, useRef, useState } from 'react';
import { reloadAction, type PwaStatus } from './pwa-state';

function useServiceWorker() {
  const [status, setStatus] = useState<PwaStatus>('idle');
  const actions = useRef({ prepare: () => {}, retry: () => {} });

  useEffect(() => {
    if (!import.meta.env.PROD) return;
    if (!('serviceWorker' in navigator)) {
      setStatus('unsupported');
      return;
    }

    const workers = navigator.serviceWorker;
    const events = new AbortController();
    const options = { signal: events.signal };
    let registration: ServiceWorkerRegistration | undefined;
    let previousController = workers.controller;
    let timer: number | undefined;

    function watchInstallation() {
      const installing = registration?.installing;
      if (!installing) return;
      function inspect() {
        if (installing?.state === 'installed' && workers.controller) setStatus('waiting');
        if (installing?.state === 'redundant') setStatus('error');
      }
      installing.addEventListener('statechange', inspect, options);
      inspect();
    }

    workers.addEventListener('controllerchange', () => {
      const controller = workers.controller;
      window.clearTimeout(timer);
      if (previousController && controller !== previousController) setStatus('reload');
      else if (controller) setStatus('idle');
      previousController = controller;
    }, options);

    async function check() {
      try {
        if (!registration || (!registration.active && !registration.installing && !registration.waiting)) {
          const cached = navigator.onLine ? undefined : await workers.getRegistration('/');
          const next = cached ?? await workers.register('/sw.js', { scope: '/', updateViaCache: 'none' });
          if (events.signal.aborted) return;
          registration = next;
          registration.addEventListener('updatefound', watchInstallation, options);
          watchInstallation();
        } else {
          await registration.update();
        }
        if (!events.signal.aborted && registration.waiting) setStatus('waiting');
      } catch {
        if (!events.signal.aborted) setStatus('error');
      }
    }

    actions.current = {
      prepare() {
        const waiting = registration?.waiting;
        if (!waiting) return void check();
        setStatus('activating');
        window.clearTimeout(timer);
        timer = window.setTimeout(() => setStatus('error'), 12000);
        try {
          waiting.postMessage({ type: 'SKIP_WAITING' });
        } catch {
          window.clearTimeout(timer);
          setStatus('error');
        }
      },
      retry() {
        setStatus('idle');
        void check();
      },
    };

    if (document.readyState === 'complete') void check();
    else window.addEventListener('load', check, { ...options, once: true });

    return () => {
      events.abort();
      window.clearTimeout(timer);
    };
  }, []);

  return { status, actions };
}

const statusText: Record<Exclude<PwaStatus, 'idle'>, string> = {
  waiting: 'An app update is available. You can keep working here.',
  activating: 'Preparing the update…',
  reload: 'The app update is ready. Reload this tab when you’re ready.',
  error: 'Offline setup or the app update could not finish. You can keep using this page.',
  unsupported: 'This browser cannot install the offline app.',
};

export function PwaUpdate({ hasDraft }: { hasDraft: boolean }) {
  const { status, actions } = useServiceWorker();
  const [confirming, setConfirming] = useState(false);
  const keepEditing = useRef<HTMLButtonElement>(null);
  const reloadButton = useRef<HTMLButtonElement>(null);
  const announcement = useRef<HTMLParagraphElement>(null);
  const restore = useRef(false);

  useEffect(() => {
    if (confirming) keepEditing.current?.focus();
    else if (restore.current) {
      restore.current = false;
      reloadButton.current?.focus();
    }
  }, [confirming]);

  function requestReload(confirmed: boolean) {
    const action = reloadAction(status, hasDraft, confirmed);
    if (action === 'confirm') setConfirming(true);
    if (action === 'reload') window.location.reload();
  }

  return (
    <div className="container pwa-update" data-pwa-status={status}>
      <p role="status" aria-live="polite" aria-atomic="true" tabIndex={-1} ref={announcement}
        className={status === 'idle' ? 'sr-only' : undefined}>{status === 'idle' ? '' : statusText[status]}</p>
      {status === 'waiting' && <button className="button button-outline" type="button"
        onClick={() => {
          announcement.current?.focus();
          actions.current.prepare();
        }}>Prepare update</button>}
      {status === 'error' && <button className="button button-outline" type="button"
        onClick={() => actions.current.retry()}>Retry offline setup</button>}
      {status === 'reload' && (
        confirming ? (
          <div className="pwa-confirm">
            <p id="reload-draft-warning">Reloading clears the symptom and assessment draft in this tab.</p>
            <div className="voice-actions">
              <button ref={keepEditing} className="button button-outline" type="button" aria-describedby="reload-draft-warning"
                onClick={() => { restore.current = true; setConfirming(false); }}>Keep editing</button>
              <button className="button button-outline" type="button" aria-describedby="reload-draft-warning"
                onClick={() => requestReload(true)}>Reload and clear draft</button>
            </div>
          </div>
        ) : <button ref={reloadButton} className="button button-outline" type="button"
          onClick={() => requestReload(false)}>Reload app</button>
      )}
    </div>
  );
}
