import { useEffect, useRef, useState, type FormEvent } from 'react';
import './private-admin.css';

type Section = 'doctors' | 'schedules' | 'appointments' | 'audit';
type RecordRow = Record<string, unknown>;
type FacilityPage = { facility: string; role: string; records: RecordRow[] };
const sections: ReadonlyArray<{ key: Section; label: string }> = [
  { key: 'appointments', label: 'Appointments' }, { key: 'doctors', label: 'Doctors' },
  { key: 'schedules', label: 'Schedules' }, { key: 'audit', label: 'Audit' },
];
const columnLabels: Record<Section, Record<string, string>> = {
  appointments: { id: 'Appointment', doctor_id: 'Doctor', slot_start: 'Start', slot_end: 'End', status: 'Status' },
  doctors: { id: 'Doctor', full_name: 'Name', specialty: 'Specialty', published: 'Published', verified: 'Verified' },
  schedules: { doctor_id: 'Doctor', revision: 'Revision', expires_at: 'Review expiry' },
  audit: { recorded_at: 'Time', actor: 'Actor', action: 'Action', target: 'Target' },
};

function errorMessage(status: number) {
  if (status === 401) return 'Staff sign-in is required.';
  if (status === 403) return 'Your staff role cannot open this section.';
  return 'Administration data is temporarily unavailable.';
}

function valueLabel(key: string, value: unknown) {
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value !== 'string' && typeof value !== 'number') return 'Unavailable';
  if (typeof value === 'string' && ['slot_start', 'slot_end', 'expires_at', 'recorded_at'].includes(key)) {
    const instant = new Date(value);
    if (Number.isFinite(instant.valueOf())) {
      return new Intl.DateTimeFormat('en-PK', {
        dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Karachi',
      }).format(instant);
    }
  }
  return String(value);
}

function parsePage(input: unknown): FacilityPage {
  if (!input || typeof input !== 'object') throw new Error();
  const { facility, role, records } = input as Partial<FacilityPage>;
  if (typeof facility !== 'string' || typeof role !== 'string' || !Array.isArray(records)
      || records.length > 50 || records.some((row) => !row || typeof row !== 'object' || Array.isArray(row))) {
    throw new Error();
  }
  return { facility, role, records };
}

export function PrivateAdmin() {
  const [token, setToken] = useState<string>();
  const [section, setSection] = useState<Section>('appointments');
  const [page, setPage] = useState<FacilityPage>();
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    setLoading(true);
    setPage(undefined);
    setMessage('');
    void (async () => {
      try {
        const response = await fetch('/admin/' + section, {
          cache: 'no-store', credentials: 'omit', signal: controller.signal,
          headers: { Authorization: 'Bearer ' + token },
        });
        if (controller.signal.aborted) return;
        if (!response.ok) {
          if (response.status === 401) setToken(undefined);
          setMessage(errorMessage(response.status));
          return;
        }
        const result = parsePage(await response.json());
        if (!controller.signal.aborted) setPage(result);
      } catch {
        if (!controller.signal.aborted) setMessage(errorMessage(503));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [token, section]);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const proof = new FormData(form).get('proof');
    form.reset();
    setLoading(true);
    setMessage('');
    try {
      const response = await fetch('/admin/session', {
        method: 'POST', cache: 'no-store', credentials: 'omit',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ proof }),
      });
      if (!response.ok) {
        setMessage(errorMessage(response.status));
        return;
      }
      const result: unknown = await response.json();
      const secret = (result as { token?: unknown } | null)?.token;
      if (typeof secret !== 'string' || !/^staff1\.[A-Za-z0-9_-]{43}$/.test(secret)) throw new Error();
      setToken(secret);
      heading.current?.focus();
    } catch {
      setMessage(errorMessage(503));
    } finally {
      setLoading(false);
    }
  }

  async function signOut() {
    const secret = token;
    setToken(undefined);
    setPage(undefined);
    setLoading(false);
    heading.current?.focus();
    setMessage('');
    try {
      const response = await fetch('/admin/session', {
        method: 'DELETE', cache: 'no-store', credentials: 'omit',
        headers: { Authorization: 'Bearer ' + secret },
      });
      if (!response.ok) setMessage('Local sign-out completed. Server revocation could not be confirmed.');
    } catch {
      setMessage('Local sign-out completed. Server revocation could not be confirmed.');
    }
  }

  return (
    <main className="private-admin">
      <header><h1 ref={heading} tabIndex={-1}>Hospital administration</h1>
        {token && <button type="button" onClick={() => void signOut()}>Sign out</button>}
      </header>
      <p role="status">{loading ? 'Loading…' : message}</p>
      {!token ? (
        <form onSubmit={(event) => void signIn(event)}>
          <label htmlFor="staff-proof">Staff identity proof</label>
          <input id="staff-proof" name="proof" type="password" maxLength={4096} required autoComplete="off" />
          <button type="submit" disabled={loading}>Sign in</button>
        </form>
      ) : (
        <>
          <nav aria-label="Administration sections">
            {sections.map(({ key, label }) => (
              <button key={key} type="button" aria-pressed={section === key}
                onClick={() => { if (section !== key) { setPage(undefined); setSection(key); } }}>{label}</button>
            ))}
          </nav>
          {page && (
            <section aria-label={sections.find(({ key }) => key === section)?.label}>
              <p>Facility {page.facility} · {page.role.replaceAll('_', ' ')}</p>
              {page.records.length === 0 ? <p>No records to show.</p> : (
                <ul>
                  {page.records.map((row, index) => (
                    <li key={index}>
                      <dl>{Object.entries(columnLabels[section]).map(([key, label]) => (
                        <div key={key}><dt>{label}</dt><dd>{valueLabel(key, row[key])}</dd></div>
                      ))}</dl>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}
        </>
      )}
    </main>
  );
}
