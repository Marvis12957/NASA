import React from "react";
import { Check, ChevronDown, Languages } from "lucide-react";
import { LANGUAGES, getLanguage, setLanguage, languageLabel, onLanguageChange } from "./langState";
import { api } from "./api";

const OTHER = "__other__";

/**
 * Chọn ngôn ngữ trả lời của agent (đặt trong Cài đặt): một dropdown với
 * sẵn vài lựa chọn mẫu (mặc định English). Chỉ khi người dùng chọn
 * "Ngôn ngữ khác…" thì ô nhập mới hiện ra để họ gõ ngôn ngữ của mình.
 * Lựa chọn lưu localStorage + đồng bộ vào bảng users (POST /api/me)
 * khi đã đăng nhập.
 */
export function LanguagePicker({ className = "" }) {
  const [value, setValue] = React.useState(() => getLanguage());
  const [open, setOpen] = React.useState(false);
  const [customText, setCustomText] = React.useState("");
  const boxRef = React.useRef(null);

  const isCustom = !LANGUAGES.some((l) => l.code === value);
  const [customMode, setCustomMode] = React.useState(isCustom);

  React.useEffect(
    () =>
      onLanguageChange((v) => {
        setValue(v);
        setCustomMode(!LANGUAGES.some((l) => l.code === v));
      }),
    []
  );

  React.useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open ]);

  const save = (v) => {
    setLanguage(v);
    // Đồng bộ về database theo tài khoản; chưa đăng nhập thì backend trả
    // 401 và lỗi bị bỏ qua — giá trị vẫn nằm ở localStorage.
    api.syncMe({ language: v }).catch(() => {});
  };

  const pick = (code) => {
    setOpen(false);
    if (code === OTHER) {
      setCustomMode(true);
      setCustomText(isCustom ? value : "");
      return;
    }
    setCustomMode(false);
    save(code);
  };

  const commitCustom = () => {
    const t = customText.trim();
    if (t) save(t);
    else if (!isCustom) setCustomMode(false);
  };

  return (
    <div ref={boxRef} className={`relative ${className}`} title="Answer language · Ngôn ngữ agent trả lời">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex h-8 w-44 items-center gap-1.5 rounded-full border bg-transparent px-2.5 text-[13px] outline-none transition-colors hover:border-zinc-400"
        aria-label="Answer language"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <Languages className="h-4 w-4 shrink-0 text-zinc-400" />
        <span className="min-w-0 flex-1 truncate text-left">{languageLabel(value)}</span>
        <ChevronDown className={`h-4 w-4 shrink-0 text-zinc-400 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <ul className="absolute right-0 z-50 mt-1.5 max-h-56 w-48 overflow-y-auto rounded-xl border bg-white p-1 shadow-lg dark:bg-zinc-900" role="listbox">
          {LANGUAGES.map((l) => (
            <li key={l.code}>
              <button
                type="button"
                onClick={() => pick(l.code)}
                className="flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-[13px] hover:bg-zinc-100 dark:hover:bg-zinc-800"
                role="option"
                aria-selected={value === l.code}
              >
                <span>{l.label}</span>
                {value === l.code && <Check className="h-3.5 w-3.5 text-zinc-400" />}
              </button>
            </li>
          ))}
          <li className="mt-1 border-t pt-1">
            <button
              type="button"
              onClick={() => pick(OTHER)}
              className="flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-[13px] hover:bg-zinc-100 dark:hover:bg-zinc-800"
              role="option"
              aria-selected={customMode}
            >
              <span>Ngôn ngữ khác…</span>
              {customMode && <Check className="h-3.5 w-3.5 text-zinc-400" />}
            </button>
          </li>
        </ul>
      )}
      {customMode && (
        <input
          autoFocus
          value={customText}
          onChange={(e) => setCustomText(e.target.value)}
          onBlur={commitCustom}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
          }}
          placeholder="Nhập ngôn ngữ của bạn…"
          className="mt-2 h-8 w-44 rounded-full border bg-transparent px-2.5 text-[13px] outline-none transition-colors focus:border-zinc-400"
          aria-label="Custom language"
        />
      )}
    </div>
  );
}
