import type { paths } from './api.generated';

const livenessPath = '/api/v1/health/live' satisfies keyof paths;
type Liveness = paths[typeof livenessPath]['get']['responses'][200]['content']['application/json'];

export async function getLiveness(signal: AbortSignal): Promise<Liveness> {
  const response = await fetch(livenessPath, { cache: 'no-store', signal });
  if (!response.ok) {
    throw new Error('API unavailable');
  }
  const body: unknown = await response.json();
  if (typeof body !== 'object' || body === null || !('status' in body) || body.status !== 'ok') {
    throw new Error('Invalid liveness response');
  }
  return { status: body.status };
}
