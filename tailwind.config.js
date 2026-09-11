/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './App.tsx', './components/**/*.{ts,tsx}', './services/**/*.{ts,tsx}', './utils/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        nfl: {
          red: '#d50a0a',
          blue: '#013369',
          dark: '#121212',
          card: '#1e1e1e',
          accent: '#00ff88',
        },
      },
    },
  },
  plugins: [],
};
