# Telegram Support Ticket Bot (Cloudflare Workers)

Complete support ticket system for Telegram, rewritten for Cloudflare Workers.

## Features
- Inline keyboard navigation
- One active ticket per user (TCK-000001 format)
- Media forwarding (photos, videos, documents, voice, stickers, GIFs, contacts, locations)
- Admin panel with reply, assign, close, ban
- Admin management (owner can add/remove admins)
- FAQ section
- Statistics
- Automatic cleanup of closed tickets older than 7 days (cron)
- Temporary states stored in Cloudflare KV
- Permanent data in Cloudflare D1

## Prerequisites
- Node.js 18+
- Wrangler CLI (`npm install -g wrangler`)
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Your Telegram user ID (owner)

## Setup

1. Clone this repository.
2. Install dependencies: `npm install`
3. Create D1 database:
   ```bash
   wrangler d1 create support-db
