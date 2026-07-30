/** @type {import('tailwindcss').Config} */
module.exports = {
  // Dark-exclusive: the palette in app/globals.css has no light counterpart,
  // so there is no `darkMode` strategy to toggle.
  // `lib/` MUST be scanned: the bracket palette (lib/api/simulations.ts) and
  // the FDR band palette (lib/api/fixtures.ts) declare their Tailwind classes
  // as data, not JSX. Without this glob those class names are invisible to the
  // compiler and every bracket bar and ticker cell ships with no background.
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        // Interactive accent (brand orange). Previously referenced by every
        // component as `bg-primary` / `text-primary` but NEVER declared, so
        // those classes silently generated nothing.
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        // Data semantics -- positive/negative metrics only, never chrome.
        positive: {
          DEFAULT: "hsl(var(--positive))",
          foreground: "hsl(var(--positive-foreground))",
        },
        negative: {
          DEFAULT: "hsl(var(--negative))",
          foreground: "hsl(var(--negative-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
      },
      fontFamily: {
        // System monospace stack: SF Mono on macOS, Cascadia on Windows.
        // Zero bundle cost and no build-time font fetch. Swapping in Geist
        // Mono or JetBrains Mono is a one-line change here plus the loader.
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "SF Mono",
          "Menlo",
          "Monaco",
          "Cascadia Mono",
          "Segoe UI Mono",
          "Roboto Mono",
          "monospace",
        ],
      },
      borderRadius: {
        // Tight by default: the largest radius in the product is 4px.
        sm: "2px",
        DEFAULT: "3px",
        md: "3px",
        lg: "4px",
        xl: "4px",
      },
      transitionTimingFunction: {
        terminal: "cubic-bezier(0.16, 1, 0.3, 1)",
      },
    },
  },
  plugins: [],
};
