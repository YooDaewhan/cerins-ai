import { useEffect, useState } from 'react'
import Workspace, { TABS, type Tab } from './Workspace'
import './App.css'

// 규정 카드. ready=false면 준비중. 새 규정을 열 때 ready를 true로 바꾸고 family를 지정.
const CARDS = [
  { family: 'TR CU', title: 'TR CU', desc: '관세동맹 기술규정', ready: true },
  { family: '', title: '준비중', desc: 'GOST', ready: false },
  { family: '', title: '준비중', desc: '', ready: false },
  { family: '', title: '준비중', desc: '', ready: false },
]

// 현재 위치는 주소의 # 뒤에 저장 (예: #TR%20CU/regs) → 새로고침해도 유지, 뒤로 가기 동작
function readHash(): { family: string | null; tab: Tab } {
  const [f = '', t = ''] = location.hash.slice(1).split('/').map(decodeURIComponent)
  return {
    family: CARDS.some((c) => c.ready && c.family === f) ? f : null,
    tab: (TABS as readonly string[]).includes(t) ? (t as Tab) : 'chat',
  }
}
const go = (family: string | null, tab: Tab = 'chat') => {
  location.hash = family ? `${encodeURIComponent(family)}/${tab}` : ''
}

export default function App() {
  const [route, setRoute] = useState(readHash)
  useEffect(() => {
    const onHash = () => setRoute(readHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  if (route.family) {
    return <Workspace family={route.family} tab={route.tab} onTab={(t) => go(route.family, t)} onHome={() => go(null)} />
  }
  return (
    <div className="home">
      <h1>CERINS AI</h1>
      <p>인증 규정 검토 도우미 — 규정을 선택하세요</p>
      <div className="cards">
        {CARDS.map((c, i) => (
          <div
            key={i}
            className={`card ${c.ready ? 'on' : 'off'}`}
            onClick={c.ready ? () => go(c.family) : undefined}
          >
            <b>{c.title}</b>
            <small>{c.desc || ' '}</small>
          </div>
        ))}
      </div>
    </div>
  )
}
