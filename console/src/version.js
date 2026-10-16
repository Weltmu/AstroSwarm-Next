/* 页面右上角那个版本号（单独放一个文件，为了让冒烟能直接 import 这份逻辑 ——
   App.jsx 里有浏览器 API，Node 里跑冒烟拉不动整个 App，和 nav.js 是同一个理由）。

   规则（老板定的，不许改回去）：
     1. 优先 GET /api/status 的 version（Linux 无头端自己的版本号）
     2. 取不到再看 /api/config 的 version
     3. 两处都没有 → 显示「版本读取中」，**绝不退回某个写死的数字**
   ⚠ 这里以前写死 '1.1.0'（那是网页自己的旧编号），老板会当成产品版本号看，
     而产品版本是另一个序列（1.2.x）。一个数字都不许硬编码。 */

export const VERSION_PENDING = '版本读取中';

export function headlessVersion(status, config) {
  const raw = String((status && status.version) || (config && config.version) || '').trim();
  if (!raw) return '';
  return raw.startsWith('v') ? raw : `v${raw}`;
}

/** 页面上直接用的那一串：拿不到就是「版本读取中」。 */
export function versionLabel(status, config) {
  return headlessVersion(status, config) || VERSION_PENDING;
}
