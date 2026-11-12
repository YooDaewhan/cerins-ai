export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init)
  if (!r.ok) {
    // 서버가 {"detail": "..."}로 준 안내 문구만 꺼내 보여줌 (아니면 상태 코드와 원문)
    const t = await r.text()
    let detail: unknown
    try { detail = JSON.parse(t).detail } catch { /* JSON이 아님 */ }
    throw new Error(typeof detail === 'string' ? detail : `${r.status} ${t}`)
  }
  return r.json()
}

export const jsonInit = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body),
})
