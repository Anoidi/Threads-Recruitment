/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#1a1e1b",
        canvas: "#f5f6f3",
        moss: { 50: "#eef7f1", 100: "#d8ecdf", 500: "#398158", 600: "#2e6d49", 700: "#26583d" },
        clay: { 50: "#fff5ef", 500: "#b96842", 600: "#9e5435" }
      },
      boxShadow: { panel: "0 1px 2px rgba(23, 35, 27, .06), 0 5px 18px rgba(23, 35, 27, .04)" },
      fontFamily: { sans: ["Inter", "Segoe UI", "sans-serif"] }
    }
  },
  plugins: []
};
