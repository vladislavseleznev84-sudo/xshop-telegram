import 'dotenv/config'
import express from 'express'
import cors from 'cors'
import Database from 'better-sqlite3'
import { z } from 'zod'
import { createHmac, timingSafeEqual } from 'node:crypto'

const app = express()
app.use(cors({ origin: process.env.FRONTEND_ORIGIN?.split(',') ?? false }))
app.use(express.json({ limit: '100kb' }))
const db = new Database(process.env.DB_PATH || 'volte.sqlite')
db.pragma('journal_mode = WAL')
db.exec(`
CREATE TABLE IF NOT EXISTS products (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, price INTEGER NOT NULL CHECK(price >= 0),
 condition TEXT NOT NULL CHECK(condition IN ('new','used')), memory TEXT NOT NULL,
 color TEXT NOT NULL, battery_health INTEGER, repair_history TEXT, condition_notes TEXT,
 stock INTEGER NOT NULL DEFAULT 0 CHECK(stock >= 0), active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS orders (
 id TEXT PRIMARY KEY, telegram_user_id TEXT NOT NULL, customer_name TEXT NOT NULL,
 contact TEXT NOT NULL, items_json TEXT NOT NULL, total INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending_manager', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
`)
const count = db.prepare('SELECT COUNT(*) AS n FROM products').get() as {n:number}
if (count.n === 0) {
 const add = db.prepare(`INSERT INTO products (id,name,price,condition,memory,color,battery_health,repair_history,condition_notes,stock)
 VALUES (?,?,?,?,?,?,?,?,?,?)`)
 const seed = db.transaction(() => {
  add.run(1,'iPhone 17 Pro',144990,'new','256 ГБ','Cosmic Orange',null,null,'Новый, запечатанный — подтвердить наличие',3)
  add.run(2,'iPhone 17',94990,'new','256 ГБ','Silver',null,null,'Новый, запечатанный — подтвердить наличие',4)
  add.run(3,'iPhone 16 Pro',89990,'used','256 ГБ','Natural Titanium',88,'Не указано — проверить перед публикацией','Демонстрационная запись; проверить состояние',1)
  add.run(4,'iPhone 15',59990,'used','128 ГБ','Black',85,'Не указано — проверить перед публикацией','Демонстрационная запись; проверить дефекты и комплект',1)
 })
 seed()
}

function verifyTelegramInitData(initData: string, token: string): {id:string; username?:string; first_name?:string} {
 if (!initData || !token) throw new Error('Telegram authentication is not configured')
 const params = new URLSearchParams(initData)
 const hash = params.get('hash')
 const authDate = Number(params.get('auth_date') || 0)
 if (!hash || !authDate || Math.abs(Date.now()/1000-authDate)>86400) throw new Error('Invalid or expired Telegram initData')
 params.delete('hash')
 params.delete('signature')
 const dataCheckString = [...params.entries()].sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>`${k}=${v}`).join('\n')
 const secret = createHmac('sha256','WebAppData').update(token).digest()
 const expected = createHmac('sha256',secret).update(dataCheckString).digest('hex')
 const actualBuf = Buffer.from(hash,'hex'), expectedBuf = Buffer.from(expected,'hex')
 if (actualBuf.length !== expectedBuf.length || !timingSafeEqual(actualBuf,expectedBuf)) throw new Error('Telegram signature check failed')
 const rawUser = params.get('user')
 if (!rawUser) throw new Error('Telegram user data missing')
 const user = JSON.parse(rawUser)
 if (!user?.id) throw new Error('Telegram user ID missing')
 return {id:String(user.id), username:user.username, first_name:user.first_name}
}
function auth(req: express.Request, res: express.Response, next: express.NextFunction) {
 try {
  const header = req.header('authorization') || ''
  const raw = header.startsWith('tma ') ? header.slice(4) : ''
  const user = verifyTelegramInitData(raw, process.env.TELEGRAM_BOT_TOKEN || '')
  ;(req as any).tgUser = user
  next()
 } catch (e) { res.status(401).json({error: e instanceof Error ? e.message : 'Unauthorized'}) }
}
app.get('/api/health', (_req,res)=>res.json({ok:true}))
app.get('/api/products', (_req,res)=>{
 const rows = db.prepare(`SELECT id,name,price,condition,memory,color,battery_health AS batteryHealth,repair_history AS repairHistory,condition_notes AS conditionNotes,stock FROM products WHERE active=1 ORDER BY id`).all()
 res.json(rows)
})
const orderSchema = z.object({
 customerName:z.string().trim().min(1).max(100),
 contact:z.string().trim().min(3).max(200),
 items:z.array(z.object({productId:z.number().int().positive(),quantity:z.number().int().min(1).max(5)})).min(1).max(20)
})
app.post('/api/orders', auth, async (req,res)=>{
 const parsed = orderSchema.safeParse(req.body)
 if (!parsed.success) return res.status(400).json({error:'Проверь имя, контакт и товары'})
 const user = (req as any).tgUser as {id:string;username?:string;first_name?:string}
 try {
  const orderId = `VLT-${Date.now().toString(36).toUpperCase()}`
  const createOrder = db.transaction(() => {
   let total = 0
   const lines: {name:string;condition:string;memory:string;quantity:number;price:number}[] = []
   for (const item of parsed.data.items) {
    const product = db.prepare('SELECT id,name,price,condition,memory,stock FROM products WHERE id=? AND active=1').get(item.productId) as any
    if (!product) throw new Error(`Товар ${item.productId} больше недоступен`)
    if (product.stock < item.quantity) throw new Error(`${product.name}: недостаточно товара в наличии`)
    total += product.price * item.quantity
    lines.push({name:product.name,condition:product.condition,memory:product.memory,quantity:item.quantity,price:product.price})
   }
   // No stock reservation yet: manager must confirm availability before payment.
   db.prepare(`INSERT INTO orders (id,telegram_user_id,customer_name,contact,items_json,total,status) VALUES (?,?,?,?,?,?,?)`)
    .run(orderId,user.id,parsed.data.customerName,parsed.data.contact,JSON.stringify(lines),total,'pending_manager')
   return {total,lines}
  })
  const order = createOrder()
  const conditionName = (c:string) => c==='new'?'новый':'б/у'
  const message = [
   `🛍 Новый заказ ${orderId}`,
   `Имя: ${parsed.data.customerName}`,
   `Контакт: ${parsed.data.contact}`,
   `Telegram ID: ${user.id}${user.username?` (@${user.username})`:''}`,
   '',
   ...order.lines.map(i=>`• ${i.name} (${conditionName(i.condition)}, ${i.memory}) × ${i.quantity} — ${new Intl.NumberFormat('ru-RU').format(i.price*i.quantity)} ₽`),
   '',
   `Итого: ${new Intl.NumberFormat('ru-RU').format(order.total)} ₽`,
   'Статус: ожидает подтверждения менеджером'
  ].join('\n')
  const token = process.env.TELEGRAM_BOT_TOKEN
  const chatId = process.env.TELEGRAM_MANAGER_CHAT_ID
  if (!token || !chatId) {
   return res.status(202).json({orderId,status:'pending_manager',total:order.total,warning:'Заказ сохранён, но уведомление менеджеру не настроено'})
  }
  const response = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
   method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({chat_id:chatId,text:message})
  })
  if (!response.ok) return res.status(202).json({orderId,status:'pending_manager',total:order.total,warning:'Заказ сохранён; отправка уведомления менеджеру не удалась'})
  res.status(201).json({orderId,status:'pending_manager',total:order.total})
 } catch (e) {
  res.status(409).json({error:e instanceof Error?e.message:'Не удалось создать заказ'})
 }
})
const port = Number(process.env.PORT || 3001)
app.listen(port,()=>console.log(`VOLTÉ API listening on :${port}`))
