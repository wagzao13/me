# 送 WAGZAO 一部 iPhone Duo

一个带共享账本和人工核验台的送礼众筹页面。前端是纯 HTML/CSS/JS，后端只依赖 Python 标准库 + SQLite。

## 怎么启动

1. 双击 `start.command`。
2. 终端会显示本机地址，并自动打开浏览器。
3. 关闭服务：回到终端窗口按 `Control-C`。

如果 macOS 第一次拦截了 `.command` 文件：
- 右键 `start.command` → 打开；或
- 在终端里执行：`chmod +x start.command && ./start.command`

第一次启动时，如果 macOS 弹出“是否允许 Python 接受传入网络连接”，请点允许；否则朋友无法通过同一 Wi-Fi 打开页面，只能本机访问。

## 三个地址

启动后终端会打印：

- 本机页面：`http://127.0.0.1:8787`
- 朋友访问（同一 Wi-Fi）：`http://你的局域网IP:8787`
- 管理员核验台：`http://127.0.0.1:8787/admin`

**核验密钥在 `ADMIN_KEY.txt` 里。** 打开核验台后把密钥粘贴进去。不要把 `ADMIN_KEY.txt` 发给朋友，也不要把它上传到公开仓库。

## 一笔心意怎么走

1. 朋友打开页面，选择金额，点“去付款”。
2. 页面显示微信收款码；朋友在微信里扫码并转账。
3. 朋友回到页面，填写称呼、微信昵称、留言，点“我已完成支付，提交登记”。
4. 页面生成一个登记编号，状态是 **待核验**，这时**不会**计入公开进度。
5. 你在核验台核对微信账单里的金额、时间和昵称，点“核对到账，通过”。
6. 通过后，这笔记录才会进入共享账本；主页面每 15 秒自动刷新，也可以手动刷新。
7. 如果核对不上，点“驳回”。

> 重要：微信个人收款码本身没有提供“支付成功回调”。所以这里做的是**共享账本 + 人工核验**，不是自动对账。要做到自动核验，需要接入微信支付商户 API / 官方回调；那需要商户号、API 密钥和服务端备案/域名等条件。

## 数据在哪

- 数据库：`data/fund.db`（SQLite）
- 核验密钥：`ADMIN_KEY.txt`
- 收款码：`assets/wechat-qr.png`
- 商品演示图：`assets/iphone-duo-lineup.webp`

**备份**：停止服务后，复制 `data/fund.db` 即可。

**清空所有登记（谨慎）**：
1. 先按 `Control-C` 停止服务；
2. 删除 `data/fund.db`（以及 `data/fund.db-wal`、`data/fund.db-shm`，如果存在）；
3. 重新启动。会得到一个空账本。

**换核验密钥**：
1. 停止服务；
2. 删除 `ADMIN_KEY.txt`；
3. 重新启动，服务器会生成新的密钥。

## 修改内容

- 收款码：替换 `assets/wechat-qr.png`，或在 `index.html` 的 `FUND_CONFIG.paymentMethods` 里改 `qr` 路径。
- 机型、颜色、容量、价格：改 `index.html` 里的 `window.IPHONE_CATALOG`。
- 快捷金额：改 `FUND_CONFIG.quickAmounts`。
- 管理员密钥也可以不用文件，启动时设置环境变量：`WAGZAO_ADMIN_KEY=你的密钥 python3 server.py`。

## 安全说明

- 这是给朋友之间用的小项目，默认监听 `0.0.0.0`，方便同一 Wi-Fi 下访问。
- 不要直接把 8787 端口暴露到公网。要公网运行，请使用 HTTPS 反向代理，并设置强密钥，最好加访问控制。
- 管理员通过 `X-Admin-Key` 请求头鉴权；“通过”只代表人工确认，不会触发微信退款或转账。
- 页面里的公开账本只展示**已通过**的记录，且匿名记录会隐藏姓名，只保留“一位不愿署名的朋友”。

## 目录

```
.
├── index.html              # 主页面
├── admin.html              # 核验台
├── server.py               # 共享账本 + API
├── start.command           # 双击启动
├── README.md
├── preview.jpg             # 页面预览图
├── ADMIN_KEY.txt           # 核验密钥（不要外发）
├── assets/
│   ├── iphone-duo-lineup.webp
│   └── wechat-qr.png
└── data/
    └── fund.db             # 启动后生成
```
