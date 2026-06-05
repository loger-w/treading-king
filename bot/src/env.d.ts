// 讓 tsc 不因前端 api.ts 的 Vite 專屬 import.meta.env 而報錯。
// bot 執行期用 tsx/esbuild,type-only import 會被抹掉,api.ts 不會真的載入。
interface ImportMeta { readonly env: Record<string, string | undefined>; }
