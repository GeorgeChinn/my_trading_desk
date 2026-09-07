# GeorgeChin Personal Trade

本地个人交易台：只读规则 + 本地筛选 + 手工记账。不做自动成交，不接外部账户。

根目录规则只保留三份，扫描时分开、不要混：
- `RULES.md`：低位金叉波段
- `RULES2.MD`：回调后的重新启动
- `RULES3.MD`：结构二（尚未写成扫描器）

买入只表示路径到达，不是成交指令。

## 本地

需要 Python 3.11+、Node.js 18+。

```powershell
python -m pip install -r backend\requirements.txt
cd frontend ; npm install
cd .. ; python run.py
```

另开窗口：`cd frontend` → `npm run dev` → http://127.0.0.1:5173  
或先 `npm run build` 再只开后端 → http://127.0.0.1:8000  
登录默认口令 `Abcd1234!`（可用 `DESK_PASSWORD` 改）。

## 服务器

数据在 `/home/ubuntu/desk-data`，不进 Git，挂到容器 `/app/data`。

```bash
docker compose up -d --build
```

打开 `http://服务器IP:8000`。改规则只改仓库里上述三份文件。
