import type { ReactNode } from 'react'

// AI 답변용 최소 마크다운: 제목(#), 표(|), 목록(-, *, 1.), 인용(>), 구분선(---), **굵게**, `코드`, <br>, $m^3$.
// innerHTML 없이 React 요소로만 그려서 답변 내용이 HTML로 실행되지 않음.

const SUP: Record<string, string> = { '2': '²', '3': '³' }
// "$m^3$" → "m³" (수식 기호는 단위 표기에만 쓰임)
const tex = (s: string) => s.replace(/\$([^$\n]+)\$/g, (_, x: string) => x.replace(/\^\{?(\d)\}?/g, (__, d: string) => SUP[d] ?? `^${d}`))

function inline(s: string): ReactNode[] {
  return tex(s)
    .split(/(\*\*[^*]+\*\*|`[^`]+`|<br\s*\/?>)/g)
    .map((part, i) => {
      if (/^\*\*[^*]+\*\*$/.test(part)) return <b key={i}>{part.slice(2, -2)}</b>
      if (/^`[^`]+`$/.test(part)) return <code key={i}>{part.slice(1, -1)}</code>
      if (/^<br\s*\/?>$/.test(part)) return <br key={i} />
      return part
    })
}

// "| a | b \| c |" → ["a", "b | c"]
const cells = (line: string) =>
  line.trim().replace(/^\||\|$/g, '').split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, '|'))

export default function Md({ text }: { text: string }) {
  const lines = text.replace(/\r/g, '').split('\n')
  const out: ReactNode[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    const t = line.trim()
    if (!t) { i++; continue }
    let m: RegExpMatchArray | null
    if ((m = t.match(/^(#{1,4})\s+(.*)$/))) {
      const Tag = (['h3', 'h3', 'h4', 'h5'] as const)[m[1].length - 1]
      out.push(<Tag key={i}>{inline(m[2])}</Tag>)
      i++
    } else if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) {
      out.push(<hr key={i} />)
      i++
    } else if (t.startsWith('|')) {
      const rows: string[][] = []
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        const r = cells(lines[i])
        if (!r.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(r) // 구분 줄(| --- |) 제외
        i++
      }
      const [head, ...raw] = rows
      // 칸 수가 머리 행보다 많으면(칸 안에 '|'가 섞인 경우) 넘치는 칸을 마지막 칸에 합쳐 열이 밀리지 않게 함
      const body = raw.map((r) => (r.length > head.length ? [...r.slice(0, head.length - 1), r.slice(head.length - 1).join(' | ')] : r))
      out.push(
        <div className="md-table" key={i}>
          <table>
            <thead><tr>{head.map((c, j) => <th key={j}>{inline(c)}</th>)}</tr></thead>
            <tbody>{body.map((r, k) => <tr key={k}>{r.map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      )
    } else if (/^([-*•]|\d+[.)])\s+/.test(t)) {
      const ordered = /^\d/.test(t)
      const items: ReactNode[] = []
      while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) {
        const raw = lines[i]
        const depth = raw.length - raw.trimStart().length >= 2 ? 1 : 0 // 들여쓴 하위 항목
        items.push(<li key={i} style={depth ? { marginLeft: 18, listStyle: 'circle' } : undefined}>{inline(raw.trim().replace(/^([-*•]|\d+[.)])\s+/, ''))}</li>)
        i++
      }
      out.push(ordered ? <ol key={i} start={parseInt(t)}>{items}</ol> : <ul key={i}>{items}</ul>)
    } else if (t.startsWith('>')) {
      const q: string[] = []
      while (i < lines.length && lines[i].trim().startsWith('>')) q.push(lines[i++].trim().replace(/^>\s?/, ''))
      out.push(<blockquote key={i}>{q.map((x, j) => <p key={j}>{inline(x)}</p>)}</blockquote>)
    } else {
      const p: string[] = []
      while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\||>|([-*•]|\d+[.)])\s+|-{3,}$)/.test(lines[i].trim())) p.push(lines[i++].trim())
      if (!p.length) p.push(lines[i++].trim()) // 어느 규칙에도 안 맞는 줄은 그대로
      out.push(<p key={i}>{p.flatMap((x, j) => (j ? [<br key={`b${j}`} />, ...inline(x)] : inline(x)))}</p>)
    }
  }
  return <div className="md">{out}</div>
}
