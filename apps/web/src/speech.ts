export const recognitionLanguages = [
  ['en', 'English'], ['ur-PK', 'Urdu'], ['ps-AF', 'Pashto'],
] as const;

export type RecognitionLanguage = typeof recognitionLanguages[number][0];

interface SpeechResult {
  isFinal: boolean;
  0: { transcript: string };
}

export interface Recognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  processLocally?: boolean;
  onstart: (() => void) | null;
  onspeechend: (() => void) | null;
  onresult: ((event: { resultIndex: number; results: ArrayLike<SpeechResult> }) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

export type PackStatus = 'available' | 'downloadable' | 'downloading' | 'unavailable';
type PackOptions = { langs: RecognitionLanguage[]; processLocally: true };
interface RecognitionConstructor {
  new (): Recognition;
  available?(options: PackOptions): Promise<PackStatus>;
  install?(options: PackOptions): Promise<boolean>;
}

export function localSpeech() {
  const browser = window as Window & {
    SpeechRecognition?: RecognitionConstructor;
    webkitSpeechRecognition?: RecognitionConstructor;
  };
  for (const Constructor of [browser.SpeechRecognition, browser.webkitSpeechRecognition]) {
    if (!Constructor) continue;
    try {
      const recognition = new Constructor();
      if (typeof recognition.processLocally === 'boolean') return { recognition, Constructor };
    } catch {
      // This browser may expose the constructor even when recognition is unavailable.
    }
  }
  return undefined;
}

export function languagePackOptions(language: RecognitionLanguage): PackOptions {
  return { langs: [language], processLocally: true };
}

export function recognitionError(code: string): string {
  switch (code) {
    case 'not-allowed':
    case 'service-not-allowed':
    case 'NotAllowedError':
    case 'SecurityError':
      return 'Microphone access was blocked. Check your browser permissions or type below.';
    case 'audio-capture':
      return 'The browser could not use a microphone. Check your device or type below.';
    case 'no-speech':
      return 'No speech was heard. Try again or type below.';
    case 'language-not-supported':
    case 'NotSupportedError':
      return 'The selected language is unavailable for on-device recognition. Choose another language or type below.';
    default:
      return 'Speech recognition could not finish. Try again or type below.';
  }
}
