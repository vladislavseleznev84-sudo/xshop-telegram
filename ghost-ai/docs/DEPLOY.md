# Деплой GHOST AI на сервер (24/7)

Подойдёт любой VPS с Ubuntu 22.04/24.04, 1 vCPU / 1–2 ГБ RAM (Hetzner, DigitalOcean, Timeweb и т. п.), ~5 $/мес.

## 0. Что понадобится

| Что | Где взять |
|---|---|
| Домен или поддомен (`ghost.example.com`) | у любого регистратора; A-запись → IP сервера |
| Токен бота | @BotFather; в **Bot Settings → Business Mode** должно быть включено |
| Ключ Anthropic | console.anthropic.com → API keys (платный аккаунт) |
| Telegram Premium на **вашем** аккаунте | нужен для раздела Business → Чат-боты |
| Ваш числовой Telegram ID | узнаете на шаге 5 |

> ⚠️ Токен, показанный в чате/переписке, считайте скомпрометированным: после настройки выполните в @BotFather `/revoke` → выберите бота → вставьте **новый** токен в `.env` и `docker compose up -d`.

## 1. Сервер

```bash
ssh root@<IP>
apt update && apt install -y docker.io docker-compose-v2 git ufw
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw enable
```

## 2. Код и секреты

```bash
git clone https://github.com/vladislavseleznev84-sudo/xshop-telegram.git
cd xshop-telegram/ghost-ai
git checkout claude/ghost-ai-telegram-9gr84x      # пока ветка не влита в main
cp .env.example .env && chmod 600 .env
docker run --rm python:3.12-slim sh -c "pip -q install cryptography && python - <<'PY'
import secrets; from cryptography.fernet import Fernet
print('WEBHOOK_SECRET='+secrets.token_urlsafe(32)); print('SESSION_SECRET='+secrets.token_urlsafe(48))
print('ENCRYPTION_KEY='+Fernet.generate_key().decode()); print('POSTGRES_PASSWORD='+secrets.token_urlsafe(18))
PY"
nano .env        # вставьте сгенерированное + TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, DOMAIN, PUBLIC_BASE_URL=https://<домен>
```

Заполните `.env`: `TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`, `DOMAIN`, `PUBLIC_BASE_URL`, четыре сгенерированных секрета.
**Сохраните `ENCRYPTION_KEY` в менеджере паролей отдельно:** без него зашифрованная память не расшифруется.
`DATABASE_URL` в `.env` можно не менять — `docker-compose.yml` подставит правильный.

## 3. Запуск

```bash
docker compose up -d --build
docker compose ps                 # db, app, caddy — Up (healthy)
docker compose logs -f app        # «webhook set»
curl https://<домен>/healthz      # {"ok":true}
```

При старте приложение само вызывает `setWebhook` (с `secret_token` и нужными `allowed_updates`) и `setMyCommands`.

## 4. Узнать свой Telegram ID

Напишите боту `/start`. Пока `OWNER_TELEGRAM_ID=0`, он ответит вашим ID. Впишите его в `.env` (`OWNER_TELEGRAM_ID=123456789`) и:

```bash
docker compose up -d
```

Теперь бот отвечает на команды только вам.

## 5. Подключить бота к аккаунту (на iPhone)

Telegram → **Настройки → Telegram Business → Чат-боты** → введите `@имя_бота` →
включите **«Отвечать на сообщения»** → выберите, какие чаты бот может видеть (лучше начать с одного тестового).
Вам придёт сообщение «🔌 Бот подключён…». Проверьте `/status`: «ответы разрешены».

## 6. Безопасный первый запуск

1. Бот стартует **на паузе**, режим **shadow**.
2. `/chats` → «Разрешить» для тестового чата (попросите друга/второй аккаунт написать вам).
3. `/resume`. Собеседник получит запрос согласия (inline-кнопки). До «Согласен» ничего не передаётся в Claude.
4. После согласия вам в личку приходят **черновики** ответов — оцените качество и стиль (`/set style_prompt …` или вкладка «Настройки» в панели).
5. Когда довольны: `/set mode live`. Стоп-кран всегда: `/pause` или большая кнопка в панели.

## 7. Панель на iPhone

Отправьте боту `/panel` → откройте ссылку (одноразовая, 5 минут) в Safari → **Поделиться → На экран «Домой»**.
Сессия живёт 14 дней, cookie `HttpOnly`/`Secure`/`SameSite=Strict`.

## 8. Обслуживание

```bash
docker compose logs -f app                      # технические логи (без текстов переписок)
git pull && docker compose up -d --build        # обновление
docker compose exec db pg_dump -U ghost ghost | gzip > ghost-$(date +%F).sql.gz   # бэкап (шифрованные поля остаются шифрованными)
```

Ежедневный бэкап: `crontab -e` → `0 4 * * * cd /root/xshop-telegram/ghost-ai && docker compose exec -T db pg_dump -U ghost ghost | gzip > /root/backups/ghost-$(date +\%F).sql.gz`.

## Проблемы

| Симптом | Что проверить |
|---|---|
| Бот молчит | `/status`: пауза? ключ Claude? «НЕТ права отвечать»? webhook установлен? чат разрешён в `/chats`? |
| `last_error` у webhook | DNS/HTTPS домена, `docker compose logs caddy` |
| Кнопки согласия не нажимаются | собеседник может ответить «да»/«нет» текстом |
| Ошибка Claude | `ANTHROPIC_API_KEY`, баланс; бот пришлёт предупреждение (не чаще раза в 10 минут) |
