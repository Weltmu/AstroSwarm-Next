/* 离屏截图专用 vite 配置（只在验收时用，不进构建）：
   root = 项目根（/src/main.jsx 才能解析），只在 URL 带 #shots 时插一行假后端脚本。
   正常 `vite` 一个字都不变；截图机位 http://127.0.0.1:5199/#shots。
   为什么不改 index.html：那是构建产物的一部分，改了就得记得还原（以前踩过忘还原）。 */
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const MOCK = resolve(here, '_shots', 'mockapi.js');

function shotsMock() {
  return {
    name: 'astroswarm-shots-mock',
    apply: 'serve',
    configureServer(server) {
      /* 把 _shots/mockapi.js 映射到 /_shots/mockapi.js 这个 URL
         （它不在 public/ 里，dev server 默认取不到） */
      server.middlewares.use((req, res, next) => {
        if (!req.url || req.url.split('?')[0] !== '/_shots/mockapi.js') return next();
        res.setHeader('Content-Type', 'text/javascript; charset=utf-8');
        res.end(readFileSync(MOCK));
      });
    },
    transformIndexHtml(html) {
      return html.replace(
        '<div id="root"></div>',
        '<script>if(location.hash.indexOf("shots")>=0){document.write(\'<script src="/_shots/mockapi.js"><\\/script>\');}</script>\n    <div id="root"></div>',
      );
    },
  };
}

export default defineConfig({
  plugins: [react(), shotsMock()],
  server: {
    host: '127.0.0.1',
    port: 5199,
    strictPort: true,
    fs: { allow: [here] },
  },
});
