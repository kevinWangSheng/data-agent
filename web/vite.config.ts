import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

import { fixtureData } from './fixture-data.ts'

export default defineConfig({
  plugins: [react(), tailwindcss(), fixtureData()],
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node',
  },
})
