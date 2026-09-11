// WeatherGPT frontend: mic capture + TTS playback, backend calls,
// explicit success/failure handling.
//
// Voice provider is a swappable interface (spec Section 7): the browser's
// Web Speech API by default, or Bhashini's ASR/TTS pipeline when the
// backend is configured with VOICE_PROVIDER=bhashini (checked once at
// startup via GET /voice/config - Bhashini credentials never reach the
// browser, so all Bhashini calls are proxied through backend endpoints).
const DEFAULT_LANG = "en-IN"; // Web Speech (BCP-47) locale, used only in web_speech mode

const micBtn = document.getElementById("mic-btn");
const textInput = document.getElementById("text-input");
const sendBtn = document.getElementById("send-btn");
const statusEl = document.getElementById("status");
const transcriptEl = document.getElementById("transcript");
const answerCard = document.getElementById("answer-card");
const answerPathEl = document.getElementById("answer-path");
const answerVerifiedEl = document.getElementById("answer-verified");
const answerTextEl = document.getElementById("answer-text");
const errorBanner = document.getElementById("error-banner");

function setStatus(text) {
  if (!text) {
    statusEl.hidden = true;
    return;
  }
  statusEl.hidden = false;
  statusEl.textContent = text;
}

function showError(message) {
  answerCard.hidden = true;
  errorBanner.hidden = false;
  errorBanner.textContent = message;
  voiceProvider.speak([message]);
}

function showAnswer(result) {
  errorBanner.hidden = true;
  answerCard.hidden = false;
  answerPathEl.textContent = result.path === "fast" ? "Fast path" : "Reasoning path";
  answerVerifiedEl.textContent = result.verified ? "Verified" : "Unverified";
  answerVerifiedEl.className = "badge " + (result.verified ? "badge-ok" : "badge-warn");
  answerTextEl.textContent = result.answer;
  voiceProvider.speak(splitIntoSentences(result.answer));
}

function splitIntoSentences(text) {
  const parts = text.match(/[^.!?]+[.!?]*/g);
  return parts ? parts.map((s) => s.trim()).filter(Boolean) : [text];
}

async function askBackend(text, inputMode) {
  transcriptEl.hidden = false;
  transcriptEl.textContent = `You asked: "${text}"`;
  setStatus("Thinking...");

  try {
    const response = await fetch("/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, input_mode: inputMode }),
    });

    const data = await response.json();
    setStatus(null);

    // Per spec Section 9: the failure shape must be handled explicitly -
    // never silently show nothing.
    if (data.error || !data.answer) {
      showError(data.error || "Something went wrong and I don't have a reliable answer.");
      return;
    }

    showAnswer(data);
  } catch (err) {
    setStatus(null);
    showError("I couldn't reach the WeatherGPT backend. Please check your connection and try again.");
  }
}

sendBtn.addEventListener("click", () => {
  const text = textInput.value.trim();
  if (text) askBackend(text, "text");
});

textInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendBtn.click();
});

// ---------------------------------------------------------------------
// Voice providers
// ---------------------------------------------------------------------

/** Browser-native Web Speech API. Zero backend config needed. */
const webSpeechProvider = {
  name: "web_speech",
  recognition: null,

  init() {
    const SpeechRecognitionImpl = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognitionImpl) {
      micBtn.disabled = true;
      micBtn.title = "Voice input isn't supported in this browser - please type your question.";
      return;
    }
    this.recognition = new SpeechRecognitionImpl();
    this.recognition.lang = DEFAULT_LANG;
    this.recognition.interimResults = false;
    this.recognition.maxAlternatives = 1;

    this.recognition.addEventListener("result", (event) => {
      const text = event.results[0][0].transcript;
      askBackend(text, "voice");
    });
    this.recognition.addEventListener("error", () => {
      setStatus(null);
      showError("I couldn't hear that clearly. Please try again or type your question.");
    });

    micBtn.addEventListener("click", () => {
      setStatus("Listening...");
      this.recognition.start();
    });
  },

  speak(sentences) {
    if (!("speechSynthesis" in window)) return;
    window.speechSynthesis.cancel();
    for (const sentence of sentences) {
      const utterance = new SpeechSynthesisUtterance(sentence);
      utterance.lang = DEFAULT_LANG;
      window.speechSynthesis.speak(utterance);
    }
  },
};

/**
 * Bhashini-backed provider. The browser only ever talks to this
 * project's own backend (/voice/asr, /voice/tts) - Bhashini's
 * userID/ulcaApiKey stay server-side. Records raw mic audio and encodes
 * it as WAV client-side (Bhashini's ASR expects wav/flac/mp3, not the
 * webm/opus that MediaRecorder produces by default), since there's no
 * external library dependency in this project.
 */
const bhashiniProvider = {
  name: "bhashini",
  language: "hi",
  recording: false,
  MAX_RECORDING_MS: 15000,

  init(language) {
    this.language = language || this.language;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      micBtn.disabled = true;
      micBtn.title = "Microphone access isn't supported in this browser - please type your question.";
      return;
    }
    micBtn.addEventListener("click", () => {
      if (this.recording) {
        this.stopRecording();
      } else {
        this.startRecording();
      }
    });
  },

  async startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const AudioContextImpl = window.AudioContext || window.webkitAudioContext;
      const audioCtx = new AudioContextImpl();
      const source = audioCtx.createMediaStreamSource(stream);
      const processor = audioCtx.createScriptProcessor(4096, 1, 1);
      const silentGain = audioCtx.createGain();
      silentGain.gain.value = 0; // avoid echoing the mic straight to speakers

      const chunks = [];
      processor.onaudioprocess = (e) => {
        chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      };
      source.connect(processor);
      processor.connect(silentGain);
      silentGain.connect(audioCtx.destination);

      this.recording = true;
      this._session = { stream, audioCtx, source, processor, silentGain, chunks };
      micBtn.textContent = "⏹";
      setStatus("Recording... click the mic again to stop (max 15s)");

      this._autoStopTimer = setTimeout(() => this.stopRecording(), this.MAX_RECORDING_MS);
    } catch (err) {
      setStatus(null);
      showError("I couldn't access your microphone. Please check permissions and try again.");
    }
  },

  stopRecording() {
    if (!this.recording || !this._session) return;
    clearTimeout(this._autoStopTimer);
    this.recording = false;
    micBtn.textContent = "🎤";
    setStatus("Processing speech...");

    const { stream, audioCtx, source, processor, silentGain, chunks } = this._session;
    source.disconnect();
    processor.disconnect();
    silentGain.disconnect();
    stream.getTracks().forEach((t) => t.stop());
    const sampleRate = audioCtx.sampleRate;
    audioCtx.close();
    this._session = null;

    const samples = this._mergeBuffers(chunks);
    const wavBuffer = this._encodeWavPCM16(samples, sampleRate);
    const blob = new Blob([wavBuffer], { type: "audio/wav" });

    const reader = new FileReader();
    reader.onloadend = () => {
      const audioBase64 = reader.result.split(",")[1];
      this._sendToASR(audioBase64, sampleRate);
    };
    reader.readAsDataURL(blob);
  },

  async _sendToASR(audioBase64, sampleRate) {
    try {
      const response = await fetch("/voice/asr", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          audio_base64: audioBase64,
          language: this.language,
          audio_format: "wav",
          sampling_rate: sampleRate,
        }),
      });
      const data = await response.json();
      setStatus(null);
      if (data.error || !data.transcript) {
        showError(data.error || "I couldn't understand that. Please try again or type your question.");
        return;
      }
      askBackend(data.transcript, "voice");
    } catch (err) {
      setStatus(null);
      showError("I couldn't reach the speech recognition service. Please try again or type your question.");
    }
  },

  async speak(sentences) {
    for (const sentence of sentences) {
      try {
        const response = await fetch("/voice/tts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: sentence, language: this.language }),
        });
        const data = await response.json();
        if (data.error || !data.audio_base64) continue; // don't block remaining sentences on one TTS failure
        await this._playBase64Audio(data.audio_base64, data.audio_format || "wav");
      } catch (err) {
        // Speech is a nice-to-have on top of the already-displayed text answer -
        // a TTS failure here must never block or hide the text response.
      }
    }
  },

  _playBase64Audio(base64, format) {
    return new Promise((resolve) => {
      const audio = new Audio(`data:audio/${format};base64,${base64}`);
      audio.addEventListener("ended", resolve);
      audio.addEventListener("error", resolve);
      audio.play().catch(resolve);
    });
  },

  _mergeBuffers(chunks) {
    const length = chunks.reduce((sum, c) => sum + c.length, 0);
    const result = new Float32Array(length);
    let offset = 0;
    for (const chunk of chunks) {
      result.set(chunk, offset);
      offset += chunk.length;
    }
    return result;
  },

  _encodeWavPCM16(samples, sampleRate) {
    const numChannels = 1;
    const bytesPerSample = 2;
    const blockAlign = numChannels * bytesPerSample;
    const byteRate = sampleRate * blockAlign;
    const dataSize = samples.length * bytesPerSample;
    const buffer = new ArrayBuffer(44 + dataSize);
    const view = new DataView(buffer);

    const writeString = (offset, str) => {
      for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
    };

    writeString(0, "RIFF");
    view.setUint32(4, 36 + dataSize, true);
    writeString(8, "WAVE");
    writeString(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true); // PCM
    view.setUint16(22, numChannels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, byteRate, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, bytesPerSample * 8, true);
    writeString(36, "data");
    view.setUint32(40, dataSize, true);

    let offset = 44;
    for (let i = 0; i < samples.length; i++, offset += 2) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return buffer;
  },
};

let voiceProvider = webSpeechProvider;

async function initVoiceProvider() {
  try {
    const response = await fetch("/voice/config");
    const config = await response.json();
    // Both "bhashini" and "google" are server-proxied ASR/TTS (record WAV
    // client-side, POST to /voice/asr and /voice/tts) - the same client
    // flow works for either, since the backend swaps the actual provider.
    if (config.provider === "bhashini" || config.provider === "google") {
      voiceProvider = bhashiniProvider;
      voiceProvider.init(config.language);
      return;
    }
  } catch (err) {
    // Backend voice config unreachable - fall through to the default,
    // zero-config Web Speech provider rather than leaving voice input dead.
  }
  voiceProvider = webSpeechProvider;
  voiceProvider.init();
}

initVoiceProvider();
