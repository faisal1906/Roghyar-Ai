export const FIX_MAX_AGE_MS = 30_000;
const WATCH_OPTIONS: PositionOptions = {
  enableHighAccuracy: false,
  maximumAge: 0,
  timeout: 15_000,
};

export type Fix = Readonly<{
  latitude: number;
  longitude: number;
  accuracy: number;
  timestamp: number;
  freshness: 'current';
}>;

type Status = 'idle' | 'checking' | 'permission' | 'locating' | 'denied' | 'unavailable' |
  'timeout' | 'unsupported' | 'insecure' | 'revoked' | 'stopped' | 'stale';
export type LocationState =
  | { status: Status; watching: boolean }
  | { status: 'available'; watching: true; fix: Fix };

function readFix(position: GeolocationPosition, now: number): Fix | null {
  const { latitude, longitude, accuracy } = position?.coords ?? {};
  const timestamp = position?.timestamp;
  if (!Number.isFinite(now) || !Number.isFinite(latitude) || latitude < -90 || latitude > 90 ||
      !Number.isFinite(longitude) || longitude < -180 || longitude > 180 ||
      !Number.isFinite(accuracy) || accuracy < 0 ||
      !Number.isFinite(timestamp) || timestamp <= 0 || timestamp > now) return null;
  return { latitude, longitude, accuracy, timestamp, freshness: 'current' };
}

export function validateFix(position: GeolocationPosition, now: number): Fix | null {
  const fix = readFix(position, now);
  return fix && now - fix.timestamp < FIX_MAX_AGE_MS ? fix : null;
}

type Environment = {
  secure: boolean;
  geolocation?: Pick<Geolocation, 'watchPosition' | 'clearWatch'>;
  permissions?: Pick<Permissions, 'query'>;
  now: () => number;
  schedule: (callback: () => void, ms: number) => () => void;
};

export function browserLocationEnvironment(): Environment {
  return {
    secure: window.isSecureContext,
    geolocation: navigator.geolocation,
    permissions: navigator.permissions,
    now: Date.now,
    schedule(callback, ms) {
      const timer = window.setTimeout(callback, ms);
      return () => window.clearTimeout(timer);
    },
  };
}

type Session = { id?: number; detach?: () => void; cancelExpiry?: () => void; lastTimestamp?: number; granted?: boolean };

export function createLocationController(env: Environment, publish: (state: LocationState) => void) {
  let session: Session | undefined;
  const update = (status: Status, watching = false) => publish({ status, watching });

  function release() {
    const previous = session;
    session = undefined;
    previous?.detach?.();
    previous?.cancelExpiry?.();
    if (previous?.id !== undefined) env.geolocation?.clearWatch(previous.id);
  }

  function end(status: Status) {
    release();
    update(status);
  }

  async function start() {
    if (session) return;
    if (!env.secure) return update('insecure');
    const geolocation = env.geolocation;
    if (!geolocation || typeof geolocation.watchPosition !== 'function' ||
        typeof geolocation.clearWatch !== 'function') return update('unsupported');

    const current: Session = {};
    session = current;
    if (env.permissions) {
      update('checking', true);
      // A browser may expose the permissions API and still reject geolocation queries.
      let permission: PermissionStatus | undefined;
      try {
        permission = await env.permissions.query({ name: 'geolocation' });
      } catch {
        // The explicit gesture still authorizes requesting the native watch.
      }
      if (current !== session) return;
      if (permission?.state === 'denied') return end('denied');
      if (permission) {
        const status = permission;
        current.granted = status.state === 'granted';
        const onChange = () => {
          if (session !== current) return;
          if (status.state === 'denied' || (current.granted && status.state === 'prompt')) end('revoked');
          else if (status.state === 'granted') {
            current.granted = true;
            if (current.lastTimestamp === undefined) update('locating', true);
          }
        };
        status.addEventListener('change', onChange);
        current.detach = () => status.removeEventListener('change', onChange);
      }
    }
    update(current.granted ? 'locating' : 'permission', true);

    try {
      const id = geolocation.watchPosition((position) => {
        if (session !== current) return;
        const now = env.now();
        const fix = readFix(position, now);
        if (!fix || (current.lastTimestamp !== undefined && fix.timestamp <= current.lastTimestamp)) return;
        if (now - fix.timestamp >= FIX_MAX_AGE_MS) {
          if (current.lastTimestamp === undefined) update('stale', true);
          return;
        }

        current.granted = true;
        current.lastTimestamp = fix.timestamp;
        current.cancelExpiry?.();
        const timestamp = fix.timestamp;
        current.cancelExpiry = env.schedule(() => {
          if (session === current && current.lastTimestamp === timestamp) update('stale', true);
        }, FIX_MAX_AGE_MS - (now - timestamp));
        publish({ status: 'available', watching: true, fix });
      }, (error) => {
        if (session !== current) return;
        if (error.code === 1) return end(current.granted ? 'revoked' : 'denied');
        current.cancelExpiry?.();
        update(error.code === 3 ? 'timeout' : 'unavailable', true);
      }, WATCH_OPTIONS);
      // A browser callback can settle the session before a watch ID is returned.
      if (session === current) current.id = id;
      else geolocation.clearWatch(id);
    } catch {
      if (session === current) end('unavailable');
    }
  }

  return {
    start,
    stop: () => end('stopped'),
    stopIfActive: () => { if (session) end('stopped'); },
    dispose: release,
  };
}
