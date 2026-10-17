import { defineConfig, type Plugin } from 'vite';
import { viteSingleFile } from 'vite-plugin-singlefile';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import zlib from 'node:zlib';
import type { IncomingMessage } from 'node:http';
import { computeSourceHash, HASH_REGEX } from './scripts/hash.mjs';

function mycroMockPlugin(): Plugin {
  let macroStatus = 'idle';
  let capturePending = false;
  let captureReadyAt = 0;
  let cachedPng: Buffer | null = null;

  function getMockPng(): Buffer {
    if (cachedPng) return cachedPng;
    const w = 3024;
    const h = 1964;
    const rowSize = 1 + w * 3;
    const raw = Buffer.alloc(rowSize * h);
    let offset = 0;
    // Standard 8 primary test bars (White, Yellow, Cyan, Green, Magenta, Red, Blue, Black)
    const primaryBars = [
      [255, 255, 255], // 100% White (#ffffff)
      [255, 255, 0],   // Yellow     (#ffff00)
      [0, 255, 255],   // Cyan       (#00ffff)
      [0, 255, 0],     // Green      (#00ff00)
      [255, 0, 255],   // Magenta    (#ff00ff)
      [255, 0, 0],     // Red        (#ff0000)
      [0, 0, 255],     // Blue       (#0000ff)
      [0, 0, 0]        // 0% Black   (#000000)
    ];

    for (let y = 0; y < h; y++) {
      raw[offset++] = 0; // filter byte
      for (let x = 0; x < w; x++) {
        if (y < 700) {
          // Top: 8 standard primary color bars
          const barIdx = Math.min(7, Math.floor(x / (w / 8)));
          const [r, g, b] = primaryBars[barIdx];
          raw[offset++] = r;
          raw[offset++] = g;
          raw[offset++] = b;
        } else if (y < 1400) {
          // Middle: 6 discrete calibrated grayscale steps (0%, 20%, 40%, 60%, 80%, 100%)
          const stepIdx = Math.min(5, Math.floor(x / (w / 6)));
          const v = Math.round((stepIdx * 255) / 5);
          raw[offset++] = v;
          raw[offset++] = v;
          raw[offset++] = v;
        } else {
          // Bottom: Linear grayscale ramp from pure 0x000000 to pure 0xffffff
          const gray = Math.floor((x * 255) / w);
          raw[offset++] = gray;
          raw[offset++] = gray;
          raw[offset++] = gray;
        }
      }
    }
    const compressed = zlib.deflateSync(raw, { level: 1 });

    function makeChunk(type: string, data: Buffer): Buffer {
      const len = Buffer.alloc(4);
      len.writeUInt32BE(data.length, 0);
      const typeBuf = Buffer.from(type, 'ascii');
      const crc = Buffer.alloc(4);
      const crcVal = zlib.crc32(Buffer.concat([typeBuf, data]));
      crc.writeUInt32BE(crcVal, 0);
      return Buffer.concat([len, typeBuf, data, crc]);
    }

    const ihdr = Buffer.alloc(13);
    ihdr.writeUInt32BE(w, 0);
    ihdr.writeUInt32BE(h, 4);
    ihdr[8] = 8;
    ihdr[9] = 2; // RGB
    ihdr[10] = 0;
    ihdr[11] = 0;
    ihdr[12] = 0;

    const header = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    cachedPng = Buffer.concat([
      header,
      makeChunk('IHDR', ihdr),
      makeChunk('IDAT', compressed),
      makeChunk('IEND', Buffer.alloc(0))
    ]);
    return cachedPng;
  }

  function readBody(req: IncomingMessage): Promise<string> {
    return new Promise((resolve) => {
      let data = '';
      req.on('data', chunk => { data += chunk; });
      req.on('end', () => resolve(data));
    });
  }

  function extractScript(body: string, contentType?: string): string {
    if (contentType?.includes('application/x-www-form-urlencoded')) {
      const params = new URLSearchParams(body);
      return params.get('script') || '';
    }
    return body;
  }

  function compileWithMacroPy(script: string): { ok: boolean; output: string } {
    const repoRoot = path.resolve(__dirname, '..');
    const pyCode = `
import sys
sys.path.insert(0, 'src/lib')
sys.path.insert(0, 'tests/unit')
from macro import Macro
from mock_hid import MockHID
m = Macro(MockHID())
code = sys.stdin.read()
bc, err = m.compile(code)
if err:
    sys.stderr.write(f"Compile Error: {err}")
    sys.exit(1)
sys.stdout.write(m.disassemble(bc))
`;
    try {
      const res = spawnSync('python3', ['-c', pyCode], {
        input: script,
        cwd: repoRoot,
        encoding: 'utf-8',
        timeout: 5000
      });
      if (res.status === 0) {
        return { ok: true, output: res.stdout };
      }
      return { ok: false, output: res.stderr || 'Compile failed' };
    } catch (e: any) {
      return { ok: false, output: e.message || 'Compiler execution error' };
    }
  }

  return {
    name: 'mycro-mock-plugin',
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const url = req.url?.split('?')[0];

        if (url === '/macro/status' && req.method === 'GET') {
          res.setHeader('Content-Type', 'text/plain; charset=utf-8');
          res.end(macroStatus);
          return;
        }

        if (url === '/macro/abort' && req.method === 'POST') {
          if (macroStatus === 'running') macroStatus = 'aborted';
          res.setHeader('Content-Type', 'text/plain; charset=utf-8');
          res.end('Abort requested');
          return;
        }

        if (url === '/compile' && req.method === 'POST') {
          const raw = await readBody(req);
          const script = extractScript(raw, req.headers['content-type']);
          const resCompile = compileWithMacroPy(script);
          if (resCompile.ok) {
            res.setHeader('Content-Type', 'text/plain; charset=utf-8');
            res.end(resCompile.output);
          } else {
            res.statusCode = 400;
            res.setHeader('Content-Type', 'text/plain; charset=utf-8');
            res.end(resCompile.output);
          }
          return;
        }

        if (url === '/macro' && req.method === 'POST') {
          const raw = await readBody(req);
          const script = extractScript(raw, req.headers['content-type']);
          const resCompile = compileWithMacroPy(script);
          if (!resCompile.ok) {
            res.statusCode = 400;
            res.setHeader('Content-Type', 'text/plain; charset=utf-8');
            res.end(resCompile.output);
            return;
          }
          macroStatus = 'running';
          setTimeout(() => {
            if (macroStatus === 'running') macroStatus = 'done';
          }, 1500);
          res.setHeader('Content-Type', 'text/plain; charset=utf-8');
          res.end('Started: 42B');
          return;
        }

        if (url === '/img/capture') {
          if (req.method === 'POST') {
            capturePending = true;
            captureReadyAt = Date.now() + 300;
            res.setHeader('Content-Type', 'text/plain; charset=utf-8');
            res.end('started');
            return;
          }
          if (req.method === 'GET') {
            if (capturePending && Date.now() < captureReadyAt) {
              res.statusCode = 204;
              res.end();
              return;
            }
            capturePending = false;
            const png = getMockPng();
            res.setHeader('Content-Type', 'image/png');
            res.setHeader('Content-Length', String(png.length));
            res.end(png);
            return;
          }
        }

        next();
      });
    }
  };
}

function mycroOutputPlugin(): Plugin {
  return {
    name: 'mycro-output-plugin',
    closeBundle() {
      const distHtml = path.resolve(__dirname, 'dist', 'index.html');
      const targetHtml = path.resolve(__dirname, '..', 'src', 'index.html');
      const targetGz = path.resolve(__dirname, '..', 'src', 'index.html.gz');
      if (!fs.existsSync(distHtml)) return;

      const hash = computeSourceHash(__dirname);
      let html = fs.readFileSync(distHtml, 'utf-8');
      html = html.replace(HASH_REGEX, '').trimEnd();
      html += `\n<!-- MYCRO_BUILD_HASH:${hash} -->\n`;

      fs.writeFileSync(targetHtml, html, 'utf-8');
      const gzipped = zlib.gzipSync(Buffer.from(html, 'utf-8'), { level: 9 });
      fs.writeFileSync(targetGz, gzipped);
      console.log(`  [mycro] Synced bundle to src/index.html (${(html.length / 1024).toFixed(1)} KB) & src/index.html.gz (${(gzipped.length / 1024).toFixed(1)} KB, hash: ${hash.slice(0, 12)})`);
    }
  };
}

export default defineConfig({
  plugins: [viteSingleFile(), mycroMockPlugin(), mycroOutputPlugin()],
  build: {
    target: 'esnext',
    minify: 'esbuild',
    cssMinify: true
  },
  server: {
    port: 5173
  }
});
