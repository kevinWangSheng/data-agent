// An in-app link: a real `<a href>` (open in a new tab, copy the address), and a plain click
// changes the page without reloading it.

import type { MouseEvent, ReactNode } from 'react'

export function Link({ href, onGo, className, title, children }: { href: string; onGo: (href: string) => void; className?: string; title?: string; children: ReactNode }) {
  const click = (e: MouseEvent<HTMLAnchorElement>) => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    onGo(href)
  }
  return (
    <a href={href} onClick={click} className={className} title={title}>
      {children}
    </a>
  )
}
