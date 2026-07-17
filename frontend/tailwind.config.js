/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef4ff",
          100: "#d9e6ff",
          500: "#3b6ef2",
          600: "#2f57d4",
          700: "#2545ab",
        },
      },
    },
  },
  plugins: [],
};
