import { useContext, useEffect, useRef, useState } from 'react';
import { EmergencyOpen } from './emergency-alert';
import { browserLocationEnvironment, createLocationController, type LocationState } from './geolocation';
import { Icon } from './icons';
import { to, unavailable } from './routes';

const messages: Record<LocationState['status'], string> = {
  idle: '',
  checking: 'Checking location permission.',
  permission: 'Waiting for browser permission or a first location fix.',
  locating: 'Looking for your location.',
  available: 'Location obtained on this device.',
  denied: 'Location permission is blocked. You can continue without sharing it.',
  unavailable: 'Your location is currently unavailable.',
  timeout: 'Finding your location timed out.',
  unsupported: 'Automatic location is unavailable in this browser.',
  insecure: 'Automatic location requires a secure connection.',
  revoked: 'Location permission changed. Sharing stopped.',
  stopped: 'Location sharing stopped.',
  stale: 'The location fix has expired. Waiting for a fresh fix.',
};

export function Hospitals() {
  const [state, setState] = useState<LocationState>({ status: 'idle', watching: false });
  const controller = useRef<ReturnType<typeof createLocationController>>(null);
  const emergency = useContext(EmergencyOpen);
  const action = useRef<HTMLButtonElement>(null);
  const stopAction = useRef<HTMLButtonElement>(null);
  const [title, availability] = unavailable['/hospitals'];

  useEffect(() => {
    const location = createLocationController(browserLocationEnvironment(), next => {
      if (!next.watching && document.activeElement === stopAction.current) action.current?.focus();
      setState(next);
    });
    controller.current = location;
    const stop = () => location.stopIfActive();
    const onVisibility = () => { if (document.hidden) stop(); };
    const onRoute = () => { if (window.location.hash !== to('/hospitals')) stop(); };
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('pagehide', stop);
    window.addEventListener('hashchange', onRoute);
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('pagehide', stop);
      window.removeEventListener('hashchange', onRoute);
      location.dispose();
      controller.current = null;
    };
  }, []);

  useEffect(() => {
    if (emergency) controller.current?.stopIfActive();
  }, [emergency]);

  const recoverable = state.status === 'timeout' || state.status === 'unavailable' || state.status === 'stale';
  const blocked = state.status === 'unsupported' || state.status === 'insecure';
  return (
    <section className="unavailable-view" aria-labelledby="page-title">
      <span className="action-icon"><Icon name="info" /></span>
      <h1 id="page-title" tabIndex={-1}>{title}</h1>
      <p>{availability}</p>
      <section className="location-panel" aria-labelledby="location-title">
        <h2 id="location-title">Your location</h2>
        <p>Sharing is optional. Location can help find nearby care once verified listings are available.
          Your coordinates stay in this page’s memory. You can stop sharing or change permission in your browser.
          Sharing stops when you leave or hide this page.</p>
        <p role="status" aria-atomic="true">{messages[state.status]}</p>
        <div className="location-actions">
          <button className="button button-outline" type="button" ref={action}
            onClick={() => {
              if (emergency || document.hidden || blocked || (state.watching && !recoverable)) return;
              if (state.watching) controller.current?.stop();
              void controller.current?.start();
            }} aria-disabled={blocked || (state.watching && !recoverable)}>
            {recoverable || state.status === 'denied' || state.status === 'revoked' ? 'Retry location' : 'Use my location'}
          </button>
          {state.watching && <button className="button button-outline" type="button" ref={stopAction}
            onClick={() => controller.current?.stop()}>Stop sharing location</button>}
        </div>
      </section>
      <p className="directions-unavailable">Directions and travel-time estimates are currently unavailable.</p>
      <a className="button button-outline" href={to('/')}>Back to home</a>
    </section>
  );
}
