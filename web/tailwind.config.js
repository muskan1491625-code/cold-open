/** Tailwind reads the CSS variables, so light/dark themes swap without class juggling. */
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    colors: {
      transparent: "transparent",
      current: "currentColor",
      bay: v("bay"), console: v("console"), raised: v("raised"), rule: v("rule"),
      bone: v("bone"), fog: v("fog"), teal: v("teal"), tungsten: v("tungsten"), rec: v("rec"), "rec-ink": v("rec-ink"),
      ink: v("ink"),
    },
    fontFamily: {
      script: ['"Courier Prime"', "Courier", "monospace"],
      sans: ['"IBM Plex Sans"', "system-ui", "sans-serif"],
      cond: ['"IBM Plex Sans Condensed"', '"IBM Plex Sans"', "sans-serif"],
    },
    fontSize: {
      xs: ["12px", "16px"], sm: ["14px", "20px"], base: ["15px", "1.5"], md: ["16px", "24px"],
      lg: ["20px", "28px"], xl: ["25px", "32px"], "2xl": ["31px", "38px"], "3xl": ["39px", "46px"],
    },
    borderRadius: { none: "0", chip: "4px", panel: "8px", phone: "12px", full: "9999px" },
    extend: {},
  },
  plugins: [],
};
