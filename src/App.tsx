import { useMemo, useState } from 'react'
import { ArrowDownRight, ArrowLeft, ArrowRight, Check, ChevronRight, Heart, Home, Minus, Plus, Search, ShoppingBag, ShoppingCart, Smartphone, UserRound, X } from 'lucide-react'

type Product = { id: number; name: string; tag: string; price: number; memory: string; color: string; image: string; description: string; category: 'pro' | 'standard' }
const products: Product[] = [
  { id: 1, name: 'iPhone 17 Pro', tag: 'НОВИНКА', price: 144990, memory: '256 ГБ', color: 'Cosmic Orange', image: 'https://images.unsplash.com/photo-1695048133142-1a20484d2569?auto=format&fit=crop&w=900&q=85', description: 'Мощный чип, профессиональная камера и премиальный корпус.', category: 'pro' },
  { id: 2, name: 'iPhone 17', tag: 'ХИТ', price: 94990, memory: '256 ГБ', color: 'Silver', image: 'https://images.unsplash.com/photo-1695048133142-1a20484d2569?auto=format&fit=crop&w=900&q=85', description: 'Баланс мощности, камеры и ежедневного удобства.', category: 'standard' },
  { id: 3, name: 'iPhone 16 Pro', tag: 'ВЫБОР', price: 119990, memory: '256 ГБ', color: 'Natural Titanium', image: 'https://images.unsplash.com/photo-1695048133142-1a20484d2569?auto=format&fit=crop&w=900&q=85', description: 'Профессиональные возможности и титановый дизайн.', category: 'pro' },
  { id: 4, name: 'iPhone 16', tag: '', price: 79990, memory: '128 ГБ', color: 'Black', image: 'https://images.unsplash.com/photo-1695048133142-1a20484d2569?auto=format&fit=crop&w=900&q=85', description: 'Надёжный повседневный iPhone с отличной камерой.', category: 'standard' },
]
type Cart = Record<number, number>
const money = (n: number) => new Intl.NumberFormat('ru-RU').format(n) + ' ₽'

export default function App() {
  const [tab, setTab] = useState<'home'|'catalog'|'cart'|'profile'>('home')
  const [filter, setFilter] = useState<'all'|'pro'|'standard'>('all')
  const [search, setSearch] = useState('')
  const [cart, setCart] = useState<Cart>({})
  const [saved, setSaved] = useState<number[]>([])
  const [selected, setSelected] = useState<Product | null>(null)
  const [checkout, setCheckout] = useState(false)
  const [name, setName] = useState('')
  const [contact, setContact] = useState('')
  const [sent, setSent] = useState(false)
  const [notice, setNotice] = useState('')
  const cartCount = Object.values(cart).reduce((a,b)=>a+b,0)
  const total = products.reduce((sum,p)=>sum+p.price*(cart[p.id]||0),0)
  const filtered = useMemo(() => products.filter(p => (filter==='all'||p.category===filter) && p.name.toLowerCase().includes(search.toLowerCase())), [filter,search])
  const updateCart = (id:number, delta:number) => setCart(old => ({...old, [id]:Math.max(0,(old[id]||0)+delta)}))
  const add = (p:Product) => { updateCart(p.id,1); setNotice(`${p.name} добавлен в корзину`); window.setTimeout(()=>setNotice(''),2200) }
  const sendOrder = () => {
    if (!name.trim() || !contact.trim()) { setNotice('Заполни имя и контакт для связи'); window.setTimeout(()=>setNotice(''),2500); return }
    const lines = products.filter(p=>cart[p.id]).map(p=>`${p.name} ${p.memory} × ${cart[p.id]} — ${money(p.price*cart[p.id])}`).join('\n')
    const text = `Новый заказ VOLTÉ\nИмя: ${name}\nКонтакт: ${contact}\n\n${lines}\n\nИтого: ${money(total)}`
    const tg = (window as any).Telegram?.WebApp
    if (tg?.openTelegramLink) {
      // Replace @YOUR_STORE with the username of your store's Telegram account.
      tg.openTelegramLink(`https://t.me/share/url?url=&text=${encodeURIComponent(text)}`)
    } else {
      navigator.clipboard?.writeText(text).catch(()=>{})
      setSent(true)
    }
  }
  return <div className="app-shell">
    <header className="topbar">
      <button className="brand" onClick={()=>{setTab('home');setSelected(null)}} aria-label="На главную">
        <span className="brand-mark"><Smartphone size={19}/></span><span><b>VOLTÉ</b><small>APPLE STORE</small></span>
      </button>
      <button className="icon-btn bag-top" onClick={()=>setTab('cart')} aria-label="Корзина"><ShoppingBag size={19}/>{cartCount>0&&<i>{cartCount}</i>}</button>
    </header>
    {selected ? <main className="page product-page">
      <button className="back-btn" onClick={()=>setSelected(null)}><ArrowLeft size={16}/> Назад в каталог</button>
      <div className="product-hero"><span className="tag">{selected.tag||'APPLE'}</span><img src={selected.image} alt={selected.name}/><div className="image-glow"/></div>
      <div className="product-info">
        <div className="eyebrow">APPLE / IPHONE</div><h1>{selected.name}</h1><p className="muted">{selected.description}</p>
        <div className="spec-row"><span>Память</span><b>{selected.memory}</b></div><div className="spec-row"><span>Цвет</span><b>{selected.color}</b></div>
        <div className="buy-row"><div><small>Стоимость</small><strong>{money(selected.price)}</strong></div><button className="primary-btn" onClick={()=>add(selected)}><Plus size={17}/> В корзину</button></div>
      </div>
    </main> : tab==='home' ? <main className="page">
      <div className="hero">
        <div className="hero-copy"><span className="eyebrow"><span className="pulse"/> НОВАЯ КОЛЛЕКЦИЯ</span><h1>Технологии.<br/><em>Без лишнего.</em></h1><p>Твой следующий iPhone уже здесь.</p><button className="hero-cta" onClick={()=>setTab('catalog')}>Смотреть каталог <ArrowDownRight size={17}/></button></div>
        <div className="hero-device"><div className="hero-orb"/><img src={products[0].image} alt="iPhone"/></div><div className="hero-index">01 <span>/ 04</span></div>
      </div>
      <div className="section-head"><div><div className="eyebrow">CURATED FOR YOU</div><h2>Выбор недели</h2></div><button className="text-btn" onClick={()=>setTab('catalog')}>Все модели <ArrowRight size={15}/></button></div>
      <div className="product-grid">{products.slice(0,2).map(p=><ProductCard key={p.id} p={p} saved={saved.includes(p.id)} onSave={()=>setSaved(s=>s.includes(p.id)?s.filter(i=>i!==p.id):[...s,p.id])} onOpen={()=>setSelected(p)} onAdd={()=>add(p)}/>)}</div>
      <div className="perks"><div><span className="perk-icon"><Check size={16}/></span><div><b>Проверенная техника</b><small>Перед продажей</small></div></div><div><span className="perk-icon"><ShoppingBag size={16}/></span><div><b>Поддержка</b><small>На каждом этапе</small></div></div></div>
    </main> : tab==='catalog' ? <main className="page">
      <div className="page-title"><div className="eyebrow">THE COLLECTION</div><h1>Каталог<span>.</span></h1><p className="muted">Выбери устройство под свой ритм.</p></div>
      <label className="search-box"><Search size={17}/><input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Найти модель iPhone"/>{search&&<button onClick={()=>setSearch('')}><X size={15}/></button>}</label>
      <div className="filters">{([['all','Все модели'],['pro','Pro'],['standard','iPhone']] as const).map(([v,l])=><button key={v} className={filter===v?'active':''} onClick={()=>setFilter(v)}>{l}</button>)}</div>
      <div className="product-grid">{filtered.map(p=><ProductCard key={p.id} p={p} saved={saved.includes(p.id)} onSave={()=>setSaved(s=>s.includes(p.id)?s.filter(i=>i!==p.id):[...s,p.id])} onOpen={()=>setSelected(p)} onAdd={()=>add(p)}/>)}</div>
      {filtered.length===0&&<div className="empty"><Smartphone size={30}/><b>Ничего не найдено</b><span>Попробуй изменить запрос.</span></div>}
    </main> : tab==='cart' ? <main className="page">
      <div className="page-title"><div className="eyebrow">YOUR SELECTION</div><h1>Корзина<span>.</span></h1><p className="muted">{cartCount?`${cartCount} товара в заказе`:'Здесь будут выбранные устройства.'}</p></div>
      {cartCount===0 ? <div className="empty"><ShoppingCart size={32}/><b>Пока пусто</b><span>Добавь iPhone, который тебе подходит.</span><button className="primary-btn" onClick={()=>setTab('catalog')}>Перейти в каталог <ArrowRight size={16}/></button></div> :
      <><div className="cart-list">{products.filter(p=>cart[p.id]>0).map(p=><div className="cart-item" key={p.id}><img src={p.image} alt={p.name}/><div className="cart-item-info"><b>{p.name}</b><small>{p.memory} · {p.color}</small><strong>{money(p.price)}</strong><div className="quantity"><button onClick={()=>updateCart(p.id,-1)}><Minus size={13}/></button><span>{cart[p.id]}</span><button onClick={()=>updateCart(p.id,1)}><Plus size={13}/></button></div></div><button className="remove" onClick={()=>setCart(c=>({...c,[p.id]:0}))}><X size={16}/></button></div>)}</div>
      <div className="total-card"><div><span>Итого</span><strong>{money(total)}</strong></div><small>Доставка и наличие уточняются при подтверждении заказа.</small><button className="primary-btn wide" onClick={()=>setCheckout(true)}>Оформить заказ <ArrowRight size={17}/></button></div></>}
    </main> : <main className="page">
      <div className="page-title"><div className="eyebrow">YOUR SPACE</div><h1>Профиль<span>.</span></h1><p className="muted">Всё важное — в одном месте.</p></div>
      <div className="profile-card"><div className="profile-avatar"><UserRound size={25}/></div><div><b>Гость VOLTÉ</b><small>Открой магазин через Telegram</small></div><ChevronRight size={18}/></div>
      <div className="menu-list"><button onClick={()=>setTab('catalog')}><Smartphone size={18}/> Каталог устройств <ChevronRight size={17}/></button><button onClick={()=>setTab('cart')}><ShoppingBag size={18}/> Моя корзина <ChevronRight size={17}/></button><button onClick={()=>setNotice('Напиши менеджеру после подключения Telegram-бота.')}><UserRound size={18}/> Связаться с менеджером <ChevronRight size={17}/></button></div>
      <p className="footnote">VOLTÉ · APPLE STORE<br/>Интерфейс-прототип. Цены и наличие нужно подтвердить перед запуском.</p>
    </main>}
    <nav className="bottom-nav">
      {([{id:'home',label:'Главная',icon:Home},{id:'catalog',label:'Каталог',icon:Smartphone},{id:'cart',label:'Корзина',icon:ShoppingCart},{id:'profile',label:'Профиль',icon:UserRound}] as const).map(item=><button key={item.id} className={tab===item.id&&!selected?'selected':''} onClick={()=>{setTab(item.id);setSelected(null)}}><span className="nav-icon"><item.icon size={19}/>{item.id==='cart'&&cartCount>0&&<i>{cartCount}</i>}</span><small>{item.label}</small></button>)}
    </nav>
    {notice&&<div className="toast">{notice}</div>}
    {checkout&&<div className="modal-backdrop" onClick={()=>setCheckout(false)}><section className="checkout-modal" onClick={e=>e.stopPropagation()}><div className="modal-head"><div><div className="eyebrow">ALMOST YOURS</div><h2>Оформление</h2></div><button className="icon-btn" onClick={()=>setCheckout(false)}><X size={18}/></button></div>
      {sent?<div className="success"><span><Check size={25}/></span><h3>Заказ подготовлен</h3><p>Текст заказа скопирован. Отправь его менеджеру магазина в Telegram.</p><button className="primary-btn wide" onClick={()=>{setSent(false);setCheckout(false)}}>Готово</button></div>:<><p className="muted">Оставь контакт — менеджер подтвердит наличие и доставку.</p><label className="field-label">Как к тебе обращаться<input value={name} onChange={e=>setName(e.target.value)} placeholder="Имя"/></label><label className="field-label">Telegram или телефон<input value={contact} onChange={e=>setContact(e.target.value)} placeholder="@username или +7..."/></label><div className="modal-total"><span>К оплате после подтверждения</span><b>{money(total)}</b></div><button className="primary-btn wide" onClick={sendOrder}>Подготовить заказ <ArrowRight size={17}/></button><small className="fineprint">Оплата не списывается. Заказ считается подтверждённым только после связи с продавцом.</small></>}</section></div>}
  </div>
}

function ProductCard({p,saved,onSave,onOpen,onAdd}:{p:Product;saved:boolean;onSave:()=>void;onOpen:()=>void;onAdd:()=>void}) {
  return <article className="product-card"><div className="card-image" onClick={onOpen}><span className="tag">{p.tag||'APPLE'}</span><button className={`heart ${saved?'saved':''}`} onClick={e=>{e.stopPropagation();onSave()}} aria-label="В избранное"><Heart size={17} fill={saved?'currentColor':'none'}/></button><img src={p.image} alt={p.name}/><span className="view-hint">Подробнее <ArrowRight size={13}/></span></div><div className="card-info"><button className="product-name" onClick={onOpen}>{p.name}</button><span className="product-meta">{p.memory} · {p.color}</span><div className="card-bottom"><strong>{money(p.price)}</strong><button className="add-btn" onClick={onAdd}><Plus size={17}/></button></div></div></article>
}