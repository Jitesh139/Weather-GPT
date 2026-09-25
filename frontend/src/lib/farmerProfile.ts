// Farmer onboarding data: the crop they grow and when it was planted, kept
// in localStorage and sent with every farmer query as `crop_context`.
//
// Also holds the parsing for what the farmer says: crop names in Hindi,
// English, Hinglish or a regional word, and relative planting times like
// "15 दिन पहले" or "डेढ़ महीना पहले".
import type { CropContext } from './api';

export interface FarmerProfile {
  /** Canonical English crop name ("wheat"), or the farmer's own words when
   * nothing in the crop list matched. */
  crop: string;
  /** Display name in Hindi ("गेहूं"). */
  cropLabel: string;
  /** Exactly what was heard or typed. */
  cropRaw: string;
  /** ISO date (yyyy-mm-dd), or null when the farmer didn't give a usable time. */
  plantedAt: string | null;
  /** Days ago as of confirmedAt - plantedAt is what stays accurate over time. */
  plantedDaysAgo: number | null;
  confirmedAt: string;
}

const PROFILE_KEY = 'mausam-gpt-farmer-profile';
// sessionStorage, not localStorage: a skip lasts for this visit only.
const SKIPPED_KEY = 'mausam-gpt-farmer-onboarding-skipped';

export function loadFarmerProfile(): FarmerProfile | null {
  try {
    const saved = JSON.parse(localStorage.getItem(PROFILE_KEY) ?? 'null');
    return saved && typeof saved.crop === 'string' && saved.crop ? (saved as FarmerProfile) : null;
  } catch {
    return null;
  }
}

export function saveFarmerProfile(profile: FarmerProfile): void {
  try {
    localStorage.setItem(PROFILE_KEY, JSON.stringify(profile));
  } catch {
    // Storage full or disabled - the profile still applies for this visit.
  }
}

export function wasOnboardingSkipped(): boolean {
  try {
    return sessionStorage.getItem(SKIPPED_KEY) === '1';
  } catch {
    return false;
  }
}

export function markOnboardingSkipped(): void {
  try {
    sessionStorage.setItem(SKIPPED_KEY, '1');
  } catch {
    // ignore - the skip still holds in memory for this page
  }
}

const DAY_MS = 24 * 60 * 60 * 1000;

function startOfToday(): Date {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

function toIsoDate(date: Date): string {
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${m}-${d}`;
}

export function plantedAtFromDaysAgo(daysAgo: number): string {
  return toIsoDate(new Date(startOfToday().getTime() - daysAgo * DAY_MS));
}

export function daysSincePlanted(profile: FarmerProfile): number | null {
  if (!profile.plantedAt) return profile.plantedDaysAgo;
  const [y, m, d] = profile.plantedAt.split('-').map(Number);
  const planted = new Date(y, m - 1, d);
  return Math.max(0, Math.round((startOfToday().getTime() - planted.getTime()) / DAY_MS));
}

export function toCropContext(profile: FarmerProfile): CropContext {
  // Match the backend's limits (models/schemas.py:CropContext) so a long
  // as-typed crop or an old profile can't get the whole query rejected.
  const days = daysSincePlanted(profile);
  return { crop: profile.crop.slice(0, 60), planted_days_ago: days === null ? null : Math.min(days, 730) };
}

/** "3 सप्ताह पहले लगाई" */
export function plantedLabel(daysAgo: number | null): string {
  if (daysAgo === null) return 'बुवाई का समय पता नहीं';
  if (daysAgo === 0) return 'आज लगाई';
  if (daysAgo === 1) return 'कल लगाई';
  if (daysAgo < 14) return `${daysAgo} दिन पहले लगाई`;
  // Weeks up to two months, except whole months ("एक महीना पहले" -> 30), which read as months.
  if (daysAgo < 60 && daysAgo % 30 !== 0) return `${Math.round(daysAgo / 7)} सप्ताह पहले लगाई`;
  if (daysAgo < 365) {
    const months = Math.round(daysAgo / 30);
    return `${months} ${months === 1 ? 'महीना' : 'महीने'} पहले लगाई`;
  }
  const years = Math.round(daysAgo / 365);
  return `${years} साल पहले लगाई`;
}

// ---------------------------------------------------------------------
// Crop names
// ---------------------------------------------------------------------

interface CropEntry {
  crop: string;
  label: string;
  aliases: string[];
}

// Hindi (with common spelling variants), English, Hinglish, and regional
// names. Spelling variants of the same word don't all need listing - the
// fuzzy match below absorbs small differences.
const CROPS: CropEntry[] = [
  { crop: 'wheat', label: 'गेहूं', aliases: ['गेहूं', 'गेहूँ', 'गेंहू', 'गेहु', 'कनक', 'गहू', 'wheat', 'gehun', 'gehu', 'gehoon', 'genhu', 'kanak', 'gahu'] },
  { crop: 'rice', label: 'धान', aliases: ['धान', 'चावल', 'भात', 'पैडी', 'rice', 'paddy', 'dhan', 'dhaan', 'chawal', 'chaval', 'bhat', 'vari', 'nellu'] },
  { crop: 'soybean', label: 'सोयाबीन', aliases: ['सोयाबीन', 'सोया', 'soybean', 'soyabean', 'soya', 'soy'] },
  { crop: 'cotton', label: 'कपास', aliases: ['कपास', 'नरमा', 'रुई', 'कापूस', 'cotton', 'kapas', 'kapaas', 'narma', 'kapus'] },
  { crop: 'sugarcane', label: 'गन्ना', aliases: ['गन्ना', 'गन्ने', 'ईख', 'ऊख', 'ऊस', 'sugarcane', 'sugar cane', 'ganna', 'ganne', 'ikh', 'ookh', 'oos', 'cane'] },
  { crop: 'maize', label: 'मक्का', aliases: ['मक्का', 'मक्की', 'भुट्टा', 'मकई', 'maize', 'corn', 'makka', 'makki', 'makai', 'bhutta'] },
  { crop: 'gram', label: 'चना', aliases: ['चना', 'चने', 'हरभरा', 'छोले', 'gram', 'chickpea', 'chana', 'channa', 'harbhara', 'bengal gram'] },
  { crop: 'mustard', label: 'सरसों', aliases: ['सरसों', 'सरसो', 'राई', 'तोरिया', 'mustard', 'sarson', 'sarso', 'rai', 'toriya', 'rapeseed'] },
  { crop: 'groundnut', label: 'मूंगफली', aliases: ['मूंगफली', 'मुंगफली', 'शेंगदाणा', 'groundnut', 'peanut', 'moongfali', 'mungfali', 'shengdana'] },
  { crop: 'pearl millet', label: 'बाजरा', aliases: ['बाजरा', 'बाजरी', 'bajra', 'bajri', 'pearl millet', 'millet'] },
  { crop: 'sorghum', label: 'ज्वार', aliases: ['ज्वार', 'जवार', 'jowar', 'jwar', 'sorghum', 'jola'] },
  { crop: 'barley', label: 'जौ', aliases: ['जौ', 'barley', 'jau', 'jav'] },
  { crop: 'pigeon pea', label: 'अरहर', aliases: ['अरहर', 'तुअर', 'तूर', 'arhar', 'tur', 'toor', 'tuvar', 'pigeon pea'] },
  { crop: 'green gram', label: 'मूंग', aliases: ['मूंग', 'मुंग', 'moong', 'mung', 'green gram'] },
  { crop: 'black gram', label: 'उड़द', aliases: ['उड़द', 'उरद', 'urad', 'udad', 'black gram'] },
  { crop: 'lentil', label: 'मसूर', aliases: ['मसूर', 'masoor', 'masur', 'lentil'] },
  { crop: 'pea', label: 'मटर', aliases: ['मटर', 'matar', 'mutter', 'pea', 'peas'] },
  { crop: 'potato', label: 'आलू', aliases: ['आलू', 'aloo', 'alu', 'potato'] },
  { crop: 'onion', label: 'प्याज', aliases: ['प्याज', 'प्याज़', 'कांदा', 'onion', 'pyaz', 'pyaj', 'kanda'] },
  { crop: 'tomato', label: 'टमाटर', aliases: ['टमाटर', 'tomato', 'tamatar'] },
  { crop: 'chilli', label: 'मिर्च', aliases: ['मिर्च', 'मिर्ची', 'chilli', 'chili', 'mirch', 'mirchi'] },
  { crop: 'sunflower', label: 'सूरजमुखी', aliases: ['सूरजमुखी', 'sunflower', 'surajmukhi'] },
  { crop: 'jute', label: 'जूट', aliases: ['जूट', 'पटसन', 'jute', 'patsan'] },
  { crop: 'banana', label: 'केला', aliases: ['केला', 'केले', 'banana', 'kela'] },
  { crop: 'tea', label: 'चाय', aliases: ['चाय', 'tea', 'chai'] },
];

/** Lowercase, strip punctuation, and drop the Devanagari marks ASR output
 * is least consistent about (nasalisation, nukta), so "गेहूँ" and "गेहू"
 * compare equal. */
function normalize(text: string): string {
  return text
    .normalize('NFC') // also splits precomposed nukta letters (ढ़) so the nukta can go
    .toLowerCase()
    .replace(/[ँं़]/g, '') // chandrabindu, anusvara, nukta
    .replace(/[^\p{L}\p{M}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function levenshtein(a: string, b: string): number {
  const prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    let diag = prev[0];
    prev[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const tmp = prev[j];
      prev[j] = Math.min(prev[j] + 1, prev[j - 1] + 1, diag + (a[i - 1] === b[j - 1] ? 0 : 1));
      diag = tmp;
    }
  }
  return prev[b.length];
}

function similarity(a: string, b: string): number {
  return 1 - levenshtein(a, b) / Math.max(a.length, b.length);
}

const FUZZY_THRESHOLD = 0.75;

export interface CropMatch {
  crop: string;
  label: string;
  /** false when nothing matched and the farmer's own words are used as-is. */
  confident: boolean;
}

export function normalizeCrop(raw: string): CropMatch | null {
  const text = normalize(raw);
  if (!text) return null;
  const padded = ` ${text} `;

  // Exact word/phrase hits first, longest alias first, so "moongfali"
  // isn't taken as "moong".
  const aliases = CROPS.flatMap((entry) => entry.aliases.map((alias) => ({ entry, alias: normalize(alias) })))
    .sort((a, b) => b.alias.length - a.alias.length);
  for (const { entry, alias } of aliases) {
    if (padded.includes(` ${alias} `)) return { crop: entry.crop, label: entry.label, confident: true };
  }

  // Then fuzzy, word by word, for misheard or misspelt names. Very short
  // aliases ("jau", "rai") are skipped here: one wrong letter in three
  // is too loose to trust.
  let best: { entry: CropEntry; score: number } | null = null;
  for (const word of text.split(' ')) {
    for (const { entry, alias } of aliases) {
      if (alias.length < 4 || alias.includes(' ')) continue;
      const score = similarity(word, alias);
      if (score >= FUZZY_THRESHOLD && (!best || score > best.score)) best = { entry, score };
    }
  }
  if (best) return { crop: best.entry.crop, label: best.entry.label, confident: true };

  const asIs = raw.trim().replace(/[।.!?]+$/u, '');
  return { crop: asIs, label: asIs, confident: false };
}

// ---------------------------------------------------------------------
// Planting time
// ---------------------------------------------------------------------

const NUMBER_WORDS: Record<string, number> = {
  // Hindi
  'आधा': 0.5, 'आधे': 0.5, 'एक': 1, 'दो': 2, 'तीन': 3, 'चार': 4, 'पांच': 5, 'पाँच': 5, 'छह': 6, 'छः': 6, 'छे': 6,
  'सात': 7, 'आठ': 8, 'नौ': 9, 'दस': 10, 'ग्यारह': 11, 'बारह': 12, 'तेरह': 13, 'चौदह': 14,
  'पंद्रह': 15, 'पद्रह': 15, 'पन्द्रह': 15, 'सोलह': 16, 'सत्रह': 17, 'अठारह': 18, 'उन्नीस': 19,
  'बीस': 20, 'इक्कीस': 21, 'बाईस': 22, 'पच्चीस': 25, 'तीस': 30, 'पैंतीस': 35, 'पैतीस': 35,
  'चालीस': 40, 'पैंतालीस': 45, 'पैतालीस': 45, 'पचास': 50, 'साठ': 60, 'सत्तर': 70, 'अस्सी': 80,
  'नब्बे': 90, 'सौ': 100, 'डेढ़': 1.5, 'ढाई': 2.5, 'सवा': 1.25,
  // Hinglish
  aadha: 0.5, ek: 1, do: 2, teen: 3, char: 4, chaar: 4, paanch: 5, panch: 5, chhe: 6, chah: 6,
  saat: 7, aath: 8, nau: 9, das: 10, gyarah: 11, barah: 12, pandrah: 15, bees: 20, pachees: 25,
  tees: 30, chalees: 40, pachas: 50, dedh: 1.5, dhai: 2.5,
  // English
  half: 0.5, a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8,
  nine: 9, ten: 10, eleven: 11, twelve: 12, fifteen: 15, twenty: 20, thirty: 30, forty: 40, fifty: 50,
};

// Longest first so "हफ्ते" is found before a shorter overlapping form.
const UNITS: { words: string[]; days: number }[] = [
  { words: ['दिन', 'din', 'day', 'days'], days: 1 },
  { words: ['हफ्ते', 'हफ्ता', 'हफ्तों', 'हफते', 'हफता', 'सप्ताह', 'hafte', 'hafta', 'week', 'weeks'], days: 7 },
  { words: ['महीने', 'महीना', 'महीनों', 'महिने', 'महिना', 'माह', 'mahine', 'mahina', 'month', 'months'], days: 30 },
  { words: ['साल', 'वर्ष', 'बरस', 'saal', 'sal', 'year', 'years'], days: 365 },
];

const MONTHS: Record<string, number> = {
  'जनवरी': 0, 'फरवरी': 1, 'मार्च': 2, 'अप्रैल': 3, 'अप्रल': 3, 'मई': 4, 'जून': 5, 'जुलाई': 6,
  'अगस्त': 7, 'सितंबर': 8, 'सितम्बर': 8, 'अक्टूबर': 9, 'अक्तूबर': 9, 'नवंबर': 10, 'नवम्बर': 10,
  'दिसंबर': 11, 'दिसम्बर': 11,
  january: 0, jan: 0, february: 1, feb: 1, march: 2, mar: 2, april: 3, apr: 3, may: 4, june: 5, jun: 5,
  july: 6, jul: 6, august: 7, aug: 7, september: 8, sep: 8, sept: 8, october: 9, oct: 9,
  november: 10, nov: 10, december: 11, dec: 11,
};

function devanagariDigitsToAscii(text: string): string {
  return text.replace(/[०-९]/g, (d) => String(d.charCodeAt(0) - 0x0966));
}

/** Parses "15 दिन पहले", "एक महीना पहले", "2 हफ्ते पहले", "डेढ़ महीना",
 * "साढ़े तीन हफ्ते", "कल", "15 अक्टूबर" and similar into days ago.
 * Returns null when no time can be found. */
export function parsePlantedDaysAgo(raw: string): number | null {
  const text = normalize(devanagariDigitsToAscii(raw));
  if (!text) return null;
  // normalize() drops anusvara/nukta, so look words up the same way.
  const words = text.split(' ');
  const numberWords = new Map(Object.entries(NUMBER_WORDS).map(([k, v]) => [normalize(k), v]));
  const unitOf = (word: string) => UNITS.find((u) => u.words.some((w) => normalize(w) === word));

  // A calendar date: "15 अक्टूबर", "october 15".
  for (let i = 0; i < words.length; i++) {
    const month = MONTHS[words[i]];
    if (month === undefined) continue;
    const day = Number(words[i - 1]) || Number(words[i + 1]) || 1;
    const today = startOfToday();
    let date = new Date(today.getFullYear(), month, day);
    if (date > today) date = new Date(today.getFullYear() - 1, month, day);
    return Math.round((today.getTime() - date.getTime()) / DAY_MS);
  }

  for (let i = 0; i < words.length; i++) {
    const unit = unitOf(words[i]);
    if (!unit) continue;

    // Walk back over the number in front of the unit: "15", "एक", "डेढ़",
    // "15-20" (read as the upper figure, since punctuation is stripped),
    // "साढ़े तीन" (three and a half), "सवा दो" (two and a quarter).
    let amount: number | null = null;
    const prev = words[i - 1];
    const prevPrev = words[i - 2];
    const numeric = (w?: string) => {
      if (!w) return null;
      if (/^\d+(\.\d+)?$/.test(w)) return Number(w);
      return numberWords.get(w) ?? null;
    };
    const n = numeric(prev);
    if (n !== null && n > 0) {
      amount = n;
      if (prevPrev === normalize('साढ़े') || prevPrev === 'sadhe') amount += 0.5;
      if (prevPrev === normalize('सवा') || prevPrev === 'sawa') amount += 0.25;
    }
    // "महीना भर पहले", "पिछले हफ्ते" - a unit with no number means one.
    if (amount === null) amount = 1;
    return Math.round(amount * unit.days);
  }

  if (words.includes('परसों') || words.includes(normalize('परसों'))) return 2;
  if (words.includes('कल') || words.includes('yesterday')) return 1;
  if (words.includes('आज') || words.includes('today')) return 0;

  // A bare number with no unit - farmers most often count in days.
  const bare = words.map((w) => (/^\d+$/.test(w) ? Number(w) : null)).find((n) => n !== null);
  return bare ?? null;
}
