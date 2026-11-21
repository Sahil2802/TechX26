import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { play } from './sfx'

// Console-switch tick on every enabled control; richer cues (approve, alarm) come from the callers.
document.addEventListener('click', (e) => {
  if ((e.target as Element).closest('button:not(:disabled), select, [role=tab]')) play('click')
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
