import React from 'react'

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('[ErrorBoundary]', error, info)
  }

  reset = () => this.setState({ error: null })

  render() {
    if (this.state.error) {
      return (
        <div style={{
          padding: 24,
          margin: 24,
          border: '1px solid #c0392b',
          borderRadius: 6,
          background: '#2a1a1a',
          color: '#eee',
          fontFamily: 'monospace',
        }}>
          <h2 style={{ marginTop: 0, color: '#e74c3c' }}>Something broke.</h2>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{String(this.state.error?.stack || this.state.error)}</pre>
          <button onClick={this.reset} style={{ padding: '6px 12px', marginTop: 8 }}>
            Try again
          </button>
          <button
            onClick={() => window.location.reload()}
            style={{ padding: '6px 12px', marginTop: 8, marginLeft: 8 }}
          >
            Reload page
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
