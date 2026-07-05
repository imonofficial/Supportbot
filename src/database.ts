/**
 * All D1 database interactions.
 */
import type { Env } from './index';

interface TicketRow {
	id: number;
	ticket_id: string;
	user_id: number;
	username: string;
	first_name: string;
	status: string;
	assigned_admin_id: number | null;
	created_at: string;
	closed_at: string | null;
}

export async function isAdmin(env: Env, userId: number): Promise<boolean> {
	if (userId === parseInt(env.OWNER_ID)) return true;
	const { results } = await env.DB.prepare('SELECT 1 FROM admins WHERE user_id = ?').bind(userId).run();
	return results.length > 0;
}

export async function addAdmin(env: Env, userId: number, addedBy: number): Promise<boolean> {
	try {
		await env.DB.prepare('INSERT INTO admins (user_id, added_by) VALUES (?, ?)').bind(userId, addedBy).run();
		return true;
	} catch {
		return false;
	}
}

export async function removeAdmin(env: Env, userId: number): Promise<boolean> {
	if (userId === parseInt(env.OWNER_ID)) return false;
	const { success } = await env.DB.prepare('DELETE FROM admins WHERE user_id = ?').bind(userId).run();
	return success;
}

export async function getAllAdmins(env: Env): Promise<number[]> {
	const { results } = await env.DB.prepare('SELECT user_id FROM admins').run();
	return (results as any[]).map((r: any) => r.user_id);
}

export async function isUserBanned(env: Env, userId: number): Promise<boolean> {
	const { results } = await env.DB.prepare('SELECT 1 FROM banned_users WHERE user_id = ?').bind(userId).run();
	return results.length > 0;
}

export async function banUser(env: Env, userId: number): Promise<void> {
	await env.DB.prepare('INSERT OR IGNORE INTO banned_users (user_id) VALUES (?)').bind(userId).run();
}

export async function getOpenTicketByUser(env: Env, userId: number): Promise<TicketRow | null> {
	const { results } = await env.DB.prepare(
		'SELECT * FROM tickets WHERE user_id = ? AND status = ?'
	).bind(userId, 'open').run();
	return (results as any[])[0] || null;
}

export async function createTicket(env: Env, user: { id: number; username?: string; first_name?: string }): Promise<TicketRow> {
	const result = await env.DB.prepare(
		'INSERT INTO tickets (user_id, username, first_name) VALUES (?, ?, ?)'
	).bind(user.id, user.username || null, user.first_name || null).run();
	const pk = result.lastRowId as number;
	const ticketId = `TCK-${pk.toString().padStart(6, '0')}`;
	await env.DB.prepare('UPDATE tickets SET ticket_id = ? WHERE id = ?').bind(ticketId, pk).run();
	const { results } = await env.DB.prepare('SELECT * FROM tickets WHERE id = ?').bind(pk).run();
	return (results as any[])[0] as TicketRow;
}

export async function closeTicket(env: Env, ticketId: number): Promise<void> {
	await env.DB.prepare(
		'UPDATE tickets SET status = ?, closed_at = ? WHERE id = ?'
	).bind('closed', new Date().toISOString(), ticketId).run();
}

export async function getOpenTickets(env: Env, limit = 50, offset = 0): Promise<TicketRow[]> {
	const { results } = await env.DB.prepare(
		'SELECT * FROM tickets WHERE status = ? ORDER BY created_at ASC LIMIT ? OFFSET ?'
	).bind('open', limit, offset).run();
	return (results as any[]) as TicketRow[];
}

export async function getTicketById(env: Env, ticketId: number): Promise<TicketRow | null> {
	const { results } = await env.DB.prepare('SELECT * FROM tickets WHERE id = ?').bind(ticketId).run();
	return (results as any[])[0] || null;
}

export async function assignTicket(env: Env, ticketId: number, adminId: number): Promise<void> {
	await env.DB.prepare('UPDATE tickets SET assigned_admin_id = ? WHERE id = ?').bind(adminId, ticketId).run();
}

export async function saveMessage(
	env: Env,
	ticketId: number,
	senderId: number,
	senderRole: 'user' | 'admin',
	contentType: string,
	fileId: string | null,
	textContent: string | null
): Promise<void> {
	await env.DB.prepare(
		'INSERT INTO messages (ticket_id, sender_id, sender_role, content_type, file_id, text_content) VALUES (?, ?, ?, ?, ?, ?)'
	).bind(ticketId, senderId, senderRole, contentType, fileId, textContent).run();
}

export async function getTicketMessages(env: Env, ticketId: number, limit = 10): Promise<any[]> {
	const { results } = await env.DB.prepare(
		'SELECT * FROM messages WHERE ticket_id = ? ORDER BY timestamp DESC LIMIT ?'
	).bind(ticketId, limit).run();
	return (results as any[]).reverse(); // oldest first
}

export async function getStatistics(env: Env): Promise<{
	totalUsers: number;
	openTickets: number;
	closedTickets: number;
	totalTickets: number;
	activeAdmins: number;
}> {
	const [
		usersResult,
		openResult,
		closedResult,
		totalResult,
		adminsResult,
	] = await Promise.all([
		env.DB.prepare('SELECT COUNT(DISTINCT user_id) as cnt FROM tickets').run(),
		env.DB.prepare("SELECT COUNT(*) as cnt FROM tickets WHERE status = 'open'").run(),
		env.DB.prepare("SELECT COUNT(*) as cnt FROM tickets WHERE status = 'closed'").run(),
		env.DB.prepare('SELECT COUNT(*) as cnt FROM tickets').run(),
		env.DB.prepare('SELECT COUNT(*) as cnt FROM admins').run(),
	]);
	return {
		totalUsers: (usersResult.results[0] as any).cnt,
		openTickets: (openResult.results[0] as any).cnt,
		closedTickets: (closedResult.results[0] as any).cnt,
		totalTickets: (totalResult.results[0] as any).cnt,
		activeAdmins: (adminsResult.results[0] as any).cnt,
	};
}

// Cleanup: delete closed tickets older than 7 days; messages deleted via CASCADE
export async function cleanupOldTickets(env: Env): Promise<void> {
	const cutoff = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();
	await env.DB.prepare("DELETE FROM tickets WHERE status = 'closed' AND closed_at < ?").bind(cutoff).run();
}
