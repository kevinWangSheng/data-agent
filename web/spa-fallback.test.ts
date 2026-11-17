import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { build } from 'vite'
import { expect, test } from 'vitest'

import { spaFallback } from './spa-fallback'

test('a build under a base path writes 404.html, the same page as index.html', async () => {
  const root = mkdtempSync(join(tmpdir(), 'spa-fallback-'))
  writeFileSync(join(root, 'index.html'), '<!doctype html><div id="root"></div><script type="module" src="/main.js"></script>')
  writeFileSync(join(root, 'main.js'), 'document.title = "app"')
  const outDir = join(root, 'dist')

  await build({ root, base: '/data-agent/', configFile: false, logLevel: 'silent', plugins: [spaFallback()], build: { outDir } })

  const index = readFileSync(join(outDir, 'index.html'), 'utf8')
  expect(index).toMatch(/src="\/data-agent\/assets\/[^"]+\.js"/)
  expect(readFileSync(join(outDir, '404.html'), 'utf8')).toBe(index)
})
