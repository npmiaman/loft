// Copies the OCR engine and the PDF worker out of node_modules into public/ocr so they ship inside the extension.
// MV3 forbids remote code, so nothing can be fetched from a CDN at runtime. Idempotent; runs before dev/build/zip.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const out = path.join(root, 'public', 'ocr');
const files = [
  ['node_modules/tesseract.js/dist/worker.min.js', 'worker.min.js'],
  ['node_modules/tesseract.js-core/tesseract-core-simd-lstm.wasm.js', 'tesseract-core-simd-lstm.wasm.js'],
  ['node_modules/@tesseract.js-data/eng/4.0.0_best_int/eng.traineddata.gz', 'eng.traineddata.gz'],
  ['node_modules/pdfjs-dist/build/pdf.worker.min.mjs', 'pdf.worker.min.mjs'],
];
fs.mkdirSync(out, { recursive: true });
for (const [from, to] of files) {
  const src = path.join(root, from);
  if (!fs.existsSync(src)) {
    console.warn(`vendor-ocr: ${from} missing (run npm install); skipping`);
    continue;
  }
  const dst = path.join(out, to);
  if (!fs.existsSync(dst) || fs.statSync(dst).size !== fs.statSync(src).size) fs.copyFileSync(src, dst);
}
