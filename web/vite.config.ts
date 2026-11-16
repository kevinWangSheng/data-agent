import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

import { siteData } from './site-data.ts'

export default defineConfig({
  plugins: [react(), tailwindcss(), siteData()],
  test: {
    include: ['src/**/*.test.ts', 'site-data.test.ts'],
    environment: 'node',
  },
})
