/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        background: '#090D16', // Deep Atmospheric Obsidian
        surface: 'rgba(15, 23, 42, 0.55)', // Translucent Storm Slate
        borderAccent: 'rgba(255, 255, 255, 0.08)',
        textPrimary: '#F8FAFC', // Pure Ice White
        textMuted: '#94A3B8', // Cool Fog Gray
        accent: '#38BDF8', // Crisp Cirrus Azure
        alertRed: '#F87171', // Muted Coral
      },
      fontFamily: {
        display: ['"Playfair Display"', '"Cormorant Garamond"', 'serif'],
        sans: ['"Geist Sans"', '"Inter"', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'monospace'],
      },
      keyframes: {
        marquee: {
          '0%': { transform: 'translateX(0)' },
          '100%': { transform: 'translateX(-50%)' },
        }
      },
      animation: {
        marquee: 'marquee 25s linear infinite',
      },
    },
  },
  plugins: [],
}
