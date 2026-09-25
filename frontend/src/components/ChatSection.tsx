import { useState, forwardRef, useRef, useEffect } from 'react';
import { Mic, Send, ShieldCheck, Activity, ChevronDown, ChevronUp, AlertCircle, ArrowLeft, Trash2, Sprout } from 'lucide-react';
import type { Persona } from '../App';
import { fetchWeatherQuery, type QueryResponse, type QueryError } from '../lib/api';
import { createVoiceController, fetchVoiceConfig, type VoiceConfig, type VoiceController } from '../lib/voice';
import {
  daysSincePlanted,
  loadFarmerProfile,
  markOnboardingSkipped,
  plantedLabel,
  toCropContext,
  wasOnboardingSkipped,
  type FarmerProfile,
} from '../lib/farmerProfile';
import { FarmerOnboarding } from './FarmerOnboarding';

interface ChatSectionProps {
  activePersona: Persona;
  onBack: () => void;
}

type ChatMessage =
  | { id: string; role: 'user'; text: string }
  | { id: string; role: 'assistant'; response: QueryResponse }
  | { id: string; role: 'error'; text: string };

const HISTORY_KEY = 'mausam-gpt-chat-history';
// Each answer carries its raw forecast JSON, so cap what localStorage holds.
const MAX_HISTORY = 60;

function loadHistory(): ChatMessage[] {
  try {
    const saved = JSON.parse(localStorage.getItem(HISTORY_KEY) ?? '[]');
    return Array.isArray(saved) ? saved : [];
  } catch {
    return [];
  }
}

function newId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function AssistantMessage({ response }: { response: QueryResponse }) {
  const [showTelemetry, setShowTelemetry] = useState(false);
  return (
    <div className="max-w-[90%] self-start rounded-2xl rounded-bl-sm border border-white/10 bg-white/5 px-4 py-3 animate-in fade-in slide-in-from-bottom-2">
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] font-mono uppercase sm:text-xs">
        <span className={`px-2 py-0.5 rounded-sm ${response.path === 'fast' ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' : 'bg-sky-500/20 text-sky-400 border border-sky-500/30'}`}>
          {response.path} Path
        </span>
        {response.verified && (
          <span className="flex items-center space-x-1 text-emerald-400">
            <ShieldCheck className="w-3 h-3" />
            <span>Verified Grounded Data</span>
          </span>
        )}
        <span className="text-textMuted ml-auto">{response.latency_ms}ms</span>
      </div>

      <div className="text-base sm:text-lg font-sans text-textPrimary leading-relaxed">{response.answer}</div>

      {response.source_data && (
        <div className="mt-3 border-t border-white/5 pt-3">
          <button
            onClick={() => setShowTelemetry(!showTelemetry)}
            className="flex items-center justify-between w-full text-xs font-mono text-textMuted hover:text-textPrimary transition-colors"
          >
            <span className="flex items-center space-x-2">
              <Activity className="w-4 h-4" />
              <span>Raw Open-Meteo Telemetry</span>
            </span>
            {showTelemetry ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </button>
          {showTelemetry && (
            <div className="mt-3 bg-black/40 rounded-lg p-3 font-mono text-xs text-textMuted overflow-x-auto border border-white/5">
              <pre>{JSON.stringify(response.source_data, null, 2)}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export const ChatSection = forwardRef<HTMLElement, ChatSectionProps>(({ activePersona, onBack }, ref) => {
  const [inputText, setInputText] = useState('');
  const [isRecording, setIsRecording] = useState(false);
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>(loadHistory);
  const [isFullScreen, setIsFullScreen] = useState(false);
  const logRef = useRef<HTMLDivElement | null>(null);
  const [voiceConfig, setVoiceConfig] = useState<VoiceConfig | null>(null);

  // Farmer onboarding: asked once, until answered; a skip holds for the session.
  const [farmerProfile, setFarmerProfile] = useState<FarmerProfile | null>(loadFarmerProfile);
  const [onboardingSkipped, setOnboardingSkipped] = useState(wasOnboardingSkipped);
  const [editingCrop, setEditingCrop] = useState(false);
  const isFarmer = activePersona === 'farmer';
  const showOnboarding = isFarmer && (editingCrop || (!farmerProfile && !onboardingSkipped));

  const addMessage = (message: ChatMessage) => setMessages((prev) => [...prev, message]);

  useEffect(() => {
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(messages.slice(-MAX_HISTORY)));
    } catch {
      // Storage full or disabled - the chat still works, it just won't persist.
    }
  }, [messages]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, loading]);

  const voiceRef = useRef<VoiceController | null>(null);
  // The controller is built once, but its callbacks must reach the latest
  // handleQuery, which is redefined on every render.
  const handleQueryRef = useRef<(text: string, mode?: 'text' | 'voice') => void>(() => {});

  useEffect(() => {
    if (activePersona) {
      const timer = setTimeout(() => {
        setIsFullScreen(true);
      }, 1500);
      return () => clearTimeout(timer);
    } else {
      setIsFullScreen(false);
    }
  }, [activePersona]);

  useEffect(() => {
    // Ask the backend which voice flow to use (browser-native Web Speech,
    // or Bhashini/Gemini proxied through /voice/asr and /voice/tts), then
    // build the matching controller.
    let disposed = false;
    let controller: VoiceController | null = null;

    fetchVoiceConfig().then((config) => {
      if (disposed) return;
      setVoiceConfig(config);
      controller = createVoiceController(config, {
        onTranscript: (transcript) => handleQueryRef.current(transcript, 'voice'),
        onError: (message) => addMessage({ id: newId(), role: 'error', text: message }),
        onRecordingChange: setIsRecording,
        onProcessingChange: setLoading,
      });
      voiceRef.current = controller;
    });

    return () => {
      disposed = true;
      controller?.dispose();
      voiceRef.current = null;
    };
  }, []);

  const toggleRecording = () => {
    if (isRecording) {
      voiceRef.current?.stop();
    } else {
      voiceRef.current?.start();
    }
  };

  const speakResponse = (text: string) => {
    void voiceRef.current?.speak(text);
  };

  const handleQuery = async (text: string, mode: 'text'|'voice' = 'text') => {
    if (!text.trim()) return;
    setInputText('');
    addMessage({ id: newId(), role: 'user', text: text.trim() });
    setLoading(true);

    try {
      const res = await fetchWeatherQuery({
        text,
        input_mode: mode,
        crop_context: isFarmer && farmerProfile ? toCropContext(farmerProfile) : undefined,
      });
      addMessage({ id: newId(), role: 'assistant', response: res });
      if (mode === 'voice') {
        speakResponse(res.answer);
      }
    } catch (err) {
      addMessage({ id: newId(), role: 'error', text: (err as QueryError).error });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    handleQueryRef.current = handleQuery;
  });

  return (
    <section ref={ref} className="w-full min-h-[100dvh] flex flex-col items-center justify-center py-16 sm:py-32 px-3 sm:px-4 z-10">
      <div className={`
        w-full bg-slate-900/60 backdrop-blur-xl px-4 sm:px-6 shadow-2xl transition-all duration-700 ease-in-out flex flex-col
        ${isFullScreen
          ? 'fixed inset-0 z-[100] rounded-none border-0 pt-[max(1rem,env(safe-area-inset-top))] pb-[max(1rem,env(safe-area-inset-bottom))]'
          : 'max-w-2xl rounded-2xl border border-white/10 relative py-4 sm:py-6'
        }
      `}>
        <div className={isFullScreen ? "max-w-4xl w-full mx-auto flex flex-col h-full flex-1" : "w-full flex flex-col"}>
          <div className="flex items-center justify-between gap-2 mb-4 sm:mb-8 border-b border-white/10 pb-3 sm:pb-4">
            <div className="flex items-center space-x-2 sm:space-x-3 min-w-0">
              <button
                onClick={onBack}
                className="p-2.5 -ml-1.5 sm:-ml-2 rounded-lg text-textMuted hover:text-textPrimary hover:bg-white/5 transition-colors shrink-0"
                title="Go Back"
              >
                <ArrowLeft className="w-5 h-5" />
              </button>
              <h2 className="font-display text-lg sm:text-2xl text-textPrimary truncate">Mausam GPT</h2>
            </div>
          <div className="flex items-center gap-1 sm:gap-2 shrink-0">
            {messages.length > 0 && (
              <button
                onClick={() => setMessages([])}
                disabled={loading}
                className="p-2 rounded-lg text-textMuted hover:text-alertRed hover:bg-white/5 transition-colors disabled:opacity-50"
                title="Clear chat history"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            )}
            {activePersona && (
              <span className="text-[10px] sm:text-xs font-mono uppercase tracking-widest text-accent bg-accent/10 px-2 sm:px-3 py-1 rounded-full border border-accent/20 whitespace-nowrap">
                {activePersona} Lens
              </span>
            )}
          </div>
        </div>

        {showOnboarding ? (
          <FarmerOnboarding
            voiceConfig={voiceConfig}
            initial={farmerProfile}
            onComplete={(profile) => {
              setFarmerProfile(profile);
              setEditingCrop(false);
            }}
            onSkip={() => {
              markOnboardingSkipped();
              setOnboardingSkipped(true);
              setEditingCrop(false);
            }}
          />
        ) : (
        <>
        {isFarmer && (
          <div lang="hi" className="mb-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-textMuted">
            <Sprout className="h-4 w-4 text-emerald-400" />
            {farmerProfile ? (
              <span>
                फसल: <span className="text-textPrimary">{farmerProfile.cropLabel}</span> · {plantedLabel(daysSincePlanted(farmerProfile))}
              </span>
            ) : (
              <span>फसल नहीं जोड़ी गई</span>
            )}
            <button
              onClick={() => setEditingCrop(true)}
              disabled={loading || isRecording}
              className="text-accent underline-offset-2 hover:underline disabled:opacity-50"
            >
              {farmerProfile ? 'बदलें' : 'फसल जोड़ें'}
            </button>
          </div>
        )}

        {/* Chat Log / Response Area */}
        <div
          ref={logRef}
          data-lenis-prevent
          className={`mb-4 sm:mb-6 flex flex-col gap-3 overflow-y-auto ${isFullScreen ? 'flex-1' : 'min-h-[160px] sm:min-h-[200px] max-h-[60vh]'}`}
        >
          {messages.length === 0 && !loading && (
            <div className="text-center text-textMuted font-sans my-auto">
              Awaiting query...
            </div>
          )}

          {messages.map((m) => {
            if (m.role === 'user') {
              return (
                <div
                  key={m.id}
                  className="max-w-[85%] self-end rounded-2xl rounded-br-sm bg-accent/15 border border-accent/25 px-4 py-2.5 text-base font-sans text-textPrimary whitespace-pre-wrap break-words animate-in fade-in slide-in-from-bottom-2"
                >
                  {m.text}
                </div>
              );
            }
            if (m.role === 'error') {
              return (
                <div
                  key={m.id}
                  className="max-w-[90%] self-start bg-alertRed/20 border border-alertRed text-alertRed px-4 py-3 rounded-2xl rounded-bl-sm flex items-start space-x-3 animate-in fade-in slide-in-from-bottom-2"
                >
                  <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
                  <div className="font-mono text-sm leading-relaxed">{m.text}</div>
                </div>
              );
            }
            return <AssistantMessage key={m.id} response={m.response} />;
          })}

          {loading && (
            <div className="self-start rounded-2xl rounded-bl-sm border border-white/10 bg-white/5 px-4 py-3.5">
              <div className="animate-pulse flex space-x-2">
                <div className="w-2 h-2 bg-accent rounded-full"></div>
                <div className="w-2 h-2 bg-accent rounded-full animation-delay-200"></div>
                <div className="w-2 h-2 bg-accent rounded-full animation-delay-400"></div>
              </div>
            </div>
          )}
        </div>
        
        {/* Input Area */}
        <div className="relative flex items-center">
          <input 
            type="text" 
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleQuery(inputText, 'text');
            }}
            placeholder="Ask about the weather..."
            className="w-full bg-black/30 border border-white/10 rounded-xl py-3.5 sm:py-4 pl-4 pr-[5.5rem] sm:pr-24 text-base text-textPrimary placeholder:text-textMuted focus:outline-none focus:border-accent/50 transition-colors font-sans"
            disabled={isRecording || loading}
          />

          <div className="absolute right-1.5 sm:right-2 flex items-center space-x-1">
            <button
              onClick={toggleRecording}
              className={`p-2.5 rounded-lg transition-colors ${isRecording ? 'bg-alertRed/20 text-alertRed' : 'text-textMuted hover:text-textPrimary hover:bg-white/5'}`}
            >
              {isRecording ? (
                <div className="flex items-center justify-center space-x-1 h-5 w-5">
                  <span className="w-1 h-3 bg-alertRed rounded-full animate-bounce"></span>
                  <span className="w-1 h-4 bg-alertRed rounded-full animate-bounce" style={{animationDelay: '0.1s'}}></span>
                  <span className="w-1 h-2 bg-alertRed rounded-full animate-bounce" style={{animationDelay: '0.2s'}}></span>
                </div>
              ) : (
                <Mic className="w-5 h-5" />
              )}
            </button>
            <button
              onClick={() => handleQuery(inputText, 'text')}
              disabled={!inputText.trim() || loading || isRecording}
              className="p-2.5 rounded-lg text-textMuted hover:text-accent hover:bg-accent/10 transition-colors disabled:opacity-50"
            >
              <Send className="w-5 h-5" />
            </button>
          </div>
          </div>
        </>
        )}
        </div>
      </div>
    </section>
  );
});

ChatSection.displayName = 'ChatSection';
