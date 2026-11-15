/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    container: { center: true, padding: "2rem", screens: { "2xl": "1400px" } },
    extend: {
      fontFamily: {
        sans: ["'Fira Sans'", "system-ui", "-apple-system", "sans-serif"],
        mono: ["'Fira Code'", "ui-monospace", "monospace"],
      },
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        secondary: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--secondary-foreground))" },
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
        lime: { DEFAULT: "#ecfa32", deep: "#d7e021" },
        brand: { DEFAULT: "#0754ce", navy: "#1b2b85", deep: "#050d2a" },
      },
      borderRadius: { lg: "var(--radius)", md: "calc(var(--radius) - 2px)", sm: "calc(var(--radius) - 4px)" },
      keyframes: {
        "accordion-down": { from: { height: "0" }, to: { height: "var(--radix-accordion-content-height)" } },
        "accordion-up": { from: { height: "var(--radix-accordion-content-height)" }, to: { height: "0" } },
        "fade-up": { from: { opacity: "0", transform: "translateY(10px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        "pane-in": { from: { opacity: "0", transform: "translateX(28px)" }, to: { opacity: "1", transform: "translateX(0)" } },
        "pane-grow": { from: { maxWidth: "0px", opacity: "0" }, to: { maxWidth: "580px", opacity: "1" } },
        "drawer-in": { from: { opacity: "0", transform: "translateX(-28px)" }, to: { opacity: "1", transform: "translateX(0)" } },
        "pop-in": { from: { opacity: "0", transform: "scale(.97) translateY(6px)" }, to: { opacity: "1", transform: "scale(1) translateY(0)" } },
        "typing-dot": { "0%, 60%, 100%": { transform: "translateY(0)", opacity: ".35" }, "30%": { transform: "translateY(-4px)", opacity: "1" } },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        "fade-up": "fade-up .45s cubic-bezier(.21,.9,.35,1) both",
        "pane-in": "pane-in .38s cubic-bezier(.21,.9,.35,1) both",
        "pane-grow": "pane-grow .52s cubic-bezier(.22,1,.36,1) both",
        "drawer-in": "drawer-in .32s cubic-bezier(.21,.9,.35,1) both",
        "pop-in": "pop-in .3s cubic-bezier(.21,.9,.35,1) both",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
