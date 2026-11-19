import { useContext, useEffect, useId, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { EmergencyOpen } from './emergency-alert';
import { Icon } from './icons';
import {
  languagePackOptions, localSpeech, recognitionError, recognitionLanguages,
  type PackStatus, type Recognition, type RecognitionLanguage,
} from './speech';

type Phase = 'idle' | 'requesting' | 'listening' | 'processing' | 'ready' | 'unsupported' | 'error';

interface Session {
  recognition: Recognition;
  seen: Set<number>;
  hasTranscript: boolean;
  finishing?: boolean;
  timer?: number;
}

const statusText: Record<Phase, string> = {
  idle: 'Ready to record. You can also type below.',
  requesting: 'Waiting for microphone permission…',
  listening: 'Listening…',
  processing: 'Processing speech…',
  ready: 'Transcript ready. Review and edit it below.',
  unsupported: 'On-device speech recognition is unavailable in this browser. Type below.',
  error: 'Speech recognition could not finish. You can type below.',
};

function disconnect(session: Session) {
  const { recognition } = session;
  window.clearTimeout(session.timer);
  recognition.onstart = null;
  recognition.onspeechend = null;
  recognition.onresult = null;
  recognition.onerror = null;
  recognition.onend = null;
}

interface VoiceInputProps {
  transcript: string;
  setTranscript: Dispatch<SetStateAction<string>>;
  descriptionRequired?: boolean;
  descriptionError?: string;
}

export function VoiceInput({ transcript, setTranscript, descriptionRequired, descriptionError }: VoiceInputProps) {
  const emergencyOpen = useContext(EmergencyOpen);
  const [speech] = useState(localSpeech);
  const [phase, setPhase] = useState<Phase>(speech ? 'idle' : 'unsupported');
  const [language, setLanguage] = useState<RecognitionLanguage>('en');
  const [pack, setPack] = useState<{ language: RecognitionLanguage; status: PackStatus }>();
  const [packCheck, refreshPack] = useState(0);
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState('');
  const input = useRef<HTMLTextAreaElement>(null);
  const recordButton = useRef<HTMLButtonElement>(null);
  const active = useRef<Session | null>(null);
  const id = useId();
  const busy = phase === 'requesting' || phase === 'listening' || phase === 'processing';
  const packStatus = pack?.language === language ? pack.status : undefined;
  const canInstall = packStatus === 'downloadable' && !!speech?.Constructor.install;
  const micDisabled = emergencyOpen || busy || installing || phase === 'unsupported' ||
    (!!packStatus && packStatus !== 'available');

  useEffect(() => {
    const browser = speech?.Constructor;
    if (!browser?.available) return;
    let current = true;
    void browser.available(languagePackOptions(language)).then(status => {
      if (current) setPack({ language, status });
    }).catch(() => {
      // Some browser policies block the availability check. Start can still report errors.
    });
    return () => { current = false; };
  }, [speech, language, packCheck]);

  function release() {
    const session = active.current;
    if (!session) return;
    active.current = null;
    disconnect(session);
    try {
      session.recognition.abort();
    } catch {
      // The browser may already have ended the session.
    }
  }

  useEffect(() => {
    if (emergencyOpen && active.current) {
      release();
      setPhase('idle');
    }
  }, [emergencyOpen]);

  useEffect(() => {
    function stopWhenHidden() {
      if (document.visibilityState === 'hidden' && active.current) {
        release();
        setPhase('idle');
      }
    }
    function stopOnPageHide() {
      if (active.current) {
        release();
        setPhase('idle');
      }
    }
    document.addEventListener('visibilitychange', stopWhenHidden);
    window.addEventListener('pagehide', stopOnPageHide);
    return () => {
      document.removeEventListener('visibilitychange', stopWhenHidden);
      window.removeEventListener('pagehide', stopOnPageHide);
      release();
    };
  }, []);

  function fail(message: string) {
    release();
    setError(message);
    setPhase('error');
  }

  function processing(session: Session) {
    if (session.finishing) return;
    session.finishing = true;
    setPhase('processing');
    window.clearTimeout(session.timer);
    session.timer = window.setTimeout(() => {
      fail('Speech recognition took too long. Try again or type below.');
    }, 8000);
  }

  async function installPack() {
    const browser = speech?.Constructor;
    if (!browser?.install || !canInstall || installing) return;
    setInstalling(true);
    let installed = false;
    let timeout: number | undefined;
    try {
      installed = await Promise.race([
        browser.install(languagePackOptions(language)),
        new Promise<boolean>(resolve => { timeout = window.setTimeout(() => resolve(false), 60000); }),
      ]);
    } catch {
      // An interrupted or rejected browser download uses the same fallback.
    } finally {
      window.clearTimeout(timeout);
    }
    setInstalling(false);
    if (installed) {
      setPack({ language, status: 'available' });
      setPhase('idle');
    } else {
      setError('The browser did not finish setting up the language pack. You can try again or type below.');
      setPhase('error');
      refreshPack(count => count + 1);
    }
  }

  function start() {
    if (active.current || document.visibilityState === 'hidden') return;
    const recognition = localSpeech()?.recognition;
    if (!recognition) {
      setPhase('unsupported');
      return;
    }

    const session: Session = { recognition, seen: new Set(), hasTranscript: false };
    active.current = session;
    try {
      recognition.processLocally = true;
      if (recognition.processLocally !== true) {
        release();
        setPhase('unsupported');
        return;
      }
      recognition.lang = language;
      recognition.continuous = false;
      recognition.interimResults = false;
      recognition.onstart = () => {
        if (active.current === session) setPhase('listening');
      };
      recognition.onspeechend = () => {
        if (active.current === session) processing(session);
      };
      recognition.onresult = ({ resultIndex, results }) => {
        if (active.current !== session) return;
        const segments: string[] = [];
        for (let index = resultIndex; index < results.length; index++) {
          const result = results[index];
          if (!result.isFinal || session.seen.has(index)) continue;
          session.seen.add(index);
          const text = result[0].transcript.trim();
          if (text) segments.push(text);
        }
        if (segments.length) {
          session.hasTranscript = true;
          const text = segments.join(' ');
          setTranscript(previous => previous && !/\s$/.test(previous) ? previous + ' ' + text : previous + text);
        }
      };
      recognition.onerror = ({ error: code }) => {
        if (active.current === session) fail(recognitionError(code));
      };
      recognition.onend = () => {
        if (active.current !== session) return;
        active.current = null;
        disconnect(session);
        if (session.hasTranscript) setPhase('ready');
        else {
          setError(recognitionError('no-speech'));
          setPhase('error');
        }
      };

      setPhase('requesting');
      session.timer = window.setTimeout(() => {
        fail('Recording timed out. Try again or type below.');
      }, 45000);
      recognition.start();
    } catch (cause) {
      fail(recognitionError(cause instanceof Error ? cause.name : 'unknown'));
    }
  }

  function stop() {
    const session = active.current;
    if (!session || phase !== 'listening') return;
    processing(session);
    try {
      session.recognition.stop();
    } catch {
      fail(recognitionError('unknown'));
    }
    input.current?.focus();
  }

  function cancel() {
    release();
    setPhase('idle');
    recordButton.current?.focus();
  }

  function clear() {
    release();
    setTranscript('');
    if (phase !== 'unsupported') setPhase('idle');
    input.current?.focus();
  }

  let message = phase === 'error' ? error : statusText[phase];
  if (installing) message = 'Downloading the on-device speech language pack…';
  else if (phase === 'idle' && packStatus === 'unavailable') message = recognitionError('language-not-supported');
  else if (phase === 'idle' && packStatus === 'downloadable') {
    message = canInstall ? 'Set up the language pack to use the microphone, or type below.' :
      'The browser needs a speech language pack for this language. You can type below.';
  } else if (phase === 'idle' && packStatus === 'downloading') {
    message = 'The browser is downloading this language pack. Check again or type below.';
  }

  return (
    <section className="voice-input" aria-labelledby={id + '-title'}>
      <div className="voice-entry">
        <button className="voice-mic" type="button" aria-label="Start recording" aria-describedby={id + '-privacy'}
          aria-disabled={micDisabled} data-listening={phase === 'listening'}
          ref={recordButton} onClick={() => { if (!micDisabled) start(); }}>
          <Icon name="mic" />
        </button>
        <div>
          <h2 id={id + '-title'}>Speak to AI Agent</h2>
          <p lang="ur-Latn" className="voice-label">AI Agent se Pochy</p>
        </div>
      </div>
      <label htmlFor={id + '-language'}>Recognition language</label>
      <select id={id + '-language'} value={language} disabled={busy || installing || phase === 'unsupported'}
        onChange={event => {
          setLanguage(event.target.value as RecognitionLanguage);
          if (phase !== 'unsupported') setPhase('idle');
        }}>
        {recognitionLanguages.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
      </select>
      <p id={id + '-privacy'} className="voice-help">Voice stays on this device. Language support depends on your browser.</p>
      <p role="status" data-phase={phase} className="voice-status">{message}</p>
      {canInstall && <div className="voice-actions">
        <button className="button button-outline" type="button" disabled={installing}
          onClick={() => void installPack()}>Download language pack</button>
      </div>}
      {packStatus === 'downloading' && <div className="voice-actions">
        <button className="button button-outline" type="button"
          onClick={() => refreshPack(count => count + 1)}>Check language pack</button>
      </div>}
      {busy && <div className="voice-actions">
        {phase === 'listening' && <button className="button button-outline" type="button" onClick={stop}>Stop recording</button>}
        <button className="button button-outline" type="button" onClick={cancel}>Cancel recording</button>
      </div>}
      <label htmlFor={id + '-transcript'}>Symptom description{descriptionRequired && <span className="field-note">Required</span>}</label>
      <textarea id={id + '-transcript'} ref={input} value={transcript} rows={3} dir="auto"
        autoComplete="off" spellCheck={false} autoCorrect="off"
        required={descriptionRequired} aria-invalid={descriptionError ? true : undefined}
        aria-describedby={descriptionError ? id + '-description-error' : undefined}
        onChange={event => setTranscript(event.target.value)}
        placeholder="Type or review your transcript here" />
      {descriptionError && <p className="field-error" id={id + '-description-error'}>{descriptionError}</p>}
      <div className="voice-actions">
        <button className="button button-outline" type="button" onClick={() => input.current?.focus()}>Edit text</button>
        <button className="button button-outline" type="button" disabled={!transcript} onClick={clear}>Clear text</button>
      </div>
      <p className="voice-help">Submitting symptoms for analysis is currently unavailable.</p>
    </section>
  );
}
