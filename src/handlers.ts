/**
 * Telegram update handlers.
 * Imported and registered in telegram.ts via registerHandlers.
 */
import { Bot, Context, InlineKeyboard } from 'grammy';
import type { Env } from './index';
import * as db from './database';
import * as kb from './keyboards';
import { getAdminState, setAdminState, clearAdminState } from './telegram';
import { extractMessageContent } from './utils';

export function registerHandlers(bot: Bot<Context>): void {
	// Command handlers
	bot.command('start', handleStart);
	bot.command('help', handleStart);

	// Callback query handler
	bot.on('callback_query:data', handleCallbackQuery);

	// User messages (including media) – forward to admins
	bot.on('message', handleUserMessage);

	// Admin stateful messages (reply, search, add admin)
	bot.on('message', handleAdminStateMessage);
}

async function handleStart(ctx: Context) {
	const env = (ctx as any).env as Env;
	await ctx.reply('👋 Welcome! Choose an option:', {
		reply_markup: kb.mainMenu(ctx.from!.id, env),
	});
}

async function handleCallbackQuery(ctx: Context) {
	const env = (ctx as any).env as Env;
	const data = ctx.callbackQuery!.data!;
	const adminId = ctx.from!.id;

	// --- Main menu actions ---
	if (data === 'create_ticket') return createTicketHandler(ctx, env);
	if (data === 'my_ticket') return myTicketHandler(ctx, env);
	if (data === 'faq') return faqHandler(ctx, env);
	if (data === 'admin_panel') return adminPanelHandler(ctx, env);

	// --- Admin panel sub-actions ---
	if (data === 'open_tickets') return openTicketsHandler(ctx, env, 1);
	if (data.startsWith('open_tickets_page_')) return openTicketsHandler(ctx, env, parseInt(data.split('_')[3]));
	if (data === 'search_ticket') return searchTicketStart(ctx, env);
	if (data === 'statistics') return statisticsHandler(ctx, env);
	if (data === 'admin_management') return adminManagementHandler(ctx, env);
	if (data === 'add_admin') return addAdminStart(ctx, env);
	if (data === 'remove_admin') return removeAdminStart(ctx, env);
	if (data.startsWith('remove_admin_confirm_')) return removeAdminConfirm(ctx, env);

	// --- Ticket view actions ---
	if (data.startsWith('view_ticket_')) return viewTicketHandler(ctx, env, parseInt(data.split('_')[2]));
	if (data.startsWith('reply_ticket_')) return replyTicketStart(ctx, env, parseInt(data.split('_')[2]));
	if (data.startsWith('assign_ticket_')) return assignTicketHandler(ctx, env, parseInt(data.split('_')[2]));
	if (data.startsWith('admin_close_ticket_')) return adminCloseTicketHandler(ctx, env, parseInt(data.split('_')[3]));
	if (data.startsWith('ban_user_ticket_')) return banUserHandler(ctx, env, parseInt(data.split('_')[3]));

	// --- User close ticket ---
	if (data.startsWith('user_close_ticket_')) return userCloseTicketHandler(ctx, env, parseInt(data.split('_')[3]));

	// --- Cancel reply ---
	if (data.startsWith('cancel_reply_')) return cancelReplyHandler(ctx, env, parseInt(data.split('_')[2]));

	// --- FAQ answers ---
	if (data.startsWith('faq_')) return faqAnswerHandler(ctx, env, data);

	// Fallback
	await ctx.answerCallbackQuery({ text: 'Unknown action' });
}

// ---------- User handlers ----------
async function createTicketHandler(ctx: Context, env: Env) {
	const userId = ctx.from!.id;
	await ctx.answerCallbackQuery();
	if (await db.isUserBanned(env, userId)) {
		await ctx.editMessageText('⛔ You are banned from creating tickets.');
		return;
	}
	const existing = await db.getOpenTicketByUser(env, userId);
	if (existing) {
		await ctx.editMessageText(`ℹ️ You already have an open ticket: ${existing.ticket_id}`);
		return;
	}
	const ticket = await db.createTicket(env, ctx.from!);
	await ctx.editMessageText(
		`✅ Ticket ${ticket.ticket_id} created!\nSend your message, photo, video, etc. We will reply as soon as possible.`,
		{ reply_markup: kb.userTicketMenu(ticket.id) }
	);
}

async function myTicketHandler(ctx: Context, env: Env) {
	const userId = ctx.from!.id;
	await ctx.answerCallbackQuery();
	const ticket = await db.getOpenTicketByUser(env, userId);
	if (!ticket) {
		await ctx.editMessageText('📭 You have no open tickets.');
		return;
	}
	const assigned = ticket.assigned_admin_id ? `Admin ${ticket.assigned_admin_id}` : 'Unassigned';
	const text = `🎫 Ticket ${ticket.ticket_id}\nStatus: ${ticket.status}\nAssigned: ${assigned}\nCreated: ${ticket.created_at}`;
	await ctx.editMessageText(text, { reply_markup: kb.userTicketMenu(ticket.id) });
}

async function userCloseTicketHandler(ctx: Context, env: Env, ticketDbId: number) {
	const userId = ctx.from!.id;
	await ctx.answerCallbackQuery();
	const ticket = await db.getTicketById(env, ticketDbId);
	if (!ticket || ticket.user_id !== userId) {
		await ctx.editMessageText('❌ Ticket not found or access denied.');
		return;
	}
	if (ticket.status !== 'open') {
		await ctx.editMessageText('This ticket is already closed.');
		return;
	}
	await db.closeTicket(env, ticketDbId);
	// Notify admins? We'll skip for simplicity.
	await ctx.editMessageText(`🔒 Ticket ${ticket.ticket_id} closed.`);
}

// ---------- FAQ ----------
async function faqHandler(ctx: Context, _env: Env) {
	await ctx.answerCallbackQuery();
	const faqKeyboard = new InlineKeyboard()
		.text('How do I create a ticket?', 'faq_create')
		.row()
		.text('How do I close a ticket?', 'faq_close')
		.row()
		.text('How do I contact support?', 'faq_contact')
		.row()
		.text('🔙 Back', 'main_menu');
	await ctx.editMessageText('❓ Frequently Asked Questions:', { reply_markup: faqKeyboard });
}

async function faqAnswerHandler(ctx: Context, _env: Env, faqKey: string) {
	await ctx.answerCallbackQuery();
	let answer = '';
	switch (faqKey) {
		case 'faq_create': answer = 'Tap "Create Ticket" in the main menu.'; break;
		case 'faq_close': answer = 'Open "My Ticket" and tap "Close Ticket".'; break;
		case 'faq_contact': answer = 'Create a ticket and describe your problem. Our admins will respond.'; break;
		default: answer = 'Unknown FAQ.';
	}
	await ctx.editMessageText(answer, {
		reply_markup: new InlineKeyboard().text('🔙 Back', 'faq'),
	});
}

// ---------- Admin panel ----------
async function adminPanelHandler(ctx: Context, env: Env) {
	const userId = ctx.from!.id;
	await ctx.answerCallbackQuery();
	if (!(await db.isAdmin(env, userId))) {
		await ctx.editMessageText('⛔ Access denied.');
		return;
	}
	const isOwner = userId === parseInt(env.OWNER_ID);
	await ctx.editMessageText('🔧 Admin Panel', { reply_markup: kb.adminPanel(isOwner) });
}

async function openTicketsHandler(ctx: Context, env: Env, page: number) {
	await ctx.answerCallbackQuery();
	const userId = ctx.from!.id;
	if (!(await db.isAdmin(env, userId))) return ctx.editMessageText('⛔ Access denied.');
	const perPage = 5;
	const offset = (page - 1) * perPage;
	const tickets = await db.getOpenTickets(env, perPage, offset);
	if (tickets.length === 0 && page === 1) {
		await ctx.editMessageText('✅ No open tickets.');
		return;
	}
	const totalOpen = (await env.DB.prepare("SELECT COUNT(*) as cnt FROM tickets WHERE status='open'").run()).results[0] as any;
	const totalPages = Math.ceil(totalOpen.cnt / perPage);
	const text = `📂 Open tickets (page ${page}/${totalPages}):`;
	const keyboard = kb.openTicketsList(tickets, page, totalPages);
	await ctx.editMessageText(text, { reply_markup: keyboard });
}

async function viewTicketHandler(ctx: Context, env: Env, ticketDbId: number) {
	await ctx.answerCallbackQuery();
	const userId = ctx.from!.id;
	if (!(await db.isAdmin(env, userId))) return ctx.editMessageText('⛔ Access denied.');
	const ticket = await db.getTicketById(env, ticketDbId);
	if (!ticket) return ctx.editMessageText('❌ Ticket not found.');
	const messages = await db.getTicketMessages(env, ticketDbId, 5);
	const history = messages.map(m => {
		const role = m.sender_role === 'user' ? '👤' : '🛠';
		const content = m.text_content || `[${m.content_type}]`;
		return `${role} ${content}`;
	}).join('\n') || 'No messages yet.';
	const assigned = ticket.assigned_admin_id ? `Admin ${ticket.assigned_admin_id}` : 'Unassigned';
	const text =
		`🎫 <b>Ticket ${ticket.ticket_id}</b>\n` +
		`👤 User: ${ticket.first_name || 'N/A'}${ticket.username ? ` (@${ticket.username})` : ''}\n` +
		`📌 Status: ${ticket.status}\n` +
		`👨‍💼 Assigned: ${assigned}\n` +
		`📅 Created: ${ticket.created_at}\n\n` +
		`<b>Recent messages:</b>\n${history}`;
	await ctx.editMessageText(text, {
		parse_mode: 'HTML',
		reply_markup: kb.adminTicketView(ticketDbId),
	});
}

async function replyTicketStart(ctx: Context, env: Env, ticketDbId: number) {
	await ctx.answerCallbackQuery();
	const adminId = ctx.from!.id;
	if (!(await db.isAdmin(env, adminId))) return;
	await setAdminState(env, adminId, { action: 'reply', ticketId: ticketDbId });
	await ctx.editMessageText('✏️ Send your reply now. Press Cancel to abort.', {
		reply_markup: new InlineKeyboard().text('Cancel Reply', `cancel_reply_${ticketDbId}`),
	});
}

async function cancelReplyHandler(ctx: Context, env: Env, ticketDbId: number) {
	await ctx.answerCallbackQuery();
	const adminId = ctx.from!.id;
	await clearAdminState(env, adminId);
	await ctx.editMessageText('❌ Reply cancelled.');
}

async function assignTicketHandler(ctx: Context, env: Env, ticketDbId: number) {
	await ctx.answerCallbackQuery();
	const adminId = ctx.from!.id;
	if (!(await db.isAdmin(env, adminId))) return;
	await db.assignTicket(env, ticketDbId, adminId);
	const ticket = await db.getTicketById(env, ticketDbId);
	await ctx.editMessageText(`✅ Ticket ${ticket!.ticket_id} assigned to you.`);
}

async function adminCloseTicketHandler(ctx: Context, env: Env, ticketDbId: number) {
	await ctx.answerCallbackQuery();
	const adminId = ctx.from!.id;
	if (!(await db.isAdmin(env, adminId))) return;
	const ticket = await db.getTicketById(env, ticketDbId);
	if (!ticket || ticket.status !== 'open') return ctx.editMessageText('Ticket already closed.');
	await db.closeTicket(env, ticketDbId);
	try {
		await ctx.api.sendMessage(ticket.user_id, `🔒 Your ticket ${ticket.ticket_id} has been closed by an admin.`);
	} catch { /* ignore */ }
	await ctx.editMessageText(`✅ Ticket ${ticket.ticket_id} closed.`);
}

async function banUserHandler(ctx: Context, env: Env, ticketDbId: number) {
	await ctx.answerCallbackQuery();
	const adminId = ctx.from!.id;
	if (!(await db.isAdmin(env, adminId))) return;
	const ticket = await db.getTicketById(env, ticketDbId);
	if (!ticket) return ctx.editMessageText('❌ Ticket not found.');
	await db.banUser(env, ticket.user_id);
	await db.closeTicket(env, ticketDbId);
	try {
		await ctx.api.sendMessage(ticket.user_id, '⛔ You have been banned from creating tickets.');
	} catch { /* ignore */ }
	await ctx.editMessageText(`🚫 User ${ticket.user_id} banned and ticket closed.`);
}

// ---------- Admin stateful message handling ----------
async function handleAdminStateMessage(ctx: Context, next: () => Promise<void>) {
	const env = (ctx as any).env as Env;
	const adminId = ctx.from!.id;
	// Only process if this is an admin and there's a pending state
	if (!(await db.isAdmin(env, adminId))) return next();
	const state = await getAdminState(env, adminId);
	if (!state) return next(); // no pending action, ignore

	// Handle different actions
	if (state.action === 'reply') {
		await processReply(ctx, env, adminId, state.ticketId!);
		return;
	}
	if (state.action === 'search') {
		await processSearch(ctx, env, adminId);
		return;
	}
	if (state.action === 'awaiting_admin_forward') {
		await processAddAdminForward(ctx, env, adminId);
		return;
	}
	// Fallback
	return next();
}

async function processReply(ctx: Context, env: Env, adminId: number, ticketDbId: number) {
	const ticket = await db.getTicketById(env, ticketDbId);
	if (!ticket) {
		await ctx.reply('❌ Ticket not found.');
		await clearAdminState(env, adminId);
		return;
	}
	try {
		// Forward the admin's message to the ticket owner
		await ctx.api.copyMessage(ticket.user_id, adminId, ctx.msg!.message_id);
	} catch {
		await ctx.reply('⚠️ Failed to send reply. User may have blocked the bot.');
	}
	const content = extractMessageContent(ctx.msg!);
	await db.saveMessage(env, ticketDbId, adminId, 'admin', content.type, content.file_id, content.text);
	await clearAdminState(env, adminId);
	// Notify admin
	await ctx.reply('✅ Reply sent.');
}

async function processSearch(ctx: Context, env: Env, adminId: number) {
	const query = ctx.msg!.text?.trim();
	if (!query) {
		await ctx.reply('Please send a ticket ID or user ID.');
		return;
	}
	await clearAdminState(env, adminId);
	let ticket: any = null;
	if (query.toUpperCase().startsWith('TCK-')) {
		const { results } = await env.DB.prepare('SELECT * FROM tickets WHERE ticket_id = ?').bind(query).run();
		ticket = results[0];
	} else {
		const userId = parseInt(query);
		if (!isNaN(userId)) {
			const { results } = await env.DB.prepare('SELECT * FROM tickets WHERE user_id = ? AND status = ?').bind(userId, 'open').run();
			ticket = results[0];
		}
	}
	if (!ticket) {
		await ctx.reply('No matching open ticket found.');
		return;
	}
	await ctx.reply(`Found ticket: ${ticket.ticket_id}`, {
		reply_markup: kb.adminTicketView(ticket.id),
	});
}

async function processAddAdminForward(ctx: Context, env: Env, adminId: number) {
	if (adminId !== parseInt(env.OWNER_ID)) {
		await clearAdminState(env, adminId);
		return;
	}
	const forwardFrom = ctx.msg?.forward_from;
	if (!forwardFrom) {
		await ctx.reply('❌ Please forward a message from the user you want to add as admin.');
		return;
	}
	await clearAdminState(env, adminId);
	const newAdminId = forwardFrom.id;
	const success = await db.addAdmin(env, newAdminId, adminId);
	if (success) {
		await ctx.reply(`✅ User ${newAdminId} is now an admin.`);
	} else {
		await ctx.reply('Failed to add admin (maybe already exists).');
	}
}

// ---------- User message forwarding ----------
async function handleUserMessage(ctx: Context) {
	const env = (ctx as any).env as Env;
	const userId = ctx.from!.id;
	if (ctx.msg?.text?.startsWith('/')) return; // ignore commands
	const ticket = await db.getOpenTicketByUser(env, userId);
	if (!ticket) return; // not in a ticket, ignore

	const content = extractMessageContent(ctx.msg!);
	await db.saveMessage(env, ticket.id, userId, 'user', content.type, content.file_id, content.text);

	// Forward to assigned admin or all admins
	const recipients = new Set<number>();
	if (ticket.assigned_admin_id) {
		recipients.add(ticket.assigned_admin_id);
	} else {
		recipients.add(parseInt(env.OWNER_ID));
		(await db.getAllAdmins(env)).forEach(id => recipients.add(id));
	}
	for (const adminId of recipients) {
		try {
			await ctx.api.copyMessage(adminId, userId, ctx.msg!.message_id);
		} catch { /* ignore */ }
	}
}

// ---------- Search ticket start (admin) ----------
async function searchTicketStart(ctx: Context, env: Env) {
	await ctx.answerCallbackQuery();
	const adminId = ctx.from!.id;
	await setAdminState(env, adminId, { action: 'search' });
	await ctx.editMessageText('🔍 Send me the ticket ID (e.g., TCK-000001) or user ID to search.');
}

// ---------- Statistics ----------
async function statisticsHandler(ctx: Context, env: Env) {
	await ctx.answerCallbackQuery();
	const userId = ctx.from!.id;
	if (!(await db.isAdmin(env, userId))) return;
	const stats = await db.getStatistics(env);
	const text =
		`📊 <b>Statistics</b>\n` +
		`👥 Total Users: ${stats.totalUsers}\n` +
		`📂 Open Tickets: ${stats.openTickets}\n` +
		`🔒 Closed Tickets: ${stats.closedTickets}\n` +
		`📑 Total Tickets: ${stats.totalTickets}\n` +
		`👨‍💼 Active Admins: ${stats.activeAdmins}`;
	await ctx.editMessageText(text, { parse_mode: 'HTML' });
}

// ---------- Admin management (owner only) ----------
async function adminManagementHandler(ctx: Context, env: Env) {
	await ctx.answerCallbackQuery();
	if (ctx.from!.id !== parseInt(env.OWNER_ID)) return;
	await ctx.editMessageText('🛡 Admin Management', {
		reply_markup: new InlineKeyboard()
			.text('➕ Add Admin', 'add_admin')
			.row()
			.text('➖ Remove Admin', 'remove_admin')
			.row()
			.text('🔙 Back', 'admin_panel'),
	});
}

async function addAdminStart(ctx: Context, env: Env) {
	await ctx.answerCallbackQuery();
	if (ctx.from!.id !== parseInt(env.OWNER_ID)) return;
	await setAdminState(env, ctx.from!.id, { action: 'awaiting_admin_forward' });
	await ctx.editMessageText('Forward a message from the user you want to make an admin.');
}

async function removeAdminStart(ctx: Context, env: Env) {
	await ctx.answerCallbackQuery();
	if (ctx.from!.id !== parseInt(env.OWNER_ID)) return;
	const admins = await db.getAllAdmins(env);
	const adminList = admins.filter(id => id !== parseInt(env.OWNER_ID));
	if (adminList.length === 0) {
		await ctx.editMessageText('No additional admins to remove.');
		return;
	}
	const keyboard = new InlineKeyboard();
	adminList.forEach(id => keyboard.text(`Admin ${id}`, `remove_admin_confirm_${id}`).row());
	keyboard.text('🔙 Back', 'admin_management');
	await ctx.editMessageText('Select an admin to remove:', { reply_markup: keyboard });
}

async function removeAdminConfirm(ctx: Context, env: Env) {
	await ctx.answerCallbackQuery();
	if (ctx.from!.id !== parseInt(env.OWNER_ID)) return;
	const targetId = parseInt(ctx.callbackQuery!.data!.split('_')[3]);
	const success = await db.removeAdmin(env, targetId);
	if (success) {
		await ctx.editMessageText(`✅ Admin ${targetId} removed.`);
	} else {
		await ctx.editMessageText('❌ Failed to remove admin.');
	}
}

// ---------- Scheduled cleanup ----------
export async function handleScheduled(env: Env) {
	await db.cleanupOldTickets(env);
	console.log('Scheduled cleanup completed.');
    }
