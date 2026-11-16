import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

import { fixtureData } from './fixture-data.ts'

// `VITE_DATA_SOURCE=local` builds the UI `data-agent web` serves (src/App.tsx); `pnpm dev:local`
// then sends the API to a `data-agent web` on its default port.
const local = process.env.VITE_DATA_SOURCE === 'local'

export default defineConfig({
  plugins: [react(), tailwindcss(), ...(local ? [] : [fixtureData()])],
  server: local ? { proxy: { '/api': 'http://127.0.0.1:8765' } } : {},
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node',
  },
})
