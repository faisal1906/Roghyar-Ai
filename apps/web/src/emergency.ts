export interface ReviewedEmergencyWarning {
  contentId: string;
  revision: string;
  text: string;
}

export type EmergencyTrigger =
  | { source: 'manual' }
  | { source: 'reviewed-rule'; categoryId: string; warning: ReviewedEmergencyWarning };

export type EmergencyState = Extract<EmergencyTrigger, { source: 'manual' }> | null;

export function activateEmergency(current: EmergencyState, trigger: EmergencyTrigger): EmergencyState {
  // BE-03/04 must supply and authorize reviewed rule output before this gate changes.
  if (current || trigger.source !== 'manual') return current;
  return trigger;
}

export interface VerifiedEmergencyHospital {
  directoryId: string;
  name: string;
  emergencyService: 'available' | 'unavailable';
  verifiedAt: string;
  distanceKm?: number;
  travelMinutes?: number;
  directionsUrl?: `https://${string}`;
  telephone?: `tel:${string}`;
}

export interface ConsentedEmergencyLocation {
  latitude: number;
  longitude: number;
  consentId: string;
}

export interface EmergencyIntegrationPorts {
  findVerifiedHospital(location: ConsentedEmergencyLocation): Promise<VerifiedEmergencyHospital | null>;
  shareLocation(request: {
    location: ConsentedEmergencyLocation;
    sharingConsentId: string;
    recipient: string;
    channel: 'sms' | 'whatsapp';
  }): Promise<{ status: 'confirmed'; deliveryId: string } | { status: 'unconfirmed' }>;
  speak(warning: ReviewedEmergencyWarning, language: 'en' | 'ur-PK' | 'ps-AF'): void;
  playTone(): void;
  vibrate(): void;
}

export const emergencyWarningSigns = [
  'Chest pain or pressure, especially with sweating, shortness of breath, or pain spreading to an arm or the jaw.',
  'Severe trouble breathing, choking, blue lips, or being unable to speak in full sentences.',
  'Sudden face drooping, one-sided weakness, slurred speech, a sudden severe headache, or a seizure.',
  'Heavy bleeding that will not stop, or vomiting or passing blood.',
  'Fainting, being unresponsive, or sudden confusion.',
  'Severe bleeding or severe abdominal pain during pregnancy.',
  'A very ill baby with a high fever, trouble breathing, difficulty waking, or poor feeding.',
  'Suspected poisoning, a major burn, or a serious head injury or fracture.',
] as const;

export const immediateActions = [
  'Call Rescue 1122 immediately.',
  'Do not drive yourself to the hospital.',
  'Ask someone nearby to stay with you.',
  'Follow the emergency dispatcher’s instructions.',
] as const;

export function emergencyHospitalMessage(offline: boolean) {
  return offline
    ? 'You’re offline. The app cannot determine your condition or find the nearest emergency hospital.'
    : 'Verified nearby emergency hospital information is unavailable.';
}
