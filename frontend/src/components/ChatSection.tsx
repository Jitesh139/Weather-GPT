import { useState, forwardRef, useRef, useEffect } from 'react';
import { Mic, Send, ShieldCheck, Activity, ChevronDown, ChevronUp, AlertCircle, ArrowLeft } from 'lucide-react';
import type { Persona } from '../App';
import { fetchWeatherQuery, type QueryResponse, type QueryError } from '../lib/api';
import { createVoiceController, fetchVoiceConfig, type VoiceController } from '../lib/voice';

interface ChatSectionProps {
  activePersona: Persona;
  onBack: () => void;
}

export const ChatSection = forwardRef<HTMLElement, ChatSectionProps>(({ activePersona, onBack }, ref) => {
  const [inputText, setInputText] = useState('');
  const [isRecording, setIsRecording] = useState(false);
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<QueryResponse | null>(null);
  const [error, setError] = useState<QueryError | null>(null);
  const [showTelemetry, setShowTelemetry] = useState(false);
  const [isFullScreen, setIsFullScreen] = useState(false);
  
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
      controller = createVoiceController(config, {
        onTranscript: (transcript) => {
          setInputText(transcript);
          handleQueryRef.current(transcript, 'voice');
        },
        onError: (message) => setError({ error: message, answer: null }),
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
      setError(null);
      setResponse(null);
      voiceRef.current?.start();
    }
  };

  const speakResponse = (text: string) => {
    voiceRef.current?.speak(text);
  };

  const handleQuery = async (text: string, mode: 'text'|'voice' = 'text') => {
    if (!text.trim()) return;
    setLoading(true);
    setError(null);
    setResponse(null);
    setShowTelemetry(false);
    
    try {
      const res = await fetchWeatherQuery({ text, input_mode: mode });
      setResponse(res);
      if (mode === 'voice') {
        speakResponse(res.answer);
      }
    } catch (err: any) {
      setError(err as QueryError);
    } finally {
      setLoading(false);
      if (mode === 'text') {
        setInputText('');
      }
    }
  };

  useEffect(() => {
    handleQueryRef.current = handleQuery;
  });

  return (
    <section ref={ref} className="w-full min-h-screen flex flex-col items-center justify-center py-32 px-4 z-10">
      <div className={`
        w-full bg-slate-900/60 backdrop-blur-xl p-6 shadow-2xl transition-all duration-700 ease-in-out flex flex-col
        ${isFullScreen 
          ? 'fixed inset-0 z-[100] rounded-none border-0' 
          : 'max-w-2xl rounded-2xl border border-white/10 relative'
        }
      `}>
        <div className={isFullScreen ? "max-w-4xl w-full mx-auto flex flex-col h-full flex-1" : "w-full flex flex-col"}>
          <div className="flex items-center justify-between mb-8 border-b border-white/10 pb-4">
            <div className="flex items-center space-x-3">
              <button 
                onClick={onBack}
                className="p-2 -ml-2 rounded-lg text-textMuted hover:text-textPrimary hover:bg-white/5 transition-colors"
                title="Go Back"
              >
                <ArrowLeft className="w-5 h-5" />
              </button>
              <h2 className="font-display text-2xl text-textPrimary">Mausam GPT</h2>
            </div>
          {activePersona && (
            <span className="text-xs font-mono uppercase tracking-widest text-accent bg-accent/10 px-3 py-1 rounded-full border border-accent/20">
              {activePersona} Lens
            </span>
          )}
        </div>
        
        {/* Chat Log / Response Area */}
        <div className={`mb-8 flex flex-col justify-end ${isFullScreen ? 'flex-1 overflow-y-auto' : 'min-h-[200px]'}`}>
          {!response && !error && !loading && (
            <div className="text-center text-textMuted font-sans my-auto">
              Awaiting query...
            </div>
          )}
          
          {loading && (
            <div className="flex justify-center items-center h-full">
              <div className="animate-pulse flex space-x-2">
                <div className="w-2 h-2 bg-accent rounded-full"></div>
                <div className="w-2 h-2 bg-accent rounded-full animation-delay-200"></div>
                <div className="w-2 h-2 bg-accent rounded-full animation-delay-400"></div>
              </div>
            </div>
          )}

          {error && (
            <div className="bg-alertRed/20 border border-alertRed text-alertRed p-4 rounded-xl flex items-start space-x-3 mb-4 animate-in fade-in slide-in-from-bottom-2">
              <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
              <div className="font-mono text-sm leading-relaxed">
                {error.error}
              </div>
            </div>
          )}

          {response && (
            <div className="flex flex-col space-y-4 animate-in fade-in slide-in-from-bottom-2">
              <div className="flex items-center space-x-3 text-xs font-mono uppercase">
                <span className={`px-2 py-1 rounded-sm ${response.path === 'fast' ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' : 'bg-sky-500/20 text-sky-400 border border-sky-500/30'}`}>
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
              
              <div className="text-lg font-sans text-textPrimary leading-relaxed">
                {response.answer}
              </div>

              <div className="mt-4 border-t border-white/5 pt-4">
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
            placeholder="Inquire about meteorological conditions..."
            className="w-full bg-black/30 border border-white/10 rounded-xl py-4 pl-4 pr-24 text-textPrimary placeholder:text-textMuted focus:outline-none focus:border-accent/50 transition-colors font-sans"
            disabled={isRecording || loading}
          />
          
          <div className="absolute right-2 flex items-center space-x-1">
            <button
              onClick={toggleRecording}
              className={`p-2 rounded-lg transition-colors ${isRecording ? 'bg-alertRed/20 text-alertRed' : 'text-textMuted hover:text-textPrimary hover:bg-white/5'}`}
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
              className="p-2 rounded-lg text-textMuted hover:text-accent hover:bg-accent/10 transition-colors disabled:opacity-50"
            >
              <Send className="w-5 h-5" />
            </button>
          </div>
          </div>
        </div>
      </div>
    </section>
  );
});

ChatSection.displayName = 'ChatSection';
