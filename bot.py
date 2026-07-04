```python
# bot.py
"""
Telegram Support Ticket Bot
Single-file bot with SQLite storage, inline keyboards, and admin management.
"""

import os
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    CallbackContext,
)

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

if not BOT_TOKEN or not OWNER_ID:
    raise ValueError("BOT_TOKEN and OWNER_ID environment variables must be set.")

# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# In-memory states (no persistent storage needed for short-lived states)
# ----------------------------------------------------------------------
# Maps admin user_id -> ticket_db_id they are replying to
admin_reply_state: Dict[int, int] = {}
# Maps admin user_id -> id of the prompt message for "Cancel Reply"
reply_prompt_msg: Dict[int, int] = {}
# Owner-only: set when awaiting a forwarded message to add an admin
awaiting_admin_forward: bool = False

# ----------------------------------------------------------------------
# Database helpers
# ----------------------------------------------------------------------
DB_NAME = "support.db"


def get_db_connection() -> sqlite3.Connection:
    """Create a new database connection with row factory enabled."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrency
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables if they don't exist and ensure the owner is an admin."""
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT UNIQUE,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                status TEXT DEFAULT 'open',
                assigned_admin_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL,
                sender_role TEXT NOT NULL,  -- 'user' or 'admin'
                content_type TEXT NOT NULL,
                file_id TEXT,
                text_content TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
            )
            """
        )
        # Ensure owner is always an admin
        conn.execute(
            "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)",
            (OWNER_ID, OWNER_ID),
        )
        conn.commit()


# ----------------------------------------------------------------------
# Admin management
# ----------------------------------------------------------------------
def is_admin(user_id: int) -> bool:
    """Check if a user is an admin (owner or in admins table)."""
    if user_id == OWNER_ID:
        return True
    with get_db_connection() as conn:
        row = conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)).fetchone()
        return row is not None


def add_admin(user_id: int, added_by: int) -> bool:
    """Add a new admin. Returns True if successful, False if already exists."""
    with get_db_connection() as conn:
        try:
            conn.execute(
                "INSERT INTO admins (user_id, added_by) VALUES (?, ?)",
                (user_id, added_by),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def remove_admin(user_id: int) -> bool:
    """Remove an admin (cannot remove owner). Returns True if removed."""
    if user_id == OWNER_ID:
        return False
    with get_db_connection() as conn:
        cur = conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount > 0


def get_all_admins() -> List[sqlite3.Row]:
    """Return list of all admin rows (excluding owner to keep it separate?)."""
    with get_db_connection() as conn:
        return conn.execute("SELECT * FROM admins ORDER BY user_id").fetchall()


# ----------------------------------------------------------------------
# Ticket operations
# ----------------------------------------------------------------------
def get_open_ticket_by_user(user_id: int) -> Optional[sqlite3.Row]:
    """Return the open ticket for a given user, or None."""
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT * FROM tickets WHERE user_id = ? AND status = 'open'",
            (user_id,),
        ).fetchone()


def create_ticket(user: Any) -> sqlite3.Row:
    """Create a new ticket for the user. Returns the ticket row."""
    with get_db_connection() as conn:
        cur = conn.execute(
            "INSERT INTO tickets (user_id, username, first_name) VALUES (?, ?, ?)",
            (user.id, user.username, user.first_name),
        )
        ticket_pk = cur.lastrowid
        ticket_id = f"TCK-{ticket_pk:06d}"
        conn.execute(
            "UPDATE tickets SET ticket_id = ? WHERE id = ?", (ticket_id, ticket_pk)
        )
        conn.commit()
        return conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_pk,)).fetchone()


def close_ticket(ticket_db_id: int, by_admin: bool = False) -> None:
    """Close a ticket and set closed_at timestamp."""
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE tickets SET status = 'closed', closed_at = ? WHERE id = ?",
            (datetime.utcnow(), ticket_db_id),
        )
        conn.commit()


def get_open_tickets() -> List[sqlite3.Row]:
    """Return all open tickets."""
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT * FROM tickets WHERE status = 'open' ORDER BY created_at ASC"
        ).fetchall()


def get_ticket_by_id(ticket_db_id: int) -> Optional[sqlite3.Row]:
    """Get a single ticket by its internal id."""
    with get_db_connection() as conn:
        return conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_db_id,)).fetchone()


def assign_ticket(ticket_db_id: int, admin_id: int) -> None:
    """Assign a ticket to an admin."""
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE tickets SET assigned_admin_id = ? WHERE id = ?",
            (admin_id, ticket_db_id),
        )
        conn.commit()


# ----------------------------------------------------------------------
# Message storage
# ----------------------------------------------------------------------
def save_message(
    ticket_db_id: int,
    sender_id: int,
    sender_role: str,
    content_type: str,
    file_id: Optional[str] = None,
    text_content: Optional[str] = None,
) -> None:
    """Insert a message record into the database."""
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO messages (ticket_id, sender_id, sender_role, content_type, file_id, text_content) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ticket_db_id, sender_id, sender_role, content_type, file_id, text_content),
        )
        conn.commit()


def get_ticket_messages(ticket_db_id: int, limit: int = 10) -> List[sqlite3.Row]:
    """Retrieve recent messages for a ticket (newest first)."""
    with get_db_connection() as conn:
        return conn.execute(
            "SELECT * FROM messages WHERE ticket_id = ? ORDER BY timestamp DESC LIMIT ?",
            (ticket_db_id, limit),
        ).fetchall()


# ----------------------------------------------------------------------
# Ban management
# ----------------------------------------------------------------------
def ban_user(user_id: int) -> None:
    """Ban a user from creating tickets."""
    with get_db_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO banned_users (user_id) VALUES (?)", (user_id,))
        conn.commit()


def is_user_banned(user_id: int) -> bool:
    """Check if a user is banned."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,)).fetchone()
        return row is not None


# ----------------------------------------------------------------------
# Message content extraction helper
# ----------------------------------------------------------------------
def extract_message_content(message: Any) -> tuple[str, Optional[str], Optional[str]]:
    """
    Determine content type, file_id, and text from a Telegram message.
    Returns (content_type, file_id, text_content).
    """
    if message.text:
        return "text", None, message.text
    if message.photo:
        # largest photo
        return "photo", message.photo[-1].file_id, message.caption or None
    if message.video:
        return "video", message.video.file_id, message.caption or None
    if message.document:
        return "document", message.document.file_id, message.caption or None
    if message.voice:
        return "voice", message.voice.file_id, None
    if message.audio:
        return "audio", message.audio.file_id, message.caption or None
    if message.sticker:
        return "sticker", message.sticker.file_id, None
    if message.animation:
        return "animation", message.animation.file_id, message.caption or None
    if message.video_note:
        return "video_note", message.video_note.file_id, None
    # fallback
    return "unknown", None, None


# ----------------------------------------------------------------------
# Automated cleanup of old closed tickets
# ----------------------------------------------------------------------
async def cleanup_old_tickets(context: CallbackContext) -> None:
    """Delete closed tickets and their messages older than 7 days."""
    cutoff = datetime.utcnow() - timedelta(days=7)
    with get_db_connection() as conn:
        # Find tickets eligible for deletion
        rows = conn.execute(
            "SELECT id FROM tickets WHERE status = 'closed' AND closed_at < ?",
            (cutoff,),
        ).fetchall()
        ticket_ids = [row["id"] for row in rows]
        if ticket_ids:
            # Delete messages first (foreign key)
            conn.executemany("DELETE FROM messages WHERE ticket_id = ?", [(tid,) for tid in ticket_ids])
            conn.executemany("DELETE FROM tickets WHERE id = ?", [(tid,) for tid in ticket_ids])
            conn.commit()
            logger.info(f"Cleaned up {len(ticket_ids)} old closed tickets.")


# ----------------------------------------------------------------------
# Keyboard builders
# ----------------------------------------------------------------------
def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Build the main start menu depending on user role."""
    buttons = [
        [InlineKeyboardButton("📝 Create Ticket", callback_data="create_ticket")],
        [InlineKeyboardButton("📋 My Ticket", callback_data="my_ticket")],
    ]
    if is_admin(user_id):
        buttons.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(buttons)


def user_ticket_keyboard(ticket_db_id: int) -> InlineKeyboardMarkup:
    """Keyboard shown to a user who has an open ticket."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔒 Close Ticket", callback_data=f"close_ticket_{ticket_db_id}")],
    ])


def admin_ticket_keyboard(ticket_db_id: int) -> InlineKeyboardMarkup:
    """Keyboard shown to an admin viewing a specific ticket."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💬 Reply", callback_data=f"reply_ticket_{ticket_db_id}"),
            InlineKeyboardButton("👤 Assign Me", callback_data=f"assign_ticket_{ticket_db_id}"),
        ],
        [
            InlineKeyboardButton("❌ Close", callback_data=f"admin_close_{ticket_db_id}"),
            InlineKeyboardButton("🚫 Ban User", callback_data=f"ban_user_{ticket_db_id}"),
        ],
        [InlineKeyboardButton("🔙 Back", callback_data="open_tickets")],
    ])


def admin_panel_keyboard(owner: bool = False) -> InlineKeyboardMarkup:
    """Admin panel with management options (owner sees add/remove)."""
    buttons = [
        [InlineKeyboardButton("📂 Open Tickets", callback_data="open_tickets")],
    ]
    if owner:
        buttons.append([InlineKeyboardButton("➕ Add Admin", callback_data="add_admin")])
        buttons.append([InlineKeyboardButton("➖ Remove Admin", callback_data="remove_admin")])
    buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])
    return InlineKeyboardMarkup(buttons)


def open_tickets_list_keyboard(tickets: List[sqlite3.Row]) -> InlineKeyboardMarkup:
    """Create a keyboard listing all open tickets."""
    keyboard = []
    for t in tickets:
        user_info = f"{t['first_name'] or 'User'}"
        if t["username"]:
            user_info += f" (@{t['username']})"
        label = f"{t['ticket_id']} - {user_info}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"view_ticket_{t['id']}")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
    return InlineKeyboardMarkup(keyboard)


# ----------------------------------------------------------------------
# Handler functions
# ----------------------------------------------------------------------
async def start(update: Update, context: CallbackContext) -> None:
    """Send the main menu on /start."""
    user = update.effective_user
    await update.message.reply_text(
        "👋 Welcome to the Support Bot!\nChoose an option:",
        reply_markup=main_menu_keyboard(user.id),
    )


async def main_menu_callback(update: Update, context: CallbackContext) -> None:
    """Handle 'main_menu' callback – resend the main menu."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "👋 Welcome to the Support Bot!\nChoose an option:",
        reply_markup=main_menu_keyboard(query.from_user.id),
    )


# --- User ticket actions ---
async def create_ticket_handler(update: Update, context: CallbackContext) -> None:
    """Create a new support ticket or show existing one."""
    query = update.callback_query
    user = query.from_user
    await query.answer()

    if is_user_banned(user.id):
        await query.edit_message_text("⛔ You are banned from creating tickets.")
        return

    existing = get_open_ticket_by_user(user.id)
    if existing:
        await query.edit_message_text(
            f"ℹ️ You already have an open ticket: {existing['ticket_id']}\n"
            "Use 'My Ticket' to manage it.",
        )
        return

    ticket = create_ticket(user)
    await query.edit_message_text(
        f"✅ Ticket {ticket['ticket_id']} created!\n"
        "Send your message, photo, video, etc. We will reply as soon as possible.",
        reply_markup=user_ticket_keyboard(ticket["id"]),
    )
    logger.info(f"User {user.id} created ticket {ticket['ticket_id']}")


async def my_ticket_handler(update: Update, context: CallbackContext) -> None:
    """Show the user's open ticket or inform that none exists."""
    query = update.callback_query
    user = query.from_user
    await query.answer()

    ticket = get_open_ticket_by_user(user.id)
    if not ticket:
        await query.edit_message_text("📭 You have no open tickets.")
        return

    # Show ticket info and close button
    assigned = "Not assigned" if not ticket["assigned_admin_id"] else f"Admin {ticket['assigned_admin_id']}"
    text = (
        f"🎫 Ticket {ticket['ticket_id']}\n"
        f"Status: {ticket['status']}\n"
        f"Assigned: {assigned}\n"
        f"Created: {ticket['created_at']}"
    )
    await query.edit_message_text(text, reply_markup=user_ticket_keyboard(ticket["id"]))


async def user_close_ticket(update: Update, context: CallbackContext) -> None:
    """Handle user closing their own ticket."""
    query = update.callback_query
    await query.answer()

    _, ticket_db_id_str = query.data.split("_close_ticket_")  # but data is "close_ticket_{id}"
    # Actually pattern: close_ticket_{id}
    # We'll extract properly
    data = query.data
    if not data.startswith("close_ticket_"):
        return
    ticket_db_id = int(data.split("_")[-1])  # crude but works
    ticket = get_ticket_by_id(ticket_db_id)
    if not ticket or ticket["user_id"] != query.from_user.id:
        await query.edit_message_text("❌ Ticket not found or access denied.")
        return
    if ticket["status"] != "open":
        await query.edit_message_text("This ticket is already closed.")
        return

    close_ticket(ticket_db_id, by_admin=False)
    await query.edit_message_text(f"🔒 Ticket {ticket['ticket_id']} closed.")
    logger.info(f"User {query.from_user.id} closed ticket {ticket['ticket_id']}")


# --- Admin panel ---
async def admin_panel_handler(update: Update, context: CallbackContext) -> None:
    """Show the admin panel (only for admins)."""
    query = update.callback_query
    user = query.from_user
    await query.answer()

    if not is_admin(user.id):
        await query.edit_message_text("⛔ Access denied.")
        return

    is_owner = (user.id == OWNER_ID)
    await query.edit_message_text(
        "🔧 Admin Panel", reply_markup=admin_panel_keyboard(owner=is_owner)
    )


async def open_tickets_handler(update: Update, context: CallbackContext) -> None:
    """List all open tickets for admins."""
    query = update.callback_query
    user = query.from_user
    await query.answer()

    if not is_admin(user.id):
        await query.edit_message_text("⛔ Access denied.")
        return

    tickets = get_open_tickets()
    if not tickets:
        await query.edit_message_text("✅ No open tickets.")
        return

    await query.edit_message_text(
        "📂 Open tickets (tap to view):",
        reply_markup=open_tickets_list_keyboard(tickets),
    )


async def view_ticket_handler(update: Update, context: CallbackContext) -> None:
    """Show ticket details for an admin."""
    query = update.callback_query
    user = query.from_user
    await query.answer()

    if not is_admin(user.id):
        await query.edit_message_text("⛔ Access denied.")
        return

    data = query.data  # "view_ticket_{id}"
    ticket_db_id = int(data.split("_")[-1])
    ticket = get_ticket_by_id(ticket_db_id)
    if not ticket:
        await query.edit_message_text("❌ Ticket not found.")
        return

    # Get recent messages
    msgs = get_ticket_messages(ticket_db_id, limit=5)
    history = "\n".join(
        f"{'👤 User' if m['sender_role'] == 'user' else '🛠 Admin'}: "
        f"{m['text_content'] or '[' + m['content_type'] + ']'}"[:200]
        for m in reversed(msgs)
    ) or "No messages yet."

    assigned = f"Admin {ticket['assigned_admin_id']}" if ticket["assigned_admin_id"] else "Unassigned"
    text = (
        f"🎫 <b>Ticket {ticket['ticket_id']}</b>\n"
        f"👤 User: {ticket['first_name'] or 'N/A'}"
        + (f" (@{ticket['username']})" if ticket['username'] else "") +
        f"\n📌 Status: {ticket['status']}\n"
        f"👨‍💼 Assigned: {assigned}\n"
        f"📅 Created: {ticket['created_at']}\n\n"
        f"<b>Recent messages:</b>\n{history}"
    )
    await query.edit_message_text(
        text,
        reply_markup=admin_ticket_keyboard(ticket_db_id),
        parse_mode="HTML",
    )


async def reply_ticket_start(update: Update, context: CallbackContext) -> None:
    """Prompt admin to send a reply."""
    query = update.callback_query
    admin_id = query.from_user.id
    await query.answer()

    if not is_admin(admin_id):
        return

    data = query.data  # "reply_ticket_{id}"
    ticket_db_id = int(data.split("_")[-1])
    ticket = get_ticket_by_id(ticket_db_id)
    if not ticket:
        await query.edit_message_text("❌ Ticket not found.")
        return

    # Send a prompt with cancel button
    cancel_button = InlineKeyboardButton("Cancel Reply", callback_data=f"cancel_reply_{ticket_db_id}")
    prompt = await query.message.reply_text(
        f"✏️ Replying to {ticket['ticket_id']}. Send your message now.",
        reply_markup=InlineKeyboardMarkup([[cancel_button]]),
    )
    # Store state
    admin_reply_state[admin_id] = ticket_db_id
    reply_prompt_msg[admin_id] = prompt.message_id


async def cancel_reply_handler(update: Update, context: CallbackContext) -> None:
    """Cancel the pending reply and clean state."""
    query = update.callback_query
    admin_id = query.from_user.id
    await query.answer()

    data = query.data  # "cancel_reply_{ticket_id}"
    ticket_db_id = int(data.split("_")[-1])

    # Remove state if it matches
    if admin_reply_state.get(admin_id) == ticket_db_id:
        del admin_reply_state[admin_id]
        if admin_id in reply_prompt_msg:
            del reply_prompt_msg[admin_id]
    # Edit the prompt message (we need the message id; but we have query.message, which is the cancel button message)
    await query.edit_message_text("❌ Reply cancelled.")


async def process_reply_message(update: Update, context: CallbackContext) -> None:
    """Forward admin's reply to the user and clear the reply state."""
    admin_id = update.effective_user.id
    message = update.effective_message

    ticket_db_id = admin_reply_state.get(admin_id)
    if not ticket_db_id:
        return  # not in reply state

    ticket = get_ticket_by_id(ticket_db_id)
    if not ticket:
        # Shouldn't happen
        await message.reply_text("❌ Ticket not found.")
        admin_reply_state.pop(admin_id, None)
        reply_prompt_msg.pop(admin_id, None)
        return

    # Copy the message to the ticket owner
    try:
        await context.bot.copy_message(
            chat_id=ticket["user_id"],
            from_chat_id=admin_id,
            message_id=message.message_id,
        )
        # Save to DB
        content_type, file_id, text_content = extract_message_content(message)
        save_message(ticket_db_id, admin_id, "admin", content_type, file_id, text_content)
    except Exception as e:
        logger.error(f"Failed to send reply to user {ticket['user_id']}: {e}")
        await message.reply_text("⚠️ Failed to send reply. The user may have blocked the bot.")
    else:
        # Edit the prompt message to indicate success
        prompt_msg_id = reply_prompt_msg.get(admin_id)
        if prompt_msg_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=admin_id,
                    message_id=prompt_msg_id,
                    text="✅ Reply sent.",
                )
            except Exception:
                pass
    finally:
        # Clean up state
        admin_reply_state.pop(admin_id, None)
        reply_prompt_msg.pop(admin_id, None)


async def assign_ticket_handler(update: Update, context: CallbackContext) -> None:
    """Assign the ticket to the admin who clicked 'Assign Me'."""
    query = update.callback_query
    admin_id = query.from_user.id
    await query.answer()

    if not is_admin(admin_id):
        return

    data = query.data  # "assign_ticket_{id}"
    ticket_db_id = int(data.split("_")[-1])
    ticket = get_ticket_by_id(ticket_db_id)
    if not ticket:
        await query.edit_message_text("❌ Ticket not found.")
        return

    assign_ticket(ticket_db_id, admin_id)
    await query.edit_message_text(
        f"✅ Ticket {ticket['ticket_id']} assigned to you.",
    )


async def admin_close_ticket(update: Update, context: CallbackContext) -> None:
    """Admin closes a ticket."""
    query = update.callback_query
    admin_id = query.from_user.id
    await query.answer()

    if not is_admin(admin_id):
        return

    data = query.data  # "admin_close_{id}"
    ticket_db_id = int(data.split("_")[-1])
    ticket = get_ticket_by_id(ticket_db_id)
    if not ticket:
        await query.edit_message_text("❌ Ticket not found.")
        return

    if ticket["status"] != "open":
        await query.edit_message_text("Ticket is already closed.")
        return

    close_ticket(ticket_db_id, by_admin=True)
    # Notify user
    try:
        await context.bot.send_message(
            ticket["user_id"],
            f"🔒 Your ticket {ticket['ticket_id']} has been closed by an admin.",
        )
    except Exception as e:
        logger.warning(f"Could not notify user {ticket['user_id']}: {e}")

    await query.edit_message_text(f"✅ Ticket {ticket['ticket_id']} closed.")


async def ban_user_handler(update: Update, context: CallbackContext) -> None:
    """Ban the user who created this ticket."""
    query = update.callback_query
    admin_id = query.from_user.id
    await query.answer()

    if not is_admin(admin_id):
        return

    data = query.data  # "ban_user_{id}"
    ticket_db_id = int(data.split("_")[-1])
    ticket = get_ticket_by_id(ticket_db_id)
    if not ticket:
        await query.edit_message_text("❌ Ticket not found.")
        return

    ban_user(ticket["user_id"])
    close_ticket(ticket_db_id, by_admin=True)
    try:
        await context.bot.send_message(
            ticket["user_id"],
            "⛔ You have been banned from creating support tickets.",
        )
    except Exception:
        pass
    await query.edit_message_text(
        f"🚫 User {ticket['user_id']} banned and ticket {ticket['ticket_id']} closed."
    )
    logger.info(f"Admin {admin_id} banned user {ticket['user_id']}")


# --- Add / Remove admin (owner only) ---
async def add_admin_start(update: Update, context: CallbackContext) -> None:
    """Owner clicks 'Add Admin' -> ask for forwarded message."""
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        await query.answer("⛔ Owner only.")
        return
    await query.answer()
    global awaiting_admin_forward
    awaiting_admin_forward = True
    await query.edit_message_text(
        "➕ To add an admin, forward a message from that user now.\n"
        "Press /cancel to abort."
    )


async def handle_forwarded_admin_add(update: Update, context: CallbackContext) -> None:
    """Process a forwarded message to add a new admin."""
    global awaiting_admin_forward
    if not awaiting_admin_forward or update.effective_user.id != OWNER_ID:
        return
    message = update.effective_message
    if not message.forward_from:
        await message.reply_text("❌ That message was not forwarded from a user. Please try again.")
        return
    new_admin_id = message.forward_from.id
    if is_admin(new_admin_id):
        await message.reply_text("ℹ️ That user is already an admin.")
        awaiting_admin_forward = False
        return
    success = add_admin(new_admin_id, OWNER_ID)
    if success:
        await message.reply_text(f"✅ User {new_admin_id} is now an admin.")
        logger.info(f"Owner added admin {new_admin_id}")
    else:
        await message.reply_text("❌ Failed to add admin (maybe already exists).")
    awaiting_admin_forward = False


async def remove_admin_start(update: Update, context: CallbackContext) -> None:
    """Owner clicks 'Remove Admin' -> list current admins (excluding owner)."""
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        await query.answer("⛔ Owner only.")
        return
    await query.answer()
    admins = get_all_admins()
    # Filter out owner
    admins_list = [a for a in admins if a["user_id"] != OWNER_ID]
    if not admins_list:
        await query.edit_message_text("ℹ️ No additional admins to remove.")
        return
    keyboard = []
    for a in admins_list:
        label = f"Admin {a['user_id']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"remove_admin_{a['user_id']}")])
    keyboard.append([InlineKeyboardButton("🔙 Cancel", callback_data="admin_panel")])
    await query.edit_message_text(
        "➖ Select an admin to remove:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def confirm_remove_admin(update: Update, context: CallbackContext) -> None:
    """Remove the chosen admin."""
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        await query.answer("⛔ Owner only.")
        return
    await query.answer()
    data = query.data  # "remove_admin_{user_id}"
    admin_user_id = int(data.split("_")[-1])
    if remove_admin(admin_user_id):
        await query.edit_message_text(f"✅ Admin {admin_user_id} removed.")
        logger.info(f"Owner removed admin {admin_user_id}")
    else:
        await query.edit_message_text("❌ Could not remove admin (maybe not found or is owner).")


# --- User message forwarding ---
async def handle_user_message(update: Update, context: CallbackContext) -> None:
    """Forward messages from users with an open ticket to admins."""
    user = update.effective_user
    message = update.effective_message
    # Ignore commands
    if message.text and message.text.startswith("/"):
        return

    ticket = get_open_ticket_by_user(user.id)
    if not ticket:
        # Not in a ticket, do nothing
        return

    # Determine recipients
    assigned = ticket["assigned_admin_id"]
    recipients = set()
    if assigned:
        recipients.add(assigned)
    else:
        # All admins (owner + registered admins)
        recipients.add(OWNER_ID)
        admins = get_all_admins()
        for a in admins:
            recipients.add(a["user_id"])

    # Forward the message to each recipient
    for admin_id in recipients:
        try:
            await context.bot.copy_message(
                chat_id=admin_id,
                from_chat_id=user.id,
                message_id=message.message_id,
            )
        except Exception as e:
            logger.error(f"Failed to forward message to admin {admin_id}: {e}")

    # Save to DB
    content_type, file_id, text_content = extract_message_content(message)
    save_message(ticket["id"], user.id, "user", content_type, file_id, text_content)


# ----------------------------------------------------------------------
# Fallback / cancel command (to exit add-admin state)
# ----------------------------------------------------------------------
async def cancel_command(update: Update, context: CallbackContext) -> None:
    """Cancel any ongoing owner-only flow (like adding admin)."""
    global awaiting_admin_forward
    if update.effective_user.id == OWNER_ID and awaiting_admin_forward:
        awaiting_admin_forward = False
        await update.message.reply_text("❎ Add admin flow cancelled.")


# ----------------------------------------------------------------------
# Main application setup
# ----------------------------------------------------------------------
def main() -> None:
    """Start the bot."""
    init_db()
    logger.info("Database initialized.")

    app = Application.builder().token(BOT_TOKEN).build()

    # Periodic cleanup job (every 24 hours)
    app.job_queue.run_repeating(cleanup_old_tickets, interval=86400, first=10)

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel_command))

    # Callback query handlers (order matters for overlapping patterns)
    app.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(create_ticket_handler, pattern="^create_ticket$"))
    app.add_handler(CallbackQueryHandler(my_ticket_handler, pattern="^my_ticket$"))
    app.add_handler(CallbackQueryHandler(user_close_ticket, pattern=r"^close_ticket_\d+$"))
    app.add_handler(CallbackQueryHandler(admin_panel_handler, pattern="^admin_panel$"))
    app.add_handler(CallbackQueryHandler(open_tickets_handler, pattern="^open_tickets$"))
    app.add_handler(CallbackQueryHandler(view_ticket_handler, pattern=r"^view_ticket_\d+$"))
    app.add_handler(CallbackQueryHandler(reply_ticket_start, pattern=r"^reply_ticket_\d+$"))
    app.add_handler(CallbackQueryHandler(cancel_reply_handler, pattern=r"^cancel_reply_\d+$"))
    app.add_handler(CallbackQueryHandler(assign_ticket_handler, pattern=r"^assign_ticket_\d+$"))
    app.add_handler(CallbackQueryHandler(admin_close_ticket, pattern=r"^admin_close_\d+$"))
    app.add_handler(CallbackQueryHandler(ban_user_handler, pattern=r"^ban_user_\d+$"))
    app.add_handler(CallbackQueryHandler(add_admin_start, pattern="^add_admin$"))
    app.add_handler(CallbackQueryHandler(remove_admin_start, pattern="^remove_admin$"))
    app.add_handler(CallbackQueryHandler(confirm_remove_admin, pattern=r"^remove_admin_\d+$"))

    # Message handlers (check order: reply first, then add-admin forward, then user messages)
    # Use a custom filter to avoid conflicts; we'll handle inside one handler if possible,
    # but using separate MessageHandlers with a high priority lambda works.
    app.add_handler(MessageHandler(
        filters.ALL & (~filters.COMMAND),  # ignore commands
        process_reply_message,
    ), group=1)  # run before user message forwarding

    app.add_handler(MessageHandler(
        filters.FORWARDED & (~filters.COMMAND),
        handle_forwarded_admin_add,
    ), group=1)

    app.add_handler(MessageHandler(
        filters.ALL & (~filters.COMMAND),
        handle_user_message,
    ), group=2)

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
```

---

Additional Files

requirements.txt

```
python-telegram-bot>=22.0
```

render.yaml

```yaml
services:
  - type: worker
    name: support-ticket-bot
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: python bot.py
    envVars:
      - key: BOT_TOKEN
        sync: false
      - key: OWNER_ID
        sync: false
```

README.md

```markdown
# Telegram Support Ticket Bot

A single-file Telegram bot that provides a complete support ticket system with:

- Inline keyboard navigation
- One active ticket per user
- Media message relay (text, photos, videos, documents, voice, etc.)
- Admin management (owner can add/remove admins)
- Automatic cleanup of closed tickets older than 7 days
- SQLite storage (no external database required)

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
```

2. Set environment variables:
   ```bash
   export BOT_TOKEN="your_telegram_bot_token"
   export OWNER_ID="your_telegram_user_id"
   ```
3. Run the bot:
   ```bash
   python bot.py
   ```

Usage

· /start – Open the main menu.
· Users can create a ticket, view their existing ticket, and close it.
· Admins (including the owner) can view all open tickets, reply, assign themselves, close, ban users, and manage admin lists.
· The owner can add admins by forwarding a message from the target user, and remove admins through a selection menu.
· All tickets and messages are automatically deleted 7 days after closure.

Deployment

A render.yaml file is provided for easy deployment on Render as a background worker.

```
