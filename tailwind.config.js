/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    './templates/**/*.html',
    './apps/**/templates/**/*.html',
    './apps/**/*.py',
    './static/js/**/*.js',
  ],
  safelist: [
    // Status badge colours are chosen at runtime from model fields, so Tailwind
    // cannot see them in the templates.
    { pattern: /^(bg|text|border|ring)-(slate|sky|emerald|amber|rose|violet|cyan|orange|teal|indigo)-(50|100|200|300|400|500|600|700|800|900)$/ },
    { pattern: /^(bg|text|border)-(slate|sky|emerald|amber|rose|violet|cyan|orange|teal|indigo)-(400|500|600)\/(10|20|30)$/ },
  ],
  theme: {
    extend: {
      colors: {
        // Ocean-inspired brand palette.
        brand: {
          50:  '#eff9ff',
          100: '#def2ff',
          200: '#b6e7ff',
          300: '#75d5ff',
          400: '#2cbeff',
          500: '#02a4f0',
          600: '#0083ce',
          700: '#0068a6',
          800: '#065889',
          900: '#0b4a71',
          950: '#072e4b',
        },
        sand: {
          50:  '#fbf8f1',
          100: '#f5eede',
          200: '#eadbbc',
          300: '#dcc292',
          400: '#cda568',
          500: '#c28f4c',
          600: '#b47940',
          700: '#956036',
          800: '#794e32',
          900: '#63412b',
        },
      },
      fontFamily: {
        sans: ['Inter', 'Segoe UI', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Cascadia Code', 'Consolas', 'monospace'],
      },
      boxShadow: {
        card: '0 1px 2px 0 rgb(15 23 42 / 0.04), 0 1px 3px 1px rgb(15 23 42 / 0.06)',
        'card-hover': '0 4px 6px -1px rgb(15 23 42 / 0.08), 0 2px 4px -2px rgb(15 23 42 / 0.06)',
      },
      keyframes: {
        'slide-in': {
          '0%': { opacity: '0', transform: 'translateY(-6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-1000px 0' },
          '100%': { backgroundPosition: '1000px 0' },
        },
      },
      animation: {
        'slide-in': 'slide-in 0.2s ease-out',
        shimmer: 'shimmer 2s infinite linear',
      },
    },
  },
  plugins: [
    require('@tailwindcss/forms')({ strategy: 'class' }),
    require('@tailwindcss/typography'),
  ],
};
