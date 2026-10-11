import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'

const tg = (window as any).Telegram?.WebApp
if (tg) {
  tg.ready()
  tg.expand()
  tg.setHeaderColor?.('#090a0d')
  tg.setBackgroundColor?.('#090a0d')
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)