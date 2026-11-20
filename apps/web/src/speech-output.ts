import { useEffect, useRef, useState } from 'react';
import type { RecognitionLanguage } from './speech';

const PREVIEW = 'Speak to AI Agent';
const voiceTags: Record<RecognitionLanguage, string> = { en: 'en-US', 'ur-PK': 'ur-PK', 'ps-AF': 'ps-AF' };
type Capability = 'checking' | 'unsupported' | 'missing' | 'dialect' | 'remote' | 'unknown' | 'ready';
type Playback = 'idle' | 'speaking' | 'stopped' | 'completed' | 'error';

function voiceFor(voices: SpeechSynthesisVoice[], tag: string) {
  return voices.find(voice => voice.lang.toLowerCase() === tag.toLowerCase() && voice.localService === true);
}

function capabilityFor(voices: SpeechSynthesisVoice[], tag: string): Capability {
  if (voiceFor(voices, tag)) return 'ready';
  const exact = voices.filter(voice => voice.lang.toLowerCase() === tag.toLowerCase());
  if (exact.some(voice => voice.localService !== false)) return 'unknown';
  if (exact.length) return 'remote';
  if (voices.some(voice => voice.lang.split('-')[0].toLowerCase() === tag.split('-')[0].toLowerCase())) return 'dialect';
  return 'missing';
}

export const playbackMessages: Record<Capability | Exclude<Playback, 'idle'>, string> = {
  checking: 'Checking for a local playback voice.',
  unsupported: 'Speech playback is unavailable in this browser.',
  missing: 'No voice for this locale is available on this device.',
  dialect: 'Only a different voice locale is available on this device.',
  remote: 'This locale uses a remote voice. Local playback is unavailable.',
  unknown: 'The browser cannot verify that this voice is local.',
  ready: 'A compatible local voice is available.',
  speaking: 'Playing the speech preview.',
  stopped: 'Speech preview stopped.',
  completed: 'Speech preview finished.',
  error: 'Speech playback could not finish.',
};

export function useSpeechOutput(language: RecognitionLanguage, blocked: boolean) {
  const tag = voiceTags[language];
  const [engine] = useState(() => typeof window.SpeechSynthesisUtterance === 'function' &&
    typeof window.speechSynthesis?.getVoices === 'function' &&
    typeof window.speechSynthesis?.speak === 'function' &&
    typeof window.speechSynthesis?.cancel === 'function' ? window.speechSynthesis : undefined);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[] | null>(null);
  const [phase, setPhase] = useState<Playback>('idle');
  const active = useRef<{ utterance: SpeechSynthesisUtterance; timer: number } | null>(null);
  const capability = !engine ? 'unsupported' : voices === null ? 'checking' : capabilityFor(voices, tag);

  function release() {
    const session = active.current;
    if (!session) return false;
    active.current = null;
    window.clearTimeout(session.timer);
    session.utterance.onend = null;
    session.utterance.onerror = null;
    try { engine?.cancel(); } catch { /* The browser can withdraw speech support. */ }
    return true;
  }

  function stop() {
    if (release()) setPhase('stopped');
  }

  useEffect(() => {
    if (!engine) return;
    function refresh() {
      try { setVoices(engine!.getVoices()); } catch { setVoices([]); }
    }
    const timer = window.setTimeout(refresh, 2000);
    try {
      const initial = engine.getVoices();
      if (initial.length) setVoices(initial);
    } catch { setVoices([]); }
    engine.addEventListener('voiceschanged', refresh);
    function hide() { if (document.hidden) stop(); }
    document.addEventListener('visibilitychange', hide);
    window.addEventListener('pagehide', stop);
    return () => {
      window.clearTimeout(timer);
      engine.removeEventListener('voiceschanged', refresh);
      document.removeEventListener('visibilitychange', hide);
      window.removeEventListener('pagehide', stop);
      release();
    };
  }, [engine]);

  useEffect(() => {
    if (release()) setPhase('stopped');
    else if (!blocked) setPhase('idle');
  }, [tag, blocked]);

  function playPreview() {
    if (!engine || language !== 'en' || blocked || document.hidden || active.current) return;
    try {
      const voice = voiceFor(engine.getVoices(), tag);
      if (!voice) { setVoices(engine.getVoices()); return; }
      const utterance = new SpeechSynthesisUtterance(PREVIEW);
      utterance.voice = voice;
      utterance.lang = tag;
      function finish(next: Playback) {
        if (active.current?.utterance !== utterance) return;
        window.clearTimeout(active.current.timer);
        active.current = null;
        utterance.onend = null;
        utterance.onerror = null;
        setPhase(next);
      }
      utterance.onend = () => finish('completed');
      utterance.onerror = () => finish('error');
      active.current = { utterance, timer: window.setTimeout(() => {
        if (release()) setPhase('error');
      }, 15000) };
      setPhase('speaking');
      engine.speak(utterance);
    } catch {
      release();
      setPhase('error');
    }
  }

  return { phase, capability, playPreview, stop };
}
