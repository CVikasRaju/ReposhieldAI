export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        dark: {
          bg: "#18181b",
          card: "#27272a",
          border: "#3f3f4f",
          input: "#3f3f4f",
          primary: "#3b82f6",
          muted: "#6b7280",
        },
        light: {
          bg: "#f9fafb",
          card: "#ffffff",
          border: "#e5e7eb",
          input: "#f3f4f6",
          primary: "3b82f6",
          muted: "#6b7280",
        },
      }
    }
  },
  plugins: [],
}
