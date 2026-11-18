import { describe, expect, test } from 'vitest'

import { chatId, chatUrl } from './route'

describe('chat address', () => {
  test('a conversation is `<base>chats/<id>`, its id escaped', () => {
    expect(chatUrl('20261117T100000Z-a1a1a1', '/')).toBe('/chats/20261117T100000Z-a1a1a1')
    expect(chatUrl('a b', '/sub/')).toBe('/sub/chats/a%20b')
  })

  test('reads the id back, with or without a trailing slash', () => {
    expect(chatId('/chats/a%20b', '/')).toBe('a b')
    expect(chatId('/sub/chats/x/', '/sub/')).toBe('x')
  })

  test('other addresses are not a conversation', () => {
    for (const path of ['/', '/chats', '/runs/x', '/sub/chats/x', '/chats/x/y']) expect(chatId(path, '/')).toBeNull()
  })
})
