// Ngôn ngữ trả lời của agent: mặc định English. Người dùng đổi trong
// LanguagePicker (combobox có thể gõ tự do nếu ngôn ngữ chưa có sẵn).
// Lưu ở localStorage và đồng bộ vào bảng users khi đã đăng nhập.
export const LANGUAGES = [
  { code: "en", label: "English" },
  { code: "vi", label: "Tiếng Việt" },
  { code: "es", label: "Español" },
  { code: "fr", label: "Français" },
  { code: "de", label: "Deutsch" },
  { code: "pt", label: "Português" },
  { code: "it", label: "Italiano" },
  { code: "ru", label: "Русский" },
  { code: "zh", label: "中文" },
  { code: "ja", label: "日本語" },
  { code: "ko", label: "한국어" },
  { code: "th", label: "ไทย" },
  { code: "id", label: "Bahasa Indonesia" },
  { code: "hi", label: "हिन्दी" },
  { code: "ar", label: "العربية" },
];

const KEY = "flame-lang";
const listeners = new Set();

export function getLanguage() {
  try {
    return localStorage.getItem(KEY) || "en";
  } catch {
    return "en";
  }
}

export function setLanguage(value) {
  const v = (value || "").trim() || "en";
  try {
    localStorage.setItem(KEY, v);
  } catch {
    /* private mode: giữ trong phiên */
  }
  listeners.forEach((fn) => fn(v));
}

export function onLanguageChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function languageLabel(value) {
  const found = LANGUAGES.find((l) => l.code === value);
  return found ? found.label : value || "English";
}
