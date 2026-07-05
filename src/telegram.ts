/**
 * Bot setup and KV state management for temporary workflows.
 */
import { Bot, Context } from 'grammy';
import type { Env } from './index';

// Type for KV stored admin states
interface AdminState {
	action: 'reply' | 'search' | 'awaiting_admin_forward';
	ticketId?: number;       // used for reply action
}

export function setupBot(env: Env): Bot<Context> {
	const bot = new Bot<Context>(env.BOT_TOKEN);

	// Attach env to context so handlers can access it
	bot.use(async (ctx, next) => {
		(ctx as any).env = env;
		await next();
	});

	// Register all handlers (imported from handlers.ts)
	require('./handlers').registerHandlers(bot);

	return bot;
}

// ----------------- KV helpers for admin states -----------------
const STATE_PREFIX = 'admin_state_';

export async function getAdminState(env: Env, adminId: number): Promise<AdminState | null> {
	const raw = await env.KV.get(`${STATE_PREFIX}${adminId}`);
	return raw ? JSON.parse(raw) : null;
}

export async function setAdminState(env: Env, adminId: number, state: AdminState): Promise<void> {
	await env.KV.put(`${STATE_PREFIX}${adminId}`, JSON.stringify(state), { expirationTtl: 300 }); // 5 minutes
}

export async function clearAdminState(env: Env, adminId: number): Promise<void> {
	await env.KV.delete(`${STATE_PREFIX}${adminId}`);
}
