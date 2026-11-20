# AI-05 speech capability and fallbacks

The existing recognition controller selects en, ur-PK or ps-AF and requires processLocally set to true before start. The pack API checks the same requested tag. Missing or rejected capability checks leave the pack unverified, with the verified local-mode property required even for an attempted recording. Pack setup is user-initiated and may download browser-vendor files. Recording, processing and installation retain their 45, 8 and 60 second limits. Final segments append to the editable draft. Typing remains available. No raw audio or transcript is sent by the application.

The speech-output hook discovers exact en-US, ur-PK and ps-AF voices, observes voiceschanged, and distinguishes checking, unsupported, missing, alternate locale, remote, unknown locality and ready. English uses en-US for the existing English interface preview only. Playback requires a current exact voice with localService true and an explicit click or keyboard action. Urdu and Pashto capability checks are available where the recognition selector is supported. They do not trigger playback of English or unreviewed translated copy. Capability is a browser declaration and does not establish pronunciation quality or offline availability.

The preview speaks only the existing fixed English label. The controller never receives transcript, AI-03 proposals, or AI-04 replies. It limits playback to one bounded utterance and a 15-second deadline. Recording, locale change, hidden pages, navigation, and emergency activation interrupt playback. Stale callbacks cannot change current state. Emergency call links remain independently usable.

Future clinical delivery must source the exact authorized AI-04 text, locale, content ID/version and digest from the trusted reply channel. Recheck content authorization, state, locale and local voice immediately before explicit playback. Clinical playback and warning delivery remain disabled.

## Future provider boundary

A future private server adapter would separately negotiate operation (STT/TTS), exact available language tags, media types, and processing region. Proposed limits are 45 seconds and 2 MiB of validated audio for STT, 2,000 code points of authorized text for TTS, and a 15-second deadline with cancellation. Require session authorization, explicit informed consent for a named processor and region, TLS, bounded validated output, owner-scoped per-operation quotas and spending limits, and approved retention and deletion terms. Capture only technical errors and validated provider/region/locale provenance. No automatic retry or downgrade to remote processing. This interface is a design contract only. Online speech is unavailable. The current fallback is editable typing and visible text.

## Physical-device checklist

| Device | Status |
| --- | --- |
| Windows Chrome and Edge | Native AI-05 recording and playback verification open |
| macOS Chrome and Safari | Open |
| Low-end Android Chrome | Open |

QA-04 should record browser and OS versions, microphone permission outcomes, pack status for each exact tag, voice tags and locality, offline recording and playback, cancellation, keyboard and screen-reader behavior, and transcription and pronunciation observations. Chromium mocks verify application lifecycle and privacy boundaries. Clinical content approval, multilingual quality, hardware behavior and any speech provider release remain gated.

References: [Web Speech API](https://webaudio.github.io/web-speech-api/), [local recognition](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition/processLocally), [voice locality](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesisVoice/localService), and [voice list updates](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesis/voiceschanged_event).
