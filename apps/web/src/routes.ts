export const navigation = [
  ['/', 'Home'], ['/assess', 'Assess'], ['/guidance', 'Guidance'],
  ['/hospitals', 'Hospitals'], ['/doctors', 'Doctors'], ['/appointments', 'My Appointments'],
] as const;

export const unavailable = {
  '/assess': ['Assess', 'Symptom assessment is currently unavailable.'],
  '/guidance': ['Guidance', 'Personalized health guidance is currently unavailable.'],
  '/hospitals': ['Hospitals', 'Verified hospital listings and nearby search are currently unavailable.'],
  '/doctors': ['Doctors', 'Verified doctor profiles and discovery are currently unavailable.'],
  '/appointments': ['My Appointments', 'Booking and appointment management are currently unavailable.'],
  '/ai-agent': ['AI Agent', 'The AI agent is currently unavailable.'],
} as const;

export type Route = '/' | keyof typeof unavailable;

export function readRoute(): Route | null {
  const path = window.location.hash.slice(1) || '/';
  if (path === '/') return path;
  return Object.hasOwn(unavailable, path) ? path as keyof typeof unavailable : null;
}

export const to = (route: Route) => '#' + route;
