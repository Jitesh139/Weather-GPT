import { useEffect, useRef, useState } from 'react';
import { Check, Keyboard, Loader2, Mic, Pencil, SkipForward, Sprout, Volume2, AlertCircle } from 'lucide-react';
import { createVoiceController, type VoiceConfig, type VoiceController } from '../lib/voice';
import {
  normalizeCrop,
  parsePlantedDaysAgo,
  plantedAtFromDaysAgo,
  plantedLabel,
  saveFarmerProfile,
  type FarmerProfile,
} from '../lib/farmerProfile';

// Two spoken questions (crop, and how long ago it was planted) shown in
// front of the farmer chat until they're answered or skipped. Uses the
// same voice controller as the chat, so ASR/TTS go through the same
// /voice/asr and /voice/tts calls with no extra latency.

type Field = 'crop' | 'planted';
type View = 'voice' | 'form' | 'confirm';

// The farmer dashboard has no language setting of its own, so onboarding
// is Hindi. The recogniser is told so too (it matters for web_speech and
// Bhashini ASR, which need the language up front).
const ONBOARDING_LANGUAGE = 'hi';

const QUESTIONS: Record<Field, string> = {
  crop: 'आप कौन सी फसल उगा रहे हैं?',
  planted: 'आपने इसे कितने समय पहले लगाया था?',
};

const PLACEHOLDERS: Record<Field, string> = {
  crop: 'जैसे: गेहूं, धान, सोयाबीन',
  planted: 'जैसे: 15 दिन पहले, एक महीना पहले',
};

// Spoken question hasn't started playing by now -> go to typing.
const TTS_START_TIMEOUT_MS = 5000;
// Mic never came on (permission prompt ignored, device busy).
const MIC_START_TIMEOUT_MS = 10000;
// Mic is on but the farmer hasn't started talking.
const NO_RESPONSE_MS = 8000;
// Transcription is taking too long.
const ASR_TIMEOUT_MS = 8000;
// How long the recognised answer stays on screen before the next question.
const ADVANCE_DELAY_MS = 900;

interface FarmerOnboardingProps {
  /** null while /voice/config is still loading. */
  voiceConfig: VoiceConfig | null;
  /** Set when re-running the flow from the chat's "बदलें" link. */
  initial?: FarmerProfile | null;
  onComplete: (profile: FarmerProfile) => void;
  onSkip: () => void;
}

export function FarmerOnboarding({ voiceConfig, initial, onComplete, onSkip }: FarmerOnboardingProps) {
  const [view, setView] = useState<View>('voice');
  const [step, setStep] = useState<Field>('crop');
  const [answers, setAnswers] = useState<Record<Field, string>>({
    crop: initial?.cropRaw ?? '',
    planted: initial?.plantedDaysAgo != null ? `${initial.plantedDaysAgo} दिन पहले` : '',
  });
  // Which answers were actually given in this run - the prefilled ones from
  // an earlier profile shouldn't show up as "आपने कहा" in the voice view.
  const [heard, setHeard] = useState<Record<Field, boolean>>({ crop: false, planted: false });
  const [speaking, setSpeaking] = useState(false);
  const [recording, setRecording] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [micField, setMicField] = useState<Field | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const controllerRef = useRef<VoiceController | null>(null);
  const viewRef = useRef<View>('voice');
  const listenTargetRef = useRef<Field>('crop');
  // Recording is only wanted while we're asking; if the mic comes on late
  // (after a fallback to typing), it's shut straight off again.
  const expectRecordingRef = useRef(false);
  const speakingStartedRef = useRef(false);
  // Bumped whenever the flow moves on, so a late TTS/ASR callback from an
  // earlier step can't act on the current one.
  const runRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimer = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = null;
  };

  const goTo = (next: View) => {
    viewRef.current = next;
    setView(next);
  };

  const fallBackToText = (message: string) => {
    runRef.current += 1;
    clearTimer();
    if (expectRecordingRef.current) {
      expectRecordingRef.current = false;
      controllerRef.current?.abort();
    }
    setMicField(null);
    setNotice(message);
    goTo('form');
  };

  const listen = (field: Field, token: number) => {
    const ctl = controllerRef.current;
    if (!ctl) return;
    listenTargetRef.current = field;
    expectRecordingRef.current = true;
    ctl.start();
    timerRef.current = setTimeout(() => {
      if (runRef.current === token) fallBackToText('माइक चालू नहीं हो पाया। कृपया लिखकर जवाब दें।');
    }, MIC_START_TIMEOUT_MS);
  };

  const ask = async (field: Field) => {
    const token = ++runRef.current;
    clearTimer();
    setStep(field);
    setNotice(null);
    const ctl = controllerRef.current;
    if (!ctl || !ctl.supported) {
      fallBackToText('इस ब्राउज़र में आवाज़ से जवाब देना संभव नहीं है। कृपया लिखकर जवाब दें।');
      return;
    }

    speakingStartedRef.current = false;
    timerRef.current = setTimeout(() => {
      if (runRef.current === token && !speakingStartedRef.current) {
        fallBackToText('सवाल सुनाने में देर हो रही है। कृपया लिखकर जवाब दें।');
      }
    }, TTS_START_TIMEOUT_MS);

    await ctl.speak(QUESTIONS[field]);
    if (runRef.current !== token) return;
    // Spoken, or TTS failed fast - the question is on screen either way,
    // so listen for the answer.
    clearTimer();
    listen(field, token);
  };

  const handleTranscript = (transcript: string) => {
    const field = listenTargetRef.current;
    expectRecordingRef.current = false;
    clearTimer();
    setMicField(null);
    setAnswers((prev) => ({ ...prev, [field]: transcript.trim() }));
    setHeard((prev) => ({ ...prev, [field]: true }));

    if (viewRef.current !== 'voice') return;
    const token = ++runRef.current;
    timerRef.current = setTimeout(() => {
      if (runRef.current !== token) return;
      if (field === 'crop') void ask('planted');
      else goTo('confirm');
    }, ADVANCE_DELAY_MS);
  };

  // Voice callbacks are wired once when the controller is built, so they
  // go through this ref to reach the current render's functions.
  const handlersRef = useRef({ handleTranscript, fallBackToText });
  handlersRef.current = { handleTranscript, fallBackToText };

  useEffect(() => {
    if (!voiceConfig) {
      // Don't wait on a slow /voice/config forever.
      const timer = setTimeout(
        () => handlersRef.current.fallBackToText('आवाज़ सेवा धीमी है। कृपया लिखकर जवाब दें।'),
        TTS_START_TIMEOUT_MS,
      );
      return () => clearTimeout(timer);
    }

    const controller = createVoiceController(
      { ...voiceConfig, language: ONBOARDING_LANGUAGE },
      {
        onTranscript: (text) => handlersRef.current.handleTranscript(text),
        onError: () => {
          expectRecordingRef.current = false;
          setMicField(null);
          if (viewRef.current === 'voice') {
            handlersRef.current.fallBackToText('आवाज़ समझ नहीं आई या माइक नहीं मिला। कृपया लिखकर जवाब दें।');
          } else {
            setNotice('आवाज़ समझ नहीं आई। कृपया फिर से बोलें या लिखें।');
          }
        },
        onRecordingChange: (isRecording) => {
          if (isRecording && !expectRecordingRef.current) {
            controller.abort();
            return;
          }
          setRecording(isRecording);
          // Web Speech has no separate transcription step, and can end
          // with no result at all - release the field's mic here.
          if (!isRecording && controller.provider === 'web_speech') setMicField(null);
          if (isRecording && viewRef.current === 'voice') {
            // Mic is live: now the farmer has ~8s to start answering.
            const token = runRef.current;
            clearTimer();
            timerRef.current = setTimeout(() => {
              if (runRef.current === token) {
                handlersRef.current.fallBackToText('कोई जवाब सुनाई नहीं दिया। कृपया लिखकर जवाब दें।');
              }
            }, NO_RESPONSE_MS);
          }
        },
        onProcessingChange: (isProcessing) => {
          setProcessing(isProcessing);
          if (isProcessing && viewRef.current === 'voice') {
            const token = runRef.current;
            clearTimer();
            // A late transcript still lands in the typed field.
            timerRef.current = setTimeout(() => {
              if (runRef.current === token) {
                handlersRef.current.fallBackToText('आवाज़ समझने में देर हो रही है। कृपया लिखकर जवाब दें।');
              }
            }, ASR_TIMEOUT_MS);
          }
        },
        onSpeakingChange: (isSpeaking) => {
          if (isSpeaking) speakingStartedRef.current = true;
          setSpeaking(isSpeaking);
        },
        onSpeechStart: () => {
          // They're answering - the no-response timeout no longer applies.
          if (viewRef.current === 'voice') clearTimer();
        },
      },
    );
    controllerRef.current = controller;
    void ask('crop');

    return () => {
      runRef.current += 1;
      clearTimer();
      expectRecordingRef.current = false;
      controller.dispose();
      controllerRef.current = null;
    };
    // ask() only reads refs and the controller built here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [voiceConfig]);

  const cropMatch = normalizeCrop(answers.crop);
  const plantedDays = parsePlantedDaysAgo(answers.planted);

  const toggleFieldMic = (field: Field) => {
    const ctl = controllerRef.current;
    if (!ctl) return;
    if (micField) {
      ctl.stop();
      return;
    }
    runRef.current += 1;
    clearTimer();
    setNotice(null);
    setMicField(field);
    listenTargetRef.current = field;
    expectRecordingRef.current = true;
    ctl.start();
  };

  const openConfirm = () => {
    runRef.current += 1;
    clearTimer();
    goTo('confirm');
  };

  const confirm = () => {
    if (!cropMatch) return;
    const profile: FarmerProfile = {
      crop: cropMatch.crop,
      cropLabel: cropMatch.label,
      cropRaw: answers.crop.trim(),
      plantedAt: plantedDays === null ? null : plantedAtFromDaysAgo(plantedDays),
      plantedDaysAgo: plantedDays,
      confirmedAt: new Date().toISOString(),
    };
    saveFarmerProfile(profile);
    onComplete(profile);
  };

  const skip = () => {
    runRef.current += 1;
    clearTimer();
    expectRecordingRef.current = false;
    controllerRef.current?.dispose();
    onSkip();
  };

  const busyLabel = !voiceConfig
    ? 'तैयार हो रहा है…'
    : speaking
      ? 'सवाल सुनाया जा रहा है…'
      : recording
        ? 'सुन रहे हैं… बोलिए'
        : processing
          ? 'समझ रहे हैं…'
          : null;

  const answerPreview = (field: Field) => {
    if (field === 'crop') {
      if (!cropMatch) return null;
      return cropMatch.confident ? `फसल: ${cropMatch.label}` : 'यह फसल सूची में नहीं मिली - जैसा लिखा है वैसा रखा जाएगा';
    }
    if (!answers.planted.trim()) return null;
    return plantedDays === null ? 'समय समझ नहीं आया' : plantedLabel(plantedDays);
  };

  const skipButton = (
    <button
      onClick={skip}
      className="flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm text-textMuted transition-colors hover:bg-white/5 hover:text-textPrimary"
    >
      <SkipForward className="h-4 w-4" />
      छोड़ें
    </button>
  );

  return (
    <div className="flex flex-1 flex-col gap-4 animate-in fade-in" lang="hi">
      <div className="flex items-center gap-2 text-accent">
        <Sprout className="h-5 w-5" />
        <h3 className="font-sans text-lg text-textPrimary">आपकी फसल के बारे में दो सवाल</h3>
      </div>
      <p className="-mt-2 text-sm text-textMuted">इससे मौसम की जानकारी आपकी फसल के हिसाब से दी जा सकेगी।</p>

      {notice && (
        <div className="flex items-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-300">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{notice}</span>
        </div>
      )}

      {view === 'voice' && (
        <>
          {(['crop', 'planted'] as Field[])
            .filter((field) => field === 'crop' || step === 'planted')
            .map((field) => {
              const active = field === step;
              const preview = heard[field] ? answerPreview(field) : null;
              return (
                <div
                  key={field}
                  className={`rounded-2xl border px-4 py-3 transition-colors ${active ? 'border-accent/40 bg-accent/5' : 'border-white/10 bg-white/5'}`}
                >
                  <div className="flex items-start gap-3">
                    <span className="mt-1 text-xs font-mono text-textMuted">{field === 'crop' ? '1' : '2'}</span>
                    <div className="min-w-0 flex-1">
                      <p className="text-base text-textPrimary sm:text-lg">{QUESTIONS[field]}</p>
                      {active && busyLabel && (
                        <div className="mt-2 flex items-center gap-2 text-sm text-accent">
                          {recording ? (
                            <span className="relative flex h-3 w-3">
                              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-alertRed opacity-75" />
                              <span className="relative inline-flex h-3 w-3 rounded-full bg-alertRed" />
                            </span>
                          ) : speaking ? (
                            <Volume2 className="h-4 w-4" />
                          ) : (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          )}
                          <span>{busyLabel}</span>
                        </div>
                      )}
                      {heard[field] && answers[field] && (
                        <div className="mt-2 text-sm">
                          <span className="text-textMuted">आपने कहा: </span>
                          <span className="text-textPrimary">“{answers[field]}”</span>
                          {preview && <div className="mt-0.5 text-emerald-400">→ {preview}</div>}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}

          <div className="mt-auto flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => void ask(step)}
                disabled={!voiceConfig || speaking || recording || processing}
                className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-2 text-sm text-textPrimary transition-colors hover:bg-white/5 disabled:opacity-50"
              >
                <Mic className="h-4 w-4" />
                फिर से पूछें
              </button>
              <button
                onClick={() => fallBackToText('')}
                className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-2 text-sm text-textPrimary transition-colors hover:bg-white/5"
              >
                <Keyboard className="h-4 w-4" />
                लिखकर बताएं
              </button>
            </div>
            {skipButton}
          </div>
        </>
      )}

      {view === 'form' && (
        <>
          {(['crop', 'planted'] as Field[]).map((field) => {
            const preview = answerPreview(field);
            const listening = micField === field;
            return (
              <div key={field} className="rounded-2xl border border-white/10 bg-white/5 px-4 py-3">
                <label htmlFor={`onboarding-${field}`} className="block text-base text-textPrimary sm:text-lg">
                  {QUESTIONS[field]}
                </label>
                <div className="relative mt-2 flex items-center">
                  <input
                    id={`onboarding-${field}`}
                    type="text"
                    value={answers[field]}
                    onChange={(e) => setAnswers((prev) => ({ ...prev, [field]: e.target.value }))}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && answers.crop.trim()) openConfirm();
                    }}
                    placeholder={PLACEHOLDERS[field]}
                    disabled={listening}
                    className="w-full rounded-xl border border-white/10 bg-black/30 py-3 pl-4 pr-12 text-base text-textPrimary placeholder:text-textMuted focus:border-accent/50 focus:outline-none"
                  />
                  {controllerRef.current?.supported && (
                    <button
                      type="button"
                      onClick={() => toggleFieldMic(field)}
                      disabled={(micField !== null && !listening) || processing}
                      title="बोलकर बताएं"
                      className={`absolute right-1.5 rounded-lg p-2 transition-colors disabled:opacity-40 ${listening ? 'bg-alertRed/20 text-alertRed' : 'text-textMuted hover:bg-white/5 hover:text-textPrimary'}`}
                    >
                      {listening && processing ? <Loader2 className="h-5 w-5 animate-spin" /> : <Mic className="h-5 w-5" />}
                    </button>
                  )}
                </div>
                {listening && (
                  <span className="mt-1.5 block text-sm text-alertRed">
                    {recording ? 'सुन रहे हैं… बोलिए' : processing ? 'समझ रहे हैं…' : 'माइक चालू हो रहा है…'}
                  </span>
                )}
                {preview && !listening && <span className="mt-1.5 block text-sm text-emerald-400">→ {preview}</span>}
              </div>
            );
          })}

          <div className="mt-auto flex items-center justify-between gap-2">
            <button
              onClick={openConfirm}
              disabled={!answers.crop.trim() || micField !== null}
              className="flex items-center gap-1.5 rounded-lg bg-accent/20 px-4 py-2.5 text-sm text-accent transition-colors hover:bg-accent/30 disabled:opacity-50"
            >
              <Check className="h-4 w-4" />
              आगे
            </button>
            {skipButton}
          </div>
        </>
      )}

      {view === 'confirm' && cropMatch && (
        <>
          <div className="rounded-2xl border border-accent/30 bg-accent/5 px-4 py-4">
            <p className="mb-3 text-base text-textPrimary sm:text-lg">क्या यह सही है?</p>
            <dl className="space-y-2 text-base">
              <div className="flex gap-2">
                <dt className="text-textMuted">फसल:</dt>
                <dd className="text-textPrimary">{cropMatch.label}</dd>
              </div>
              {cropMatch.confident && cropMatch.label !== answers.crop.trim() && (
                <p className="-mt-1 text-xs text-textMuted">आपने कहा: “{answers.crop.trim()}”</p>
              )}
              {!cropMatch.confident && (
                <p className="text-sm text-amber-300">
                  यह नाम हमारी फसल सूची में नहीं मिला। सही है तो ऐसे ही रखें, नहीं तो “बदलें” दबाकर फिर से बताएं।
                </p>
              )}
              <div className="flex gap-2">
                <dt className="text-textMuted">बुवाई:</dt>
                <dd className="text-textPrimary">{plantedLabel(plantedDays)}</dd>
              </div>
              {plantedDays === null && (
                <p className="text-sm text-amber-300">
                  समय समझ नहीं आया{answers.planted.trim() ? ` (“${answers.planted.trim()}”)` : ''}। “बदलें” दबाकर फिर से बताएं, या ऐसे ही आगे बढ़ें।
                </p>
              )}
            </dl>
          </div>

          <div className="mt-auto flex flex-wrap items-center gap-2">
            <button
              onClick={confirm}
              className="flex items-center gap-1.5 rounded-lg bg-emerald-500/20 px-4 py-2.5 text-base text-emerald-300 transition-colors hover:bg-emerald-500/30"
            >
              <Check className="h-4 w-4" />
              सही है
            </button>
            <button
              onClick={() => {
                setNotice(null);
                goTo('form');
              }}
              className="flex items-center gap-1.5 rounded-lg border border-white/10 px-4 py-2.5 text-base text-textPrimary transition-colors hover:bg-white/5"
            >
              <Pencil className="h-4 w-4" />
              बदलें
            </button>
          </div>
        </>
      )}
    </div>
  );
}
