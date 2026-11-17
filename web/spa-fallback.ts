// The online site's 404 page: the app's own index.html. GitHub Pages serves `404.html` for an address
// with no file, such as `/data-agent/runs/<id>`; the app then reads the address (src/App.tsx) and opens
// that page. Its asset links start at the base path, so they load at any depth.

import type { Plugin } from 'vite'

export function spaFallback(): Plugin {
  return {
    name: 'spa-fallback',
    apply: 'build',
    enforce: 'post',
    generateBundle(_, bundle) {
      const index = bundle['index.html']
      if (index?.type !== 'asset') this.error('no index.html in the build to copy into 404.html')
      this.emitFile({ type: 'asset', fileName: '404.html', source: index.source })
    },
  }
}
