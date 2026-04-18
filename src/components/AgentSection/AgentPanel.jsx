import { useEffect, useRef, useState } from 'react'
import './AgentPanel.css'

const STARTERS = [
  'Audit the app: what needs attention?',
  'Find duplicate wanted items',
  'What recognize jobs are stuck?',
  'Show me orphan files in the pipeline',
  'List everything in the library missing an album',
]


function AgentPanel() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [sidecarUp, setSidecarUp] = useState(null)
  const scrollRef = useRef(null)
  const abortRef = useRef(null)

  useEffect(() => {
    fetch('/sidecar/health')
      .then(r => r.ok ? r.json() : null)
      .then(d => setSidecarUp(!!d?.ok))
      .catch(() => setSidecarUp(false))
  }, [])

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages])

  const send = async (text) => {
    if (!text.trim() || sending) return
    setMessages(m => [...m, { role: 'user', content: text }])
    setInput('')
    setSending(true)

    const assistantIdx = messages.length + 1
    setMessages(m => [...m, { role: 'assistant', content: '', tools: [] }])

    const ctrl = new AbortController()
    abortRef.current = ctrl

    try {
      const resp = await fetch('/sidecar/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
        signal: ctrl.signal,
      })

      if (!resp.ok || !resp.body) {
        throw new Error(`sidecar returned ${resp.status}`)
      }

      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const parts = buffer.split('\n\n')
        buffer = parts.pop() || ''
        for (const raw of parts) {
          const line = raw.split('\n').find(l => l.startsWith('data: '))
          if (!line) continue
          try {
            const data = JSON.parse(line.slice(6))
            if (data.content) {
              setMessages(m => {
                const copy = [...m]
                const prev = copy[assistantIdx] || {}
                copy[assistantIdx] = { ...prev, role: 'assistant', content: data.content }
                return copy
              })
            }
            if (data.tool) {
              setMessages(m => {
                const copy = [...m]
                const prev = copy[assistantIdx] || { role: 'assistant', content: '', tools: [] }
                const tools = prev.tools || []
                if (tools.includes(data.tool)) return m
                copy[assistantIdx] = { ...prev, tools: [...tools, data.tool] }
                return copy
              })
            }
            if (data.error) {
              setMessages(m => {
                const copy = [...m]
                const prev = copy[assistantIdx] || {}
                copy[assistantIdx] = { ...prev, role: 'assistant', content: `Error: ${data.error}` }
                return copy
              })
            }
          } catch {
            /* ignore partial */
          }
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') {
        setMessages(m => {
          const copy = [...m]
          copy[assistantIdx] = { role: 'assistant', content: `Failed: ${e.message}` }
          return copy
        })
      }
    } finally {
      setSending(false)
    }
  }

  const resetSession = async () => {
    try { await fetch('/sidecar/reset', { method: 'POST' }) } catch {}
    setMessages([])
  }

  return (
    <div className="agent-panel">
      <div className="agent-header">
        <div>
          <h2 className="page-title">Agent</h2>
          <p className="agent-subtitle">
            {sidecarUp === false && (
              <span className="agent-status-bad">
                Sidecar unreachable. Start it with <code>./ocdj-sidecar/run.sh</code> on the host.
              </span>
            )}
            {sidecarUp === true && <span className="agent-status-ok">Sidecar connected · Max auth</span>}
            {sidecarUp === null && <span>Checking sidecar…</span>}
          </p>
        </div>
        <button className="btn btn-sm" onClick={resetSession}>New session</button>
      </div>

      <div className="agent-transcript" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="agent-empty">
            <div className="agent-empty__title">Ask the assistant something.</div>
            <div className="agent-empty__sub">Try one:</div>
            <div className="agent-empty__starters">
              {STARTERS.map(s => (
                <button key={s} className="btn btn-sm" onClick={() => send(s)} disabled={!sidecarUp || sending}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`agent-msg agent-msg--${m.role}`}>
            <div className="agent-msg__role">{m.role === 'user' ? 'you' : 'assistant'}</div>
            <div className="agent-msg__body">{m.content || (sending && i === messages.length - 1 ? '…' : '')}</div>
            {m.tools && m.tools.length > 0 && (
              <div className="agent-tools">
                {m.tools.map(t => <span key={t} className="agent-tool-chip">{t}</span>)}
              </div>
            )}
          </div>
        ))}
      </div>

      <form
        className="agent-composer"
        onSubmit={e => { e.preventDefault(); send(input) }}
      >
        <input
          type="text"
          placeholder={sidecarUp ? 'Ask about your library, wantlist, mixes…' : 'Start the sidecar first'}
          value={input}
          onChange={e => setInput(e.target.value)}
          disabled={!sidecarUp || sending}
        />
        <button className="btn btn-primary" type="submit" disabled={!sidecarUp || sending || !input.trim()}>
          {sending ? 'Working…' : 'Send'}
        </button>
      </form>
    </div>
  )
}

export default AgentPanel
