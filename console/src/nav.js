/* 导航结构（4 组）：机器人 / 工坊 / 人格 / 设置 —— **与桌面端一一对应**，有测试锁着。
   单独放一个文件是为了让测试能直接 import 这份结构，不用把整个 App 拉进来
   （App 里有浏览器 API，Node 里跑冒烟会炸）。

   4 组的来历（2026-10-02 合并）：原来是「运行 / 能力 / 系统」三组、九个子项平铺；
   现在子项收进组里、点组标题才展开：
     机器人：首页、接入（QQ/微信/飞书/纸飞机 都在接入页里）、消息中心、日志、依赖
     工坊  ：插件工坊、插件管理
     人格  ：AI 大脑
     设置  ：全局管理、设置
   页面一个都没删，只是换了分组位置。 */

export const NAV_GROUPS = [
  {
    id: 'bot',
    title: '机器人',
    items: [
      ['home', '首页', 'home'],
      ['access', '接入', 'plug'],
    ],
  },
  {
    id: 'workshop',
    title: '工坊',
    items: [
      ['plugins', '插件', 'puzzle'],
      ['workshop', '插件工坊', 'sparkle'],
    ],
  },
  {
    id: 'persona',
    title: '人格',
    items: [
      ['brain', 'AI 大脑', 'brain'],
    ],
  },
  {
    id: 'settings',
    title: '设置',
    items: [
      ['services', '全局管理', 'server'],
      ['messages', '消息中心', 'chat'],
      ['logs', '日志', 'file-text'],
      ['deps', '依赖', 'package'],
      ['settings', '设置', 'gear'],
    ],
  },
];

/* 小白模式（默认开启）下隐藏这些入口，可在「设置」里关掉。
   与桌面端 main_window.ADVANCED_PAGES 同一份名单：
   桌面端是 ("微信", "QQ", "飞书", "纸飞机", "消息中心", "插件", "日志", "依赖")，
   即**不藏「插件工坊」**（老板当场找不到过一次，所以工坊入口永远留着）。 */
export const ADVANCED_PAGES = ['messages', 'plugins', 'logs', 'deps'];
