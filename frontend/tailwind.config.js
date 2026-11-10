/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        studioDark: "#0B0F19",
        studioCard: "#131B2E",
        studioBorder: "#1E293B",
        accentCyan: "#00F0FF",
        accentPurple: "#7928CA",
        accentYellow: "#FFDE59"
      }
    },
  },
  plugins: [],
}
