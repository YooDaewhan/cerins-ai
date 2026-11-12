import { useState } from 'react'
import Workspace from './Workspace'
import './App.css'

// 규정 카드. ready=false면 준비중. 새 규정을 열 때 ready를 true로 바꾸고 family를 지정.
const CARDS = [
  { family: 'TR CU', title: 'TR CU', desc: '관세동맹 기술규정', ready: true },
  { family: '', title: '준비중', desc: 'GOST', ready: false },
  { family: '', title: '준비중', desc: '', ready: false },
  { family: '', title: '준비중', desc: '', ready: false },
]

export default function App() {
  const [family, setFamily] = useState<string | null>(null)
  if (family) return <Workspace family={family} onHome={() => setFamily(null)} />
  return (
    <div className="home">
      <h1>CERINS AI</h1>
      <p>인증 규정 검토 도우미 — 규정을 선택하세요</p>
      <div className="cards">
        {CARDS.map((c, i) => (
          <div
            key={i}
            className={`card ${c.ready ? 'on' : 'off'}`}
            onClick={c.ready ? () => setFamily(c.family) : undefined}
          >
            <b>{c.title}</b>
            <small>{c.desc || ' '}</small>
          </div>
        ))}
      </div>
    </div>
  )
}
