# Otto Master WebUI

这是 Otto Master 的独立 Vite/TypeScript 前端源码，以 Forge 电台的 3D 工作台为主界面。

开发需要Node.js 22.12或更新版本：

```bash
npm ci
npm run dev
```

`npm run build` 会先执行 TypeScript 检查，再把可离线交付的静态快照写入
`../otto-master/src/otto_master/web/`。生产运行只启动 Otto Master Python 进程，
不需要 Node.js，也不会启动参考工程中的假设备后端、Worker 或独立小智服务。
