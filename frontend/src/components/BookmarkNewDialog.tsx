import { useEffect, useState } from "react";

/**
 * 「新增書籤」modal — 單一輸入框、確認後 create。
 */
interface Props {
  onClose: () => void;
  onCreate: (name: string) => Promise<void>;
}

export function BookmarkNewDialog({ onClose, onCreate }: Props) {
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function submit() {
    const v = name.trim();
    if (!v || submitting) return;
    setSubmitting(true);
    try {
      await onCreate(v);
    } catch (e) {
      alert(`新增失敗:${(e as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <div onClick={onClose}
        className="fixed inset-0 z-20 bg-bg-deep/85"
        style={{ backdropFilter: "blur(2px)" }} />

      <div role="dialog" aria-modal="true"
        className="fixed top-1/2 left-1/2 z-[21] bg-bg-card border border-line-strong p-6
                   w-[min(360px,90vw)]"
        style={{ transform: "translate(-50%, -50%)" }}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-serif font-bold text-xl tracking-[-0.4px]">新增書籤</h3>
          <button onClick={onClose} className="text-ink-dim hover:text-accent text-lg" aria-label="關閉">×</button>
        </div>

        <div className="h-px bg-line mb-4" />

        <label className="label-tiny mb-2 block">名稱</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
          autoFocus
          placeholder="例: 高息股、半導體..."
          className="w-full bg-bg-deep border border-line text-ink px-3 py-2 outline-none
                     focus:border-accent text-sm"
        />

        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onClose}
            className="px-4 py-2 text-sm border border-line-strong text-ink-muted hover:border-ink-muted hover:text-ink">
            取消
          </button>
          <button onClick={submit} disabled={!name.trim() || submitting}
            className="px-4 py-2 text-sm bg-accent text-bg font-medium disabled:opacity-40">
            {submitting ? "新增中…" : "新增"}
          </button>
        </div>
      </div>
    </>
  );
}
