// Voice input/output. Which flow is used is decided by the backend, not
// here: GET /voice/config reports the server-side VOICE_PROVIDER once at
// load, and this module picks the matching client flow.
//
//   web_speech        -> the browser does STT and TTS itself, no backend
//                        calls, no credentials, Chrome/Edge only.
//   bhashini | google -> record WAV in the browser, POST it to this
//                        project's own /voice/asr and /voice/tts. The
//                        provider credentials stay server-side and are
//                        never exposed to the browser, which is exactly
//                        why these two go through the backend at all.
//
// Both server-side providers share one client flow - the backend swaps
// the actual provider behind /voice/asr and /voice/tts.
import { API_BASE } from './api';

export type VoiceProvider = 'web_speech' | 'bhashini' | 'google';

export interface VoiceConfig {
  provider: VoiceProvider;
  language: string;
}

export interface VoiceHandlers {
  onTranscript: (text: string) => void;
  onError: (message: string) => void;
  onRecordingChange: (recording: boolean) => void;
  onProcessingChange: (processing: boolean) => void;
}

export interface VoiceController {
  provider: VoiceProvider;
  supported: boolean;
  start: () => void;
  stop: () => void;
  speak: (text: string) => void;
  dispose: () => void;
}

// Web Speech (BCP-47) locale, used only in web_speech mode.
const WEB_SPEECH_LANG = 'en-IN';
const MAX_RECORDING_MS = 15000;

const DEFAULT_CONFIG: VoiceConfig = { provider: 'web_speech', language: WEB_SPEECH_LANG };

// The backend reports a bare ISO-639 code ("hi"); SpeechRecognition wants
// a BCP-47 locale ("hi-IN"). Without this, Hindi speech is decoded by an
// English recogniser and comes out as nonsense words.
const WEB_SPEECH_LOCALES: Record<string, string> = {
  hi: 'hi-IN',
  en: 'en-IN',
  bn: 'bn-IN',
  ta: 'ta-IN',
  te: 'te-IN',
  mr: 'mr-IN',
  gu: 'gu-IN',
  kn: 'kn-IN',
  ml: 'ml-IN',
  pa: 'pa-IN',
};

function toSpeechLocale(language: string): string {
  if (language.includes('-')) return language; // already a full locale
  return WEB_SPEECH_LOCALES[language] ?? WEB_SPEECH_LANG;
}

/** Never throws: an unreachable backend falls back to the zero-config
 * browser flow rather than leaving the mic button dead. */
export async function fetchVoiceConfig(): Promise<VoiceConfig> {
  try {
    const res = await fetch(`${API_BASE}/voice/config`);
    if (!res.ok) return DEFAULT_CONFIG;
    const data = (await res.json()) as Partial<VoiceConfig>;
    if (data.provider === 'bhashini' || data.provider === 'google') {
      return { provider: data.provider, language: data.language ?? 'hi' };
    }
    if (data.provider === 'web_speech') {
      // Honour the configured language here too - the browser recogniser
      // needs to be told what it's listening for.
      return { provider: 'web_speech', language: data.language ?? WEB_SPEECH_LANG };
    }
  } catch {
    // fall through to the browser-native default
  }
  return DEFAULT_CONFIG;
}

export function createVoiceController(config: VoiceConfig, handlers: VoiceHandlers): VoiceController {
  return config.provider === 'web_speech'
    ? createWebSpeechController(config, handlers)
    : createServerVoiceController(config, handlers);
}

// ---------------------------------------------------------------------
// web_speech: everything happens in the browser
// ---------------------------------------------------------------------

function createWebSpeechController(config: VoiceConfig, handlers: VoiceHandlers): VoiceController {
  const locale = toSpeechLocale(config.language);
  const SpeechRecognitionImpl =
    (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

  if (!SpeechRecognitionImpl) {
    return unsupportedController(
      'web_speech',
      "Voice input isn't supported in this browser - please type your question instead.",
      handlers,
    );
  }

  const recognition = new SpeechRecognitionImpl();
  recognition.lang = locale;
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;

  recognition.onresult = (event: any) => {
    handlers.onRecordingChange(false);
    handlers.onTranscript(event.results[0][0].transcript);
  };
  recognition.onerror = () => {
    handlers.onRecordingChange(false);
    handlers.onError("I couldn't hear that clearly. Please try again, or type your question.");
  };
  recognition.onend = () => handlers.onRecordingChange(false);

  return {
    provider: 'web_speech',
    supported: true,
    start: () => {
      try {
        recognition.start();
        handlers.onRecordingChange(true);
      } catch {
        handlers.onRecordingChange(false);
      }
    },
    stop: () => recognition.stop(),
    speak: (text: string) => {
      if (!('speechSynthesis' in window)) return;
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = locale;
      window.speechSynthesis.speak(utterance);
    },
    dispose: () => {
      try {
        recognition.abort();
      } catch {
        // already stopped
      }
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    },
  };
}

// ---------------------------------------------------------------------
// bhashini | google: record WAV here, transcribe and synthesise server-side
// ---------------------------------------------------------------------

interface RecordingSession {
  stream: MediaStream;
  audioCtx: AudioContext;
  source: MediaStreamAudioSourceNode;
  processor: ScriptProcessorNode;
  silentGain: GainNode;
  chunks: Float32Array[];
}

function createServerVoiceController(config: VoiceConfig, handlers: VoiceHandlers): VoiceController {
  if (!navigator.mediaDevices?.getUserMedia) {
    return unsupportedController(
      config.provider,
      "Microphone access isn't supported in this browser - please type your question instead.",
      handlers,
    );
  }

  let session: RecordingSession | null = null;
  let autoStopTimer: ReturnType<typeof setTimeout> | null = null;
  let currentAudio: HTMLAudioElement | null = null;
  // Bumped on every new playback request so a slow TTS response for an
  // earlier answer can't start talking over a newer one.
  let speechToken = 0;
  let disposed = false;

  const teardown = (): RecordingSession | null => {
    if (autoStopTimer) {
      clearTimeout(autoStopTimer);
      autoStopTimer = null;
    }
    const finished = session;
    session = null;
    if (!finished) return null;

    finished.source.disconnect();
    finished.processor.disconnect();
    finished.silentGain.disconnect();
    finished.stream.getTracks().forEach((track) => track.stop());
    return finished;
  };

  const start = async () => {
    if (session) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (disposed) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      const AudioContextImpl = window.AudioContext || (window as any).webkitAudioContext;
      const audioCtx: AudioContext = new AudioContextImpl();
      const source = audioCtx.createMediaStreamSource(stream);
      // Deprecated, but the only universally available way to get raw
      // PCM out without shipping an AudioWorklet module - MediaRecorder
      // produces webm/opus, which neither ASR provider accepts.
      const processor = audioCtx.createScriptProcessor(4096, 1, 1);
      const silentGain = audioCtx.createGain();
      silentGain.gain.value = 0; // don't echo the mic straight back to the speakers

      const chunks: Float32Array[] = [];
      processor.onaudioprocess = (event) => {
        chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      };
      source.connect(processor);
      processor.connect(silentGain);
      silentGain.connect(audioCtx.destination);

      session = { stream, audioCtx, source, processor, silentGain, chunks };
      handlers.onRecordingChange(true);
      autoStopTimer = setTimeout(stop, MAX_RECORDING_MS);
    } catch {
      handlers.onRecordingChange(false);
      handlers.onError("I couldn't access your microphone. Please check permissions and try again.");
    }
  };

  const stop = () => {
    const finished = teardown();
    handlers.onRecordingChange(false);
    if (!finished) return;

    const sampleRate = finished.audioCtx.sampleRate;
    void finished.audioCtx.close();
    const wav = encodeWavPCM16(mergeBuffers(finished.chunks), sampleRate);
    handlers.onProcessingChange(true);
    void transcribe(wav, sampleRate);
  };

  const transcribe = async (wav: ArrayBuffer, sampleRate: number) => {
    try {
      const res = await fetch(`${API_BASE}/voice/asr`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          audio_base64: arrayBufferToBase64(wav),
          language: config.language,
          audio_format: 'wav',
          sampling_rate: sampleRate,
        }),
      });
      const data = (await res.json()) as { transcript?: string | null; error?: string | null };
      if (disposed) return;

      if (!res.ok || data.error || !data.transcript) {
        handlers.onProcessingChange(false);
        handlers.onError(
          data.error ?? "I couldn't understand that. Please try again, or type your question.",
        );
        return;
      }
      // Cleared before handing off, so the query's own loading state owns
      // the spinner from here on.
      handlers.onProcessingChange(false);
      handlers.onTranscript(data.transcript);
    } catch {
      if (disposed) return;
      handlers.onProcessingChange(false);
      handlers.onError(
        "I couldn't reach the speech recognition service. Please try again, or type your question.",
      );
    }
  };

  const speak = async (text: string) => {
    speechToken += 1;
    const token = speechToken;
    currentAudio?.pause();
    currentAudio = null;

    try {
      // One request for the whole answer rather than one per sentence:
      // provider quotas are counted per request, and the spoken answer is
      // a nice-to-have on top of text that is already on screen.
      const res = await fetch(`${API_BASE}/voice/tts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, language: config.language }),
      });
      const data = (await res.json()) as { audio_base64?: string | null; audio_format?: string };
      if (disposed || token !== speechToken || !data.audio_base64) return;

      const audio = new Audio(`data:audio/${data.audio_format ?? 'wav'};base64,${data.audio_base64}`);
      currentAudio = audio;
      await audio.play().catch(() => undefined);
    } catch {
      // A TTS failure must never block or hide the text answer, which is
      // already displayed by the time this runs.
    }
  };

  return {
    provider: config.provider,
    supported: true,
    start: () => void start(),
    stop,
    speak: (text: string) => void speak(text),
    dispose: () => {
      disposed = true;
      const finished = teardown();
      if (finished) void finished.audioCtx.close();
      currentAudio?.pause();
      currentAudio = null;
    },
  };
}

function unsupportedController(
  provider: VoiceProvider,
  message: string,
  handlers: VoiceHandlers,
): VoiceController {
  return {
    provider,
    supported: false,
    start: () => handlers.onError(message),
    stop: () => undefined,
    speak: () => undefined,
    dispose: () => undefined,
  };
}

// ---------------------------------------------------------------------
// WAV encoding - both ASR providers expect wav/flac/mp3, not the
// webm/opus a MediaRecorder would hand us.
// ---------------------------------------------------------------------

function mergeBuffers(chunks: Float32Array[]): Float32Array {
  const length = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const merged = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.length;
  }
  return merged;
}

function encodeWavPCM16(samples: Float32Array, sampleRate: number): ArrayBuffer {
  const numChannels = 1;
  const bytesPerSample = 2;
  const blockAlign = numChannels * bytesPerSample;
  const dataSize = samples.length * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  const writeString = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
  };

  writeString(0, 'RIFF');
  view.setUint32(4, 36 + dataSize, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * blockAlign, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bytesPerSample * 8, true);
  writeString(36, 'data');
  view.setUint32(40, dataSize, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const sample = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }
  return buffer;
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  const CHUNK = 0x8000; // avoid blowing the argument limit on long recordings
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}
