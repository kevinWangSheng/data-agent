import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

import { siteData } from './site-data.ts'
import { spaFallback } from './spa-fallback.ts'

// `VITE_DATA_SOURCE=local` builds the UI `data-agent web` serves (src/App.tsx); `pnpm dev:local`
// then sends the API to a `data-agent web` on its default port. The other build is the online site
// (.github/workflows/pages.yml), with its data and a 404 page that opens the app.
const local = process.env.VITE_DATA_SOURCE === 'local'

export default defineConfig({
  plugins: [react(), tailwindcss(), ...(local ? [] : [siteData(), spaFallback()])],
  server: local ? { proxy: { '/api': 'http://127.0.0.1:8765' } } : {},
  test: {
    include: ['src/**/*.test.ts', 'site-data.test.ts', 'spa-fallback.test.ts'],
    environment: 'node',
  },
})
