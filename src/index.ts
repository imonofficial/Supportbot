import { Bot, Context, webhookCallback } from 'grammy';
import { Env, initDb, deleteOldClosedTickets } from './db';
import { setupBot } from './handlers';

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    try {
      const url = new URL(request.url);

      // Only allow POST requests for webhook
      if (request.method !== 'POST') {
        return new Response('Not Found', { status: 404 });
      }

      // Auto Webhook Registration check
      try {
        const isSet = await env.KV.get('webhook_set');
        if (!isSet) {
          const bot = new Bot(env.BOT_TOKEN);
          const webhookUrl = `${url.origin}/${env.WEBHOOK_SECRET}`;
          await bot.api.setWebhook(webhookUrl);
          await env.KV.put('webhook_set', 'true');
          console.log(`Webhook set to ${webhookUrl}`);
        }
      } catch (e) {
        console.error('Failed to auto-register webhook', e);
      }

      // Verify webhook path
      if (url.pathname !== `/${env.WEBHOOK_SECRET}`) {
        return new Response('Not Found', { status: 404 });
      }

      const bot = new Bot(env.BOT_TOKEN);

      // Auto-initialize DB
      const ownerId = parseInt(env.OWNER_ID, 10);
      if (!isNaN(ownerId)) {
        await initDb(env.DB, ownerId);
      }

      setupBot(bot, env);

      // Return a proper handler that does not throw
      const handler = webhookCallback(bot, 'cloudflare-mod');
      const response = await handler(request);
      return response;
    } catch (err) {
      console.error('Global Error in fetch:', err);
      return new Response('Error', { status: 200 }); 
    }
  },

  async scheduled(event: ScheduledEvent, env: Env, ctx: ExecutionContext): Promise<void> {
    try {
      await deleteOldClosedTickets(env.DB);
      console.log('Old closed tickets deleted.');
    } catch (err) {
      console.error('Scheduled task error:', err);
    }
  }
};
