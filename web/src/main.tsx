import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'

// The pages arrive with tickets 76, 79 and 81 (.scratch/web-ui/spec.md).
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <main className="p-8">data-agent</main>
  </StrictMode>,
)
