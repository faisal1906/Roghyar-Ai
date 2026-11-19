export type PwaStatus = 'idle' | 'waiting' | 'activating' | 'reload' | 'error' | 'unsupported';

export function reloadAction(status: PwaStatus, hasDraft: boolean, confirmed: boolean) {
  if (status !== 'reload') return 'wait';
  return hasDraft && !confirmed ? 'confirm' : 'reload';
}
