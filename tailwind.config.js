/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        terminal: {
          bg: "#0B0F14",
          card: "#111827",
          border: "#243041",
          muted: "#7C8798",
        },
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(45, 212, 191, 0.16), 0 12px 32px rgba(0,0,0,0.35)",
      },
    },
  },
  plugins: [],
};
