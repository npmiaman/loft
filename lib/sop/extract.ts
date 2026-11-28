// Turn an uploaded file into text, entirely inside the browser. Nothing is uploaded anywhere except the final text, which goes
// to the LLM when the plan is built. Runs in an extension page (needs Worker/canvas): the side panel.
import { htmlText, officeText, rtfText } from './office';

export interface Extracted {
  text: string;
  via: string;
  note?: string;
}

export const MAX_FILE_BYTES = 30 * 1024 * 1024;
const MAX_PDF_PAGES = 60;
const MAX_OCR_PAGES = 12;
const ext = (n: string) => (/\.([a-z0-9]+)$/i.exec(n)?.[1] ?? '').toLowerCase();

export const ACCEPT =
  '.txt,.md,.markdown,.csv,.tsv,.json,.html,.htm,.rtf,.docx,.xlsx,.pptx,.pdf,.png,.jpg,.jpeg,.webp,.gif,.bmp,image/*,application/pdf';

const IMAGE = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'heic']);
const PLAIN = new Set(['txt', 'md', 'markdown', 'csv', 'tsv', 'log', 'text']);

export async function extractFile(file: File, onStatus: (s: string) => void = () => {}): Promise<Extracted> {
  if (file.size > MAX_FILE_BYTES) throw new Error(`${file.name} is ${(file.size / 1048576).toFixed(0)} MB; the limit is ${MAX_FILE_BYTES / 1048576} MB.`);
  const e = ext(file.name);
  const type = file.type;

  if (IMAGE.has(e) || type.startsWith('image/')) {
    if (e === 'heic' || type === 'image/heic') throw new Error("HEIC photos can't be read in the browser. Export it as JPG or PNG first.");
    onStatus(`Reading text from ${file.name} (OCR)…`);
    const text = await ocr(file, onStatus);
    if (text.trim().length < 20) throw new Error(`Couldn't find readable text in ${file.name}. OCR works on printed or typed text; handwriting and very small, blurry or angled photos usually fail.`);
    return { text, via: 'image-ocr', note: 'OCR can misread characters; check the text.' };
  }
  if (e === 'pdf' || type === 'application/pdf') return pdf(file, onStatus);
  if (e === 'docx' || e === 'xlsx' || e === 'pptx') {
    const text = await officeText(new Uint8Array(await file.arrayBuffer()), e);
    if (!text.trim()) throw new Error(`${file.name} has no text in it (images inside Office files aren't read; export them as images or PDF).`);
    return { text, via: e };
  }
  if (['doc', 'xls', 'ppt', 'pages', 'numbers', 'key'].includes(e)) {
    throw new Error(`.${e} files can't be read. Save it as .docx, .pdf or plain text and upload that.`);
  }
  const raw = await file.text();
  if (e === 'html' || e === 'htm' || type === 'text/html') return { text: htmlText(raw), via: 'html' };
  if (e === 'rtf') return { text: rtfText(raw), via: 'rtf' };
  if (e === 'json') {
    try { return { text: JSON.stringify(JSON.parse(raw), null, 2), via: 'json' }; } catch { return { text: raw, via: 'text' }; }
  }
  if (PLAIN.has(e) || type.startsWith('text/') || looksLikeText(raw)) return { text: raw, via: e === 'csv' || e === 'tsv' ? e : 'text' };
  throw new Error(`Don't know how to read ${file.name}. Try PDF, Word (.docx), a spreadsheet, plain text or an image.`);
}

const looksLikeText = (s: string) => s.length > 0 && (s.slice(0, 4000).match(/[\u0000-\u0008\u000e-\u001f�]/g)?.length ?? 0) < 8;

// ---------------------------------------------------------------- PDF
async function pdf(file: File, onStatus: (s: string) => void): Promise<Extracted> {
  const lib = await import('pdfjs-dist/build/pdf.min.mjs');
  lib.GlobalWorkerOptions.workerSrc = chrome.runtime.getURL('ocr/pdf.worker.min.mjs');
  const doc = await lib.getDocument({ data: new Uint8Array(await file.arrayBuffer()) }).promise;
  const pages = Math.min(doc.numPages, MAX_PDF_PAGES);
  const parts: string[] = [];
  for (let i = 1; i <= pages; i++) {
    onStatus(`Reading ${file.name} page ${i}/${pages}…`);
    const page = await doc.getPage(i);
    const tc = await page.getTextContent();
    let line = '';
    const lines: string[] = [];
    for (const it of tc.items as { str?: string; hasEOL?: boolean }[]) {
      line += it.str ?? '';
      if (it.hasEOL) { lines.push(line); line = ''; }
    }
    if (line) lines.push(line);
    parts.push(lines.join('\n').trim());
  }
  const note = doc.numPages > pages ? `Only the first ${pages} of ${doc.numPages} pages were read.` : undefined;
  const text = parts.join('\n\n');
  // a scanned PDF has pages but (almost) no text layer: render the pages and OCR them
  if (text.replace(/\s/g, '').length >= 40 * Math.min(pages, 3)) return { text, via: 'pdf', note };

  const ocrPages = Math.min(pages, MAX_OCR_PAGES);
  const out: string[] = [];
  for (let i = 1; i <= ocrPages; i++) {
    onStatus(`Scanned PDF: OCR page ${i}/${ocrPages}…`);
    const page = await doc.getPage(i);
    const vp = page.getViewport({ scale: 2 });
    const canvas = document.createElement('canvas');
    canvas.width = Math.ceil(vp.width);
    canvas.height = Math.ceil(vp.height);
    await page.render({ canvas, viewport: vp }).promise;
    out.push(await ocr(canvas, onStatus));
  }
  const joined = out.join('\n\n');
  if (joined.trim().length < 20) throw new Error(`${file.name} looks scanned and OCR found no readable text.`);
  const skipped = doc.numPages > ocrPages ? ` Scanned PDF: only the first ${ocrPages} of ${doc.numPages} pages were read.` : '';
  return { text: joined, via: 'pdf-ocr', note: `OCR can misread characters; check the text.${skipped}` };
}

// ---------------------------------------------------------------- OCR (Tesseract, bundled in the extension; English)
let worker: Promise<import('tesseract.js').Worker> | null = null;
let progress: (s: string) => void = () => {};

function getWorker() {
  worker ??= import('tesseract.js').then((T) => {
    const base = chrome.runtime.getURL('ocr/');
    return T.createWorker('eng', 1, {
      workerPath: base + 'worker.min.js',
      corePath: base + 'tesseract-core-simd-lstm.wasm.js',
      langPath: base,
      workerBlobURL: false,
      gzip: true,
      logger: (m: { status: string; progress: number }) => progress(`OCR: ${m.status} ${Math.round((m.progress ?? 0) * 100)}%`),
    });
  });
  worker.catch(() => { worker = null; });
  return worker;
}

const withTimeout = <T,>(p: Promise<T>, ms: number, what: string): Promise<T> =>
  new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error(what)), ms);
    p.then((v) => (clearTimeout(t), resolve(v)), (e) => (clearTimeout(t), reject(e)));
  });

async function ocr(img: File | HTMLCanvasElement, onStatus: (s: string) => void): Promise<string> {
  progress = onStatus;
  try {
    // a crashed engine can fail inside its worker without rejecting anything, so every step has a deadline
    const w = await withTimeout(getWorker(), 60_000, 'The OCR engine did not start. Reload the extension; if it keeps happening, paste the SOP text instead.');
    const { data } = await withTimeout(w.recognize(img), 150_000, 'OCR took too long on this image. Try a smaller or clearer one, or paste the text.');
    return data.text.replace(/[ \t]+\n/g, '\n').trim();
  } catch (e) {
    const w = worker;
    worker = null;
    w?.then((x) => x.terminate()).catch(() => {});
    throw e;
  }
}
