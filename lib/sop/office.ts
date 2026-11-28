// Reading text out of Office files and other plain formats without any library: .docx/.xlsx/.pptx are zip files of XML.
// Pure (no DOM): uses DecompressionStream, which exists in browsers and Node 18+.

interface ZipEntry { name: string; method: number; csize: number; offset: number }

const u16 = (b: Uint8Array, o: number) => (b[o] ?? 0) | ((b[o + 1] ?? 0) << 8);
const u32 = (b: Uint8Array, o: number) => (u16(b, o) | (u16(b, o + 2) << 16)) >>> 0;

function entries(zip: Uint8Array): ZipEntry[] {
  // end-of-central-directory record: last 22+ bytes
  let eocd = -1;
  for (let i = zip.length - 22; i >= Math.max(0, zip.length - 65_558); i--) {
    if (u32(zip, i) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error('Not a valid zip/Office file.');
  const count = u16(zip, eocd + 10);
  let p = u32(zip, eocd + 16);
  const out: ZipEntry[] = [];
  for (let i = 0; i < count && u32(zip, p) === 0x02014b50; i++) {
    const nameLen = u16(zip, p + 28), extra = u16(zip, p + 30), comment = u16(zip, p + 32);
    out.push({
      name: new TextDecoder().decode(zip.subarray(p + 46, p + 46 + nameLen)),
      method: u16(zip, p + 10),
      csize: u32(zip, p + 20),
      offset: u32(zip, p + 42),
    });
    p += 46 + nameLen + extra + comment;
  }
  return out;
}

async function inflateRaw(data: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([data as BlobPart]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

export class Zip {
  private constructor(private bytes: Uint8Array, private list: ZipEntry[]) {}
  static open(bytes: Uint8Array) { return new Zip(bytes, entries(bytes)); }
  names() { return this.list.map((e) => e.name); }
  async text(name: string): Promise<string | null> {
    const e = this.list.find((x) => x.name === name);
    if (!e) return null;
    const o = e.offset;
    const start = o + 30 + u16(this.bytes, o + 26) + u16(this.bytes, o + 28);
    const raw = this.bytes.subarray(start, start + e.csize);
    if (e.method === 0) return new TextDecoder().decode(raw);
    if (e.method === 8) return new TextDecoder().decode(await inflateRaw(raw));
    throw new Error(`Unsupported zip compression (${e.method}).`);
  }
}

const ENT: Record<string, string> = { nbsp: ' ', amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" };
export const decodeXml = (s: string) =>
  s.replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);/gi, (_, e: string) =>
    e[0] === '#' ? String.fromCodePoint(e[1]?.toLowerCase() === 'x' ? Number.parseInt(e.slice(2), 16) : Number.parseInt(e.slice(1), 10)) : (ENT[e.toLowerCase()] ?? ''));

/** word/document.xml -> paragraphs; table cells joined with " | ". */
export function docxText(xml: string): string {
  const rows = xml.split(/<\/w:tr>/);
  const para = (frag: string) =>
    frag
      .split(/<\/w:p>/)
      .map((p) =>
        decodeXml(
          [...p.matchAll(/<w:t(?:\s[^>]*)?>([\s\S]*?)<\/w:t>|<w:tab\s*\/>|<w:br\s*\/>/g)]
            .map((m) => (m[1] !== undefined ? m[1] : m[0].startsWith('<w:tab') ? '\t' : '\n'))
            .join(''),
        ).trim(),
      )
      .filter(Boolean);
  const out: string[] = [];
  for (const r of rows) {
    const tr = r.search(/<w:tr[\s>]/);
    if (tr < 0) { out.push(...para(r)); continue; }
    out.push(...para(r.slice(0, tr))); // paragraphs before the table row
    const cells = r.slice(tr).split(/<\/w:tc>/).map((c) => para(c).join(' ')).filter(Boolean);
    if (cells.length) out.push(cells.join(' | '));
  }
  return out.join('\n');
}

export function pptxText(xml: string): string {
  return xml
    .split(/<\/a:p>/)
    .map((p) => decodeXml([...p.matchAll(/<a:t>([\s\S]*?)<\/a:t>/g)].map((m) => m[1]).join('')).trim())
    .filter(Boolean)
    .join('\n');
}

const colIndex = (ref: string) => [...(/^[A-Z]+/.exec(ref)?.[0] ?? 'A')].reduce((n, c) => n * 26 + c.charCodeAt(0) - 64, 0) - 1;

export function xlsxSheet(xml: string, shared: string[]): string {
  const rows: string[] = [];
  for (const row of xml.matchAll(/<row\b[^>]*>([\s\S]*?)<\/row>/g)) {
    const cells: string[] = [];
    for (const c of (row[1] ?? '').matchAll(/<c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g)) {
      const attrs = c[1] ?? '', body = c[2] ?? '';
      const t = /\bt="(\w+)"/.exec(attrs)?.[1];
      const v = /<v>([\s\S]*?)<\/v>/.exec(body)?.[1];
      const inline = [...body.matchAll(/<t(?:\s[^>]*)?>([\s\S]*?)<\/t>/g)].map((m) => m[1]).join('');
      const val = t === 's' && v !== undefined ? (shared[Number(v)] ?? '') : t === 'inlineStr' ? decodeXml(inline) : decodeXml(v ?? '');
      cells[colIndex(/\br="([A-Z]+\d+)"/.exec(attrs)?.[1] ?? 'A1')] = val;
    }
    const line = Array.from(cells, (x) => x ?? '').join(' | ').replace(/(\s\|\s)+$/, '').trim();
    if (line) rows.push(line);
  }
  return rows.join('\n');
}

export const sharedStrings = (xml: string) =>
  [...xml.matchAll(/<si>([\s\S]*?)<\/si>/g)].map((m) => decodeXml([...(m[1] ?? '').matchAll(/<t(?:\s[^>]*)?>([\s\S]*?)<\/t>/g)].map((x) => x[1]).join('')));

export async function officeText(bytes: Uint8Array, kind: 'docx' | 'xlsx' | 'pptx'): Promise<string> {
  const zip = Zip.open(bytes);
  if (kind === 'docx') {
    const xml = await zip.text('word/document.xml');
    if (!xml) throw new Error('This .docx has no document body.');
    return docxText(xml);
  }
  if (kind === 'pptx') {
    const slides = zip.names().filter((n) => /^ppt\/slides\/slide\d+\.xml$/.test(n)).sort((a, b) => Number(/\d+/.exec(a.slice(10))?.[0]) - Number(/\d+/.exec(b.slice(10))?.[0]));
    const parts: string[] = [];
    for (const [i, n] of slides.entries()) parts.push(`--- slide ${i + 1} ---\n${pptxText((await zip.text(n)) ?? '')}`);
    return parts.join('\n');
  }
  const shared = sharedStrings((await zip.text('xl/sharedStrings.xml')) ?? '');
  const names = [...((await zip.text('xl/workbook.xml')) ?? '').matchAll(/<sheet\b[^>]*\bname="([^"]*)"/g)].map((m) => decodeXml(m[1] ?? ''));
  const sheets = zip.names().filter((n) => /^xl\/worksheets\/sheet\d+\.xml$/.test(n)).sort((a, b) => Number(/\d+/.exec(a.slice(13))?.[0]) - Number(/\d+/.exec(b.slice(13))?.[0]));
  const parts: string[] = [];
  for (const [i, n] of sheets.entries()) parts.push(`--- sheet: ${names[i] ?? i + 1} ---\n${xlsxSheet((await zip.text(n)) ?? '', shared)}`);
  return parts.join('\n');
}

const RTF_SKIP = new Set(['fonttbl', 'colortbl', 'stylesheet', 'info', 'pict', 'header', 'footer', 'themedata', 'datastore']);

export function rtfText(rtf: string): string {
  let out = '', skipDepth = -1, depth = 0;
  for (let i = 0; i < rtf.length; i++) {
    const c = rtf[i];
    if (c === '{') {
      depth++;
      const m = /^\{\\(\*\\)?([a-z]+)/i.exec(rtf.slice(i, i + 40));
      if (skipDepth < 0 && m && (m[1] || RTF_SKIP.has((m[2] ?? '').toLowerCase()))) skipDepth = depth;
    } else if (c === '}') {
      if (depth === skipDepth) skipDepth = -1;
      depth--;
    } else if (skipDepth >= 0) {
      continue;
    } else if (c === '\\') {
      const m = /^\\([a-z]+)(-?\d+)? ?/i.exec(rtf.slice(i, i + 40));
      if (m) {
        if (m[1] === 'par' || m[1] === 'line') out += '\n';
        else if (m[1] === 'tab') out += '\t';
        i += m[0].length - 1;
      } else if (rtf[i + 1] === "'") {
        out += String.fromCharCode(Number.parseInt(rtf.slice(i + 2, i + 4), 16) || 63);
        i += 3;
      } else if (rtf[i + 1] === '\\' || rtf[i + 1] === '{' || rtf[i + 1] === '}') {
        out += rtf[++i];
      } else i++;
    } else if (c !== '\n' && c !== '\r') out += c;
  }
  return out.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
}

export function htmlText(html: string): string {
  return decodeXml(
    html
      .replace(/<(script|style|noscript)[\s\S]*?<\/\1>/gi, '')
      .replace(/<\/(p|div|li|tr|h[1-6]|br)>|<br\s*\/?>/gi, '\n')
      .replace(/<\/t[dh]>/gi, ' | ')
      .replace(/<[^>]+>/g, ''),
  ).replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
}
