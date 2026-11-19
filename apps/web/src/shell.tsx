import { memo, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Icon } from './icons';
import { EmergencyHelpButton } from './emergency-alert';
import { navigation, to, type Route } from './routes';
import type { Connection } from './connection';

export const Header = memo(function Header({ route, onEmergencyHelp }: {
  route: Route | null;
  onEmergencyHelp: (opener: HTMLButtonElement) => void;
}) {
  const [open, setOpen] = useState(false);
  const header = useRef<HTMLElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  useLayoutEffect(() => {
    const element = header.current;
    if (!element) return;
    const update = () => document.documentElement.style.setProperty('--header-height', element.offsetHeight + 'px');
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => {
      observer.disconnect();
      document.documentElement.style.removeProperty('--header-height');
    };
  }, []);
  useEffect(() => {
    const close = () => setOpen(false);
    const desktop = window.matchMedia('(min-width: 64rem)');
    window.addEventListener('hashchange', close);
    desktop.addEventListener('change', close);
    return () => {
      window.removeEventListener('hashchange', close);
      desktop.removeEventListener('change', close);
    };
  }, []);

  return (
    <header className="site-header" ref={header} onKeyDown={(event) => {
      if (event.key === 'Escape' && open) {
        setOpen(false);
        menuButton.current?.focus();
      }
    }}>
      <div className="header-inner container">
        <a className="brand" href={to('/')} onClick={() => setOpen(false)}>
          <span className="brand-mark"><Icon name="brand" /></span>
          <span>ROGHYAR <span className="brand-accent">AI</span></span>
        </a>
        <div className="header-actions">
          <a className="emergency-link" href="tel:1122"><Icon name="phone" /><span>Call Rescue 1122</span></a>
          <button className="menu-toggle" type="button" aria-label={open ? 'Close menu' : 'Open menu'}
            aria-expanded={open} aria-controls="primary-navigation" ref={menuButton}
            onClick={() => setOpen(!open)}>
            <Icon name={open ? 'close' : 'menu'} />
          </button>
        </div>
        <nav id="primary-navigation" className={open ? 'site-nav is-open' : 'site-nav'} aria-label="Main navigation">
          {navigation.map(([path, label]) => (
            <a key={path} href={to(path)} aria-current={route === path ? 'page' : undefined}
              onClick={() => {
                if (open && route === path) menuButton.current?.focus();
                setOpen(false);
              }}>{label}</a>
          ))}
        </nav>
      </div>
      <div className="trust-row">
        <div className="container">
          <span className="trust-copy"><Icon name="shield" />Guidance is not a diagnosis.</span>
          <EmergencyHelpButton onOpen={onEmergencyHelp} className="button emergency-help emergency-help-header" />
        </div>
      </div>
    </header>
  );
});

export function EmergencyNotice({ onEmergencyHelp }: { onEmergencyHelp: (opener: HTMLButtonElement) => void }) {
  return (
    <aside className="emergency-notice" aria-labelledby="emergency-title">
      <span className="emergency-icon"><Icon name="alert" /></span>
      <div>
        <h2 id="emergency-title">When to call for emergency help</h2>
        <p>Call Rescue 1122 for chest pain, severe difficulty breathing, signs of stroke, or severe bleeding.</p>
        <EmergencyHelpButton onOpen={onEmergencyHelp} />
      </div>
    </aside>
  );
}

const connectionText: Record<Connection, string> = {
  checking: 'Checking service connection…',
  reachable: 'Service connected',
  unavailable: 'Service connection unavailable',
  offline: 'You’re offline. You can still call Rescue 1122.',
};

export const Footer = memo(function Footer({ connection }: { connection: Connection }) {
  return (
    <footer className="site-footer">
      <div className="container">
        <span>ROGHYAR AI</span>
        <p role="status" className="connection-status" data-connection={connection}>{connectionText[connection]}</p>
      </div>
    </footer>
  );
});
