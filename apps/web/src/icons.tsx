const shapes = {
  brand: 'M9 3h6v6h6v6h-6v6H9v-6H3V9h6z',
  phone: 'M7 3H4a1 1 0 0 0-1 1c0 9.4 7.6 17 17 17a1 1 0 0 0 1-1v-3l-5-2-2 2a15 15 0 0 1-7-7l2-2z',
  shield: 'M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z M9 12l2 2 4-4',
  alert: 'M12 3 22 21H2z M12 9v4m0 4h.01',
  menu: 'M4 6h16M4 12h16M4 18h16',
  close: 'm6 6 12 12M18 6 6 18',
  mic: 'M9 5a3 3 0 1 1 6 0v7a3 3 0 1 1-6 0z M5 10v2a7 7 0 0 0 14 0v-2m-7 9v3m-3 0h6',
  pulse: 'M2 12h5l3-7 4 14 3-7h5',
  hospital: 'M4 22V5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v17M2 22h20M9 22v-5h6v5M12 7v6m-3-3h6',
  doctor: 'M5 3v5a5 5 0 0 0 10 0V3M3 3h4m6 0h4M10 13v3a5 5 0 0 0 10 0v-1m0 0a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z',
  info: 'M21 12a9 9 0 1 1-18 0 9 9 0 1 1 18 0Z M12 11v5m0-8h.01',
};

export type IconName = keyof typeof shapes;

export function Icon({ name }: { name: IconName }) {
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"><path d={shapes[name]} /></svg>;
}
