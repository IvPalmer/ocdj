import { createContext, useContext, useState, useCallback, useRef, useEffect } from 'react'
import './Toast.css'

const ToastContext = createContext(null)

let externalPush = null

export function toast(message, opts = {}) {
  if (externalPush) externalPush(message, opts)
  else console.warn('[toast] provider not mounted:', message)
}

export function ToastProvider({ children }) {
  const [items, setItems] = useState([])
  const idRef = useRef(0)

  const push = useCallback((message, { type = 'info', duration = 5000 } = {}) => {
    const id = ++idRef.current
    setItems(prev => [...prev, { id, message, type }])
    if (duration > 0) {
      setTimeout(() => {
        setItems(prev => prev.filter(t => t.id !== id))
      }, duration)
    }
  }, [])

  const dismiss = useCallback((id) => {
    setItems(prev => prev.filter(t => t.id !== id))
  }, [])

  useEffect(() => {
    externalPush = push
    return () => { externalPush = null }
  }, [push])

  return (
    <ToastContext.Provider value={{ push, dismiss }}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite">
        {items.map(t => (
          <div key={t.id} className={`toast toast-${t.type}`} onClick={() => dismiss(t.id)}>
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used inside ToastProvider')
  return ctx
}
