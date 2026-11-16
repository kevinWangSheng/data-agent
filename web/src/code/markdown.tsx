// Copied from SignalPilot (https://github.com/SignalPilot-Labs/SignalPilot, commit f5eee22,
// signalpilot/web/app/evals/_components/Markdown.tsx), Apache License 2.0; see NOTICE.
// Modified: dropped "use client" and `fmtNum`; the wrapper's class is this app's `md` plus an
// optional extra class.
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Markdown block styled by index.css `.md`. */
export function Md({ children, className }: { children: string; className?: string }) {
  return (
    <div className={className ? `md ${className}` : "md"}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
