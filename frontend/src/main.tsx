import React, { Component, type ReactNode, StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { play } from './sfx'

// Console-switch tick on every enabled control; richer cues (approve, alarm) come from the callers.
document.addEventListener('click', (e) => {
  if ((e.target as Element).closest('button:not(:disabled), select, [role=tab]')) play('click')
})

interface ErrorBoundaryProps {
  children: ReactNode
}

interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
}

class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('Uncaught error in AquaGrid UI:', error, errorInfo)
  }

  render() {
    if (this.state.hasError) {
      return (
        <main style={{ padding: '2rem', maxWidth: '800px', margin: '0 auto' }}>
          <h1>💧 AquaGrid Operator Console</h1>
          <div className="alert a-error" style={{ marginTop: '1rem' }}>
            <h3>⚠️ UI Rendering Error</h3>
            <p>{this.state.error?.message || 'An unexpected rendering error occurred.'}</p>
            <pre style={{ maxHeight: '200px' }}>{this.state.error?.stack}</pre>
          </div>
          <button
            className="primary"
            onClick={() => {
              window.location.reload()
            }}
          >
            Reload Console
          </button>
        </main>
      )
    }

    return this.props.children
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
