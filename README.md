# VOLTÉ — Telegram Mini App starter

Mobile-first dark storefront prototype for a Russian iPhone shop. Frontend: React + TypeScript + Vite. Backend scaffold: Express + SQLite, Telegram initData validation and manager notification.

## Run frontend

Requires Node.js 20+.

```bash
npm install
npm run dev
```

## Run API locally

```bash
cd server
npm install
npm run dev
```

Set server environment variables from `../.env.example`.

## Telegram setup

1. Create a bot via `@BotFather`. Never share its token or commit it to git.
2. Deploy frontend to an HTTPS host.
3. Deploy the API and set its environment variables.
4. Configure the Mini App URL/menu button with `@BotFather`.
5. Connect checkout to `POST /api/orders` and send raw `Telegram.WebApp.initData` in `Authorization: tma <initData>`.
6. Test with a private manager chat before taking customer orders.

## Prototype / not production-ready yet

- Frontend still uses local demo products and demo checkout flow; wire it to `GET /api/products` and `POST /api/orders` before real use.
- Seed inventory and prices are examples. Replace them with verified actual inventory. For used devices, verify battery health, repair history, defects, accessories and warranty terms.
- API validates Telegram initData and calculates totals from server-side product records, but does not yet decrement stock or provide an admin inventory interface.
- Orders are stored in SQLite and a manager notification is attempted via Telegram Bot API. Configure persistent storage/backups or migrate to managed PostgreSQL for production.
- No payment integration is included. Add a Russian acquiring provider after confirming business status and eligibility; verify payment server-side through signed webhooks.
- Before public launch add rate limiting, monitoring, privacy notice, terms, delivery/returns/warranty policy, data-retention controls and a security review.

`VOLTÉ` is a working name only. Check Telegram username, domain and trademark availability.
