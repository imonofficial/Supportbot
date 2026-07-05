import { InlineKeyboard } from 'grammy';
import type { Env } from './index';
import { isAdmin } from './database';

export function mainMenu(userId: number, env: Env): InlineKeyboard {
	const keyboard = new InlineKeyboard()
		.text('📝 Create Ticket', 'create_ticket')
		.row()
		.text('📋 My Ticket', 'my_ticket')
		.row()
		.text('❓ FAQ', 'faq');
	if (isAdmin(env, userId)) {
		keyboard.row().text('⚙️ Admin Panel', 'admin_panel');
	}
	return keyboard;
}

export function userTicketMenu(ticketDbId: number): InlineKeyboard {
	return new InlineKeyboard().text('🔒 Close Ticket', `user_close_ticket_${ticketDbId}`);
}

export function adminPanel(isOwner: boolean): InlineKeyboard {
	const keyboard = new InlineKeyboard()
		.text('📂 Open Tickets', 'open_tickets')
		.row()
		.text('🔍 Search Ticket', 'search_ticket')
		.row()
		.text('📊 Statistics', 'statistics');
	if (isOwner) {
		keyboard.row().text('🛡 Admin Management', 'admin_management');
	}
	keyboard.row().text('🔙 Main Menu', 'main_menu');
	return keyboard;
}

export function adminTicketView(ticketDbId: number): InlineKeyboard {
	return new InlineKeyboard()
		.text('💬 Reply', `reply_ticket_${ticketDbId}`)
		.text('👤 Assign Me', `assign_ticket_${ticketDbId}`)
		.row()
		.text('❌ Close', `admin_close_ticket_${ticketDbId}`)
		.text('🚫 Ban User', `ban_user_ticket_${ticketDbId}`)
		.row()
		.text('🔙 Back', 'open_tickets');
}

export function openTicketsList(tickets: any[], page: number, totalPages: number): InlineKeyboard {
	const keyboard = new InlineKeyboard();
	tickets.forEach(t => {
		const label = `${t.ticket_id} - ${t.first_name || 'User'}${t.username ? ' @' + t.username : ''}`;
		keyboard.text(label, `view_ticket_${t.id}`).row();
	});
	if (totalPages > 1) {
		const row = [];
		if (page > 1) row.push(InlineKeyboard.text('⬅️ Prev', `open_tickets_page_${page - 1}`));
		if (page < totalPages) row.push(InlineKeyboard.text('Next ➡️', `open_tickets_page_${page + 1}`));
		keyboard.row(...row);
	}
	keyboard.text('🔙 Back', 'admin_panel');
	return keyboard;
}
