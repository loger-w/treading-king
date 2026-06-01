import { useRef, useState } from "react";
import { api } from "../lib/api";

/**
 * 設定可攜檔面板 — 匯出(下載 JSON)/ 匯入(整包取代)。
 * 用在「管理書籤」modal 裡,讓 user 把本機設定搬到別台機器。
 */
export function ConfigIODialog({ onClose }: { onClose: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function handleExport() {
    setBusy(true);
    setMsg(null);
    try {
      const blob = await api.config.export();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const date = new Date().toISOString().slice(0, 10);
      a.href = url;
      a.download = `trading-king-config-${date}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setMsg(`匯出失敗:${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function handleImportFile(file: File) {
    if (!window.confirm("匯入會「整包取代」本機目前的書籤 / 訊號規則 / 監聽清單(會先備份舊檔)。確定?")) return;
    setBusy(true);
    setMsg(null);
    try {
      const data = JSON.parse(await file.text());
      await api.config.import(data);
      setMsg("匯入完成,設定已即時套用。");
    } catch (e) {
      setMsg(`匯入失敗:${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <button
        disabled={busy}
        onClick={handleExport}
        className="px-4 py-2 text-sm bg-accent text-bg font-medium disabled:opacity-40"
      >
        匯出設定(下載 JSON)
      </button>
      <button
        disabled={busy}
        onClick={() => fileRef.current?.click()}
        className="px-4 py-2 text-sm border border-accent text-ink hover:bg-accent/10 disabled:opacity-40"
      >
        匯入設定(整包取代)
      </button>
      <input
        ref={fileRef}
        type="file"
        accept="application/json"
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) handleImportFile(f);
          e.target.value = "";
        }}
      />
      {msg && <p className="text-xs text-ink-dim">{msg}</p>}
      <button onClick={onClose} className="text-xs text-ink-dim hover:text-accent self-start">
        ← 返回
      </button>
    </div>
  );
}
