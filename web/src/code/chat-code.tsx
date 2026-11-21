// Copied from SignalPilot (https://github.com/SignalPilot-Labs/SignalPilot, commit f5eee22,
// signalpilot/web/components/chat/chat-code.tsx), Apache License 2.0; see NOTICE.
// Modified: dropped "use client" and the Python parser (only SQL and plain text here); styles come
// from this app's `code` and `tok-*` classes (index.css) instead of upstream's colours; the height
// cap is a pixel value; `CopyButton` uses this app's button class; the block's comment no longer
// mentions upstream's notebook.
import { Check, Copy } from "lucide-react";
import { memo, useMemo, useState, type ReactNode } from "react";
import { classHighlighter, highlightTree } from "@lezer/highlight";
import { StandardSQL } from "@codemirror/lang-sql";

export type ChatCodeLanguage = "sql" | "shell" | "text";

const SHELL_KEYWORDS = new Set([
  "if",
  "then",
  "else",
  "elif",
  "fi",
  "for",
  "in",
  "do",
  "done",
  "while",
  "until",
  "case",
  "esac",
  "function",
  "export",
  "local",
  "return",
  "set",
  "unset",
  "exit",
]);

// Lightweight shell tokenizer using the existing `tok-*` classes (no extra parser dependency).
const SHELL_TOKEN =
  /(#[^\n]*)|('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")|(\$\{[^}]+\}|\$[A-Za-z_][A-Za-z0-9_]*)|(\|\||&&|>>|<<|[|;&><])|((?:^|(?<=\s))--?[A-Za-z0-9_-]+)|(\b[A-Za-z_][A-Za-z0-9_-]*\b)|(\b\d+(?:\.\d+)?\b)/g;

function highlightShell(code: string): ReactNode[] {
  const children: ReactNode[] = [];
  let cursor = 0;
  for (const m of code.matchAll(SHELL_TOKEN)) {
    const start = m.index ?? 0;
    const text = m[0];
    let cls: string | null = null;
    if (m[1]) cls = "tok-comment";
    else if (m[2]) cls = "tok-string";
    else if (m[3]) cls = "tok-typeName";
    else if (m[4]) cls = "tok-operator";
    else if (m[5]) cls = "tok-typeName";
    else if (m[6]) cls = SHELL_KEYWORDS.has(text) ? "tok-keyword" : null;
    else if (m[7]) cls = "tok-number";
    if (!cls) continue;
    if (start > cursor) children.push(code.slice(cursor, start));
    children.push(
      <span key={`${start}-${start + text.length}`} className={cls}>
        {text}
      </span>,
    );
    cursor = start + text.length;
  }
  if (cursor < code.length) children.push(code.slice(cursor));
  return children.length ? children : [code];
}

function highlight(code: string, language: ChatCodeLanguage): ReactNode[] {
  if (language === "shell") return highlightShell(code);
  const parser = language === "sql" ? StandardSQL.language.parser : null;
  if (!parser) return [code];
  try {
    const tree = parser.parse(code);
    const children: ReactNode[] = [];
    let cursor = 0;
    highlightTree(tree, classHighlighter, (from, to, classes) => {
      if (from > cursor) children.push(code.slice(cursor, from));
      children.push(
        <span key={`${from}-${to}`} className={classes}>
          {code.slice(from, to)}
        </span>,
      );
      cursor = to;
    });
    if (cursor < code.length) children.push(code.slice(cursor));
    return children;
  } catch {
    return [code];
  }
}

export function CopyButton({
  text,
  label = "Copy",
}: {
  text: string;
  label?: string;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label={label}
      onClick={() => {
        void navigator.clipboard
          .writeText(text)
          .then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          })
          .catch(() => undefined);
      }}
      className="btn btn-sm"
    >
      {copied ? <Check className="h-3 w-3 text-ok" /> : <Copy className="h-3 w-3" />}
      {copied ? "Copied" : label}
    </button>
  );
}

/**
 * Static, dependency-light highlighted code block. Uses the lezer SQL parser
 * instead of mounting a CodeMirror editor per block.
 */
export const ChatCode = memo(function ChatCode({
  code,
  language,
  maxHeight = 420,
}: {
  code: string;
  language: ChatCodeLanguage;
  maxHeight?: number;
}) {
  const trimmed = code.replace(/\s+$/, "");
  const children = useMemo(() => highlight(trimmed, language), [
    trimmed,
    language,
  ]);
  return (
    <pre className="code" style={{ maxHeight }}>
      <code>{children}</code>
    </pre>
  );
});
