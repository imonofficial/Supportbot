/**
 * Cloudflare Worker entry point.
 * Handles Telegram webhook calls and scheduled cleanup jobs.
 */
import { Bot, webhookCallback } from 'grammy';
import { handleScheduled } from './handlers';
import { setupBot } from './telegram';

export interface Env {
	BOT_TOKEN: string;
	OWNER_ID: string;
	DB: D1Database;
	KV: KVNamespace;
	WEBHOOK_PATH: string;
}

export default {
	async fetch(request: Request, env: Env): Promise<Response> {
		const bot = setupBot(env);
		const url = new URL(request.url);
		if (url.pathname === env.WEBHOOK_PATH) {
			// Use grammy's webhook callback, which returns a Response
			return webhookCallback(bot, 'cloudflare-mod', {
				secretToken: env.WEBHOOK_PATH, // not strictly necessary, but adds a bit of security
			})(request);
		}
		return new Response('Not Found', { status: 404 });
	},

	async scheduled(event: ScheduledEvent, env: Env, ctx: ExecutionContext): Promise<void> {
		ctx.waitUntil(handleScheduled(env));
	},
};
