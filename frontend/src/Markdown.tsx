import type { ReactNode } from 'react'

// Minimal markdown for the LLM brief. It is untrusted text, so it is rendered as React elements
// (headings, bullets, **bold**, `code`) and never as raw HTML.
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) return <b key={i}>{part.slice(2, -2)}</b>
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) return <code key={i}>{part.slice(1, -1)}</code>
    return part
  })
}

export function Markdown({ source }: { source: string }) {
  const blocks: ReactNode[] = []
  let bullets: string[] = []
  const flush = () => {
    if (bullets.length) {
      blocks.push(<ul key={blocks.length}>{bullets.map((b, i) => <li key={i}>{inline(b)}</li>)}</ul>)
      bullets = []
    }
  }
  for (const line of source.split('\n')) {
    const bullet = /^\s*[-*]\s+(.*)/.exec(line)
    const heading = /^(#{1,3})\s+(.*)/.exec(line)
    if (bullet) { bullets.push(bullet[1]); continue }
    flush()
    if (heading) {
      const Tag = heading[1].length === 1 ? 'h3' : 'h4'
      blocks.push(<Tag key={blocks.length}>{inline(heading[2])}</Tag>)
    } else if (line.trim()) {
      blocks.push(<p key={blocks.length}>{inline(line)}</p>)
    }
  }
  flush()
  return <div className="brief">{blocks}</div>
}
