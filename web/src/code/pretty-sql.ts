// Copied from SignalPilot (https://github.com/SignalPilot-Labs/SignalPilot, commit f5eee22,
// signalpilot/web/lib/pretty-sql.ts), Apache License 2.0; see NOTICE. Unmodified.
import { format as formatSql } from "sql-formatter";

/** Uppercased, indented SQL for display; returns the input on parse failure. */
export function prettySql(sql: string): string {
  try {
    return formatSql(sql, { language: "postgresql", keywordCase: "upper" });
  } catch {
    return sql;
  }
}
