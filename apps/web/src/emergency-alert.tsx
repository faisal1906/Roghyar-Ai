import { createContext, useLayoutEffect, useRef, type KeyboardEvent } from 'react';
import { emergencyHospitalMessage, emergencyWarningSigns, immediateActions } from './emergency';
import { Icon } from './icons';

export const EmergencyOpen = createContext(false);

export function EmergencyHelpButton({ onOpen, className }: {
  onOpen: (opener: HTMLButtonElement) => void;
  className?: string;
}) {
  return (
    <button className={className ?? 'button emergency-help'} type="button" aria-haspopup="dialog"
      onClick={event => onOpen(event.currentTarget)}>Emergency help</button>
  );
}

export function EmergencyAlert({ offline, onAcknowledge }: {
  offline: boolean;
  onAcknowledge: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const primary = useRef<HTMLAnchorElement>(null);
  const last = useRef<HTMLButtonElement>(null);

  useLayoutEffect(() => {
    const modal = dialog.current;
    modal?.showModal();
    primary.current?.focus({ preventScroll: true });
    primary.current?.scrollIntoView({ block: 'nearest' });
    return () => modal?.close();
  }, []);

  function wrapFocus(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key !== 'Tab') return;
    if (event.shiftKey && event.target === primary.current) {
      event.preventDefault();
      last.current?.focus();
    } else if (!event.shiftKey && event.target === last.current) {
      event.preventDefault();
      primary.current?.focus();
    }
  }

  return (
    <dialog className="emergency-dialog" aria-labelledby="emergency-alert-title"
      aria-describedby="emergency-alert-intro" aria-modal="true" ref={dialog}
      onCancel={event => event.preventDefault()} onKeyDown={wrapFocus}>
      <div className="emergency-dialog-content">
        <header className="emergency-dialog-header">
          <span className="emergency-alert-icon"><Icon name="alert" /></span>
          <div>
            <h1 id="emergency-alert-title">Possible emergency — act now</h1>
            <p id="emergency-alert-intro">If someone may be in immediate danger, call Rescue 1122. You opened general emergency help.</p>
          </div>
        </header>

        <a ref={primary} className="emergency-call" href="tel:1122"><Icon name="phone" />CALL 1122 NOW</a>

        <section className="emergency-panel" aria-labelledby="emergency-action-title">
          <h2 id="emergency-action-title">What to do now</h2>
          <ul>{immediateActions.map(action => <li key={action}>{action}</li>)}</ul>
        </section>

        <section className="emergency-panel" aria-labelledby="emergency-signs-title">
          <h2 id="emergency-signs-title">Emergency warning signs</h2>
          <ul>{emergencyWarningSigns.map(sign => <li key={sign}>{sign}</li>)}</ul>
        </section>

        <section className="emergency-panel" aria-labelledby="emergency-hospital-title">
          <h2 id="emergency-hospital-title">Nearby emergency care</h2>
          <p>{emergencyHospitalMessage(offline)}</p>
        </section>

        <div className="emergency-dialog-actions">
          <a className="emergency-call" href="tel:1122"><Icon name="phone" />CALL 1122 NOW</a>
          <button className="emergency-acknowledge" ref={last} type="button" onClick={() => {
            dialog.current?.close();
            onAcknowledge();
          }}>Acknowledge and close</button>
        </div>
      </div>
    </dialog>
  );
}
