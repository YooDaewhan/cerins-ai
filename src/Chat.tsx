import { useEffect, useRef, useState } from 'react'
import { api, jsonInit } from './api'

type Source = { code: string; article: string | null; page: number | null }
type Msg = { role: 'me' | 'ai'; text: string; sources?: Source[]; error?: boolean }
type Ask = { answer: string; sources: Source[] }

export default function Chat({ family }: { family: string }) {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' })
  }, [msgs])

  async function send() {
    const question = q.trim()
    if (!question || busy) return
    setQ('')
    setBusy(true)
    setMsgs((m) => [...m, { role: 'me', text: question }])
    try {
      // 오류 메시지를 뺀 최근 대화를 함께 보내 후속 질문이 앞 조건을 이어가게 함
      const history = msgs.filter((m) => !m.error).slice(-6).map((m) => ({ role: m.role, text: m.text }))
      const r = await api<Ask>('/api/ask', jsonInit('POST', { question, family, history }))
      setMsgs((m) => [...m, { role: 'ai', text: r.answer, sources: r.sources }])
    } catch (e) {
      setMsgs((m) => [...m, { role: 'ai', text: `오류: ${(e as Error).message}`, error: true }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="msgs">
        {msgs.length === 0 && (
          <div className="empty">
            <h2>{family} 질문하기</h2>
            <p>예: "규격 15cm, 압력 14인데 적용 대상인가요?"</p>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={m.role}>
            <div className={`bubble ${m.error ? 'err' : ''}`}>{m.text}</div>
            {m.sources && m.sources.length > 0 && (
              <div className="meta">
                근거:{' '}
                {m.sources.map((s, j) => (
                  <span className="chip" key={j}>
                    {s.code} {s.article ?? ''} {s.page ? `p.${s.page}` : ''}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
        {busy && (
          <div className="ai">
            <div className="bubble">검토 중…</div>
          </div>
        )}
        <div ref={end} />
      </div>
      <div className="composer">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            send()
          }}
        >
          <textarea
            rows={2}
            value={q}
            placeholder="질문을 입력하세요 (Enter 전송, Shift+Enter 줄바꿈)"
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                send()
              }
            }}
          />
          <button className="btn" disabled={busy || !q.trim()}>
            전송
          </button>
        </form>
      </div>
    </>
  )
}
