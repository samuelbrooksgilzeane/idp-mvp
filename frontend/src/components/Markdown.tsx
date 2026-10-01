import { Fragment, type ReactNode } from "react";

/**
 * Renders the small markdown subset assistant answers use: paragraphs, headings, bullet and
 * numbered lists, tables, **bold** and `code`. Builds React elements only, so answer text can
 * never inject HTML; links stay plain text.
 */
export function Markdown({ text }: { text: string }) {
  return <div className="markdown">{blocks(text.replace(/\r\n/g, "\n").split("\n"))}</div>;
}

function blocks(lines: string[]): ReactNode[] {
  const out: ReactNode[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
    } else if (isTableRow(line) && index + 1 < lines.length && isSeparator(lines[index + 1])) {
      const rows: string[][] = [];
      const header = cells(line);
      index += 2;
      while (index < lines.length && isTableRow(lines[index])) rows.push(cells(lines[index++]));
      out.push(
        <div className="markdown-table" key={out.length}>
          <table>
            <thead><tr>{header.map((cell, i) => <th key={i}>{inline(cell)}</th>)}</tr></thead>
            <tbody>
              {rows.map((row, r) => (
                <tr key={r}>{header.map((_, c) => <td key={c}>{inline(row[c] ?? "")}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
    } else if (/^\s*([-*•]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items: string[] = [];
      while (index < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[index])) {
        items.push(lines[index++].replace(/^\s*([-*•]|\d+[.)])\s+/, ""));
      }
      const children = items.map((item, i) => <li key={i}>{inline(item)}</li>);
      out.push(ordered ? <ol key={out.length}>{children}</ol> : <ul key={out.length}>{children}</ul>);
    } else if (/^#{1,6}\s+/.test(line)) {
      out.push(<p className="markdown-heading" key={out.length}>{inline(line.replace(/^#+\s+/, ""))}</p>);
      index += 1;
    } else {
      const paragraph: string[] = [];
      while (index < lines.length && lines[index].trim() && !startsBlock(lines, index)) {
        paragraph.push(lines[index++]);
      }
      out.push(<p key={out.length}>{paragraph.map((part, i) => (
        <Fragment key={i}>{i > 0 ? <br /> : null}{inline(part)}</Fragment>
      ))}</p>);
    }
  }
  return out;
}

function startsBlock(lines: string[], index: number): boolean {
  const line = lines[index];
  return /^\s*([-*•]|\d+[.)])\s+/.test(line) || /^#{1,6}\s+/.test(line)
    || (isTableRow(line) && index + 1 < lines.length && isSeparator(lines[index + 1]));
}

const isTableRow = (line: string) => /^\s*\|.*\|\s*$/.test(line);
const isSeparator = (line: string) => /^\s*\|(\s*:?-{2,}:?\s*\|)+\s*$/.test(line);
const cells = (line: string) => line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());

/** **bold** and `code`; everything else is literal text. */
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i}>{part.slice(1, -1)}</code>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}
