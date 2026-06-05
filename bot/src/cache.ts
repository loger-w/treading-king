// 簡單 per-key TTL 快取;now() 可注入以便測試。
export class TtlCache<T> {
  private store = new Map<string, { at: number; val: T }>();
  constructor(private ttlMs: number, private now: () => number = () => Date.now()) {}
  async get(key: string, loader: () => Promise<T>): Promise<T> {
    const hit = this.store.get(key);
    if (hit && this.now() - hit.at < this.ttlMs) return hit.val;
    const val = await loader();
    this.store.set(key, { at: this.now(), val });
    return val;
  }
}
