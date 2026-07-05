/**
 * Extract content type, file_id and text from a Telegram message.
 */
export function extractMessageContent(msg: any): { type: string; file_id: string | null; text: string | null } {
	if (msg.text) return { type: 'text', file_id: null, text: msg.text };
	if (msg.photo) return { type: 'photo', file_id: msg.photo[msg.photo.length - 1].file_id, text: msg.caption || null };
	if (msg.video) return { type: 'video', file_id: msg.video.file_id, text: msg.caption || null };
	if (msg.document) return { type: 'document', file_id: msg.document.file_id, text: msg.caption || null };
	if (msg.voice) return { type: 'voice', file_id: msg.voice.file_id, text: null };
	if (msg.audio) return { type: 'audio', file_id: msg.audio.file_id, text: msg.caption || null };
	if (msg.sticker) return { type: 'sticker', file_id: msg.sticker.file_id, text: null };
	if (msg.animation) return { type: 'animation', file_id: msg.animation.file_id, text: msg.caption || null };
	if (msg.video_note) return { type: 'video_note', file_id: msg.video_note.file_id, text: null };
	if (msg.contact) return { type: 'contact', file_id: null, text: JSON.stringify(msg.contact) };
	if (msg.location) return { type: 'location', file_id: null, text: JSON.stringify(msg.location) };
	return { type: 'unknown', file_id: null, text: null };
}
