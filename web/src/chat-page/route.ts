// The chat page's address (local build only): `<base>chats/<id>` opens a conversation, or a single
// run shown as one; the base address is the chat page with none open. The base is the build's base
// path, as for the run page (../run-page/route.ts).

const rootOf = (base: string) => (base.endsWith('/') ? base : `${base}/`)

export function chatUrl(id: string, base: string): string {
  return `${rootOf(base)}chats/${encodeURIComponent(id)}`
}

/** The conversation id in the address; null when the address is not a conversation's. */
export function chatId(pathname: string, base: string): string | null {
  const root = rootOf(base)
  if (!pathname.startsWith(root)) return null
  const match = /^chats\/([^/]+)\/?$/.exec(pathname.slice(root.length))
  return match ? decodeURIComponent(match[1]) : null
}
