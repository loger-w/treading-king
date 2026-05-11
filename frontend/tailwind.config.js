/** @type {import('tailwindcss').Config} */
// Editorial Dark — 暗色襯線雜誌調
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx,js,jsx}"],
  theme: {
    extend: {
      colors: {
        // 暖黑系背景
        bg: {
          DEFAULT: "#14110c",  // 主背景（咖啡墨色）
          card: "#1d1812",     // 卡片底
          deep: "#0d0a07",     // 比主底再深一階（footer / overlay）
        },
        // 線條
        line: {
          DEFAULT: "#2e2a22",  // 細分隔
          strong: "#4a4234",   // 主分隔（取代米色版的 1px black）
        },
        // 文字
        ink: {
          DEFAULT: "#ede4d3",  // 主文字（暖米白）
          muted: "#d4c8b0",    // 次文字
          dim: "#8a8273",      // 標籤 / metadata
        },
        // 強調色 — 在暗底會跳
        accent: {
          DEFAULT: "#e85a4f",  // 深紅 — masthead top border, accent dots
          hover: "#f06b5f",
        },
        // 台股慣例：上漲紅、下跌綠（與美股相反）
        bull: "#e85a4f",       // 上漲紅（跟 accent 同色）
        bear: "#7fc99a",       // 下跌綠
      },
      fontFamily: {
        serif: ['"Source Serif 4"', 'Georgia', 'serif'],
        sans: ['"Inter Tight"', '-apple-system', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        // Major third scale (1.25x)
        '2xs': ['10px', '14px'],
        xs: ['11px', '16px'],
        sm: ['13px', '18px'],
        base: ['15px', '24px'],
        lg: ['18px', '28px'],
        xl: ['22px', '30px'],
        '2xl': ['28px', '36px'],
        '3xl': ['36px', '44px'],
        '4xl': ['48px', '56px'],
        '5xl': ['64px', '72px'],
      },
      letterSpacing: {
        tightest: '-0.04em',
        tighter: '-0.025em',
        editorial: '-0.5px',
      },
    },
  },
  plugins: [],
};
