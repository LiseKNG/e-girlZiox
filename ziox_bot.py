#!/usr/bin/env python3
"""
Ziox Bot - Bot Telegram personnel
- Répond aux messages en l'absence du owner
- Reconnaît les membres de la "famille Ziox" via un mot de passe
- Boutons inline pour le menu et les commandes owner
"""

import json
import logging
import os
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============== CONFIGURATION ==============
BOT_TOKEN = "8230994359:AAE5R_UYe3UYKuuLhCDa-rsp4wK1JwwGMa0"

# ⚠️ Remplace par TON ID Telegram (nombre). Pour le trouver : parle à @userinfobot sur Telegram.
OWNER_ID = 123456789  # <-- À REMPLACER

# Mot de passe secret pour que la famille Ziox soit reconnue
FAMILY_PASSWORD = "ZIOX2026"  # <-- modifiable

# Message envoyé automatiquement à la famille Ziox quand tu es absent
ABSENCE_MESSAGE_FAMILY = (
    "👋 Salut, ici le bot de la famille Ziox.\n"
    "Le owner est absent pour le moment, mais votre message a bien été reçu "
    "et lui sera transmis dès son retour. 🙏"
)

DATA_FILE = Path(__file__).parent / "ziox_data.json"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ============== STOCKAGE PERSISTANT ==============
def load_data():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"absence_mode": False, "family_members": {}}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


data = load_data()


# ============== CLAVIERS / BOUTONS ==============
def main_menu_keyboard(is_owner: bool):
    buttons = [
        [InlineKeyboardButton("ℹ️ À propos", callback_data="about")],
        [InlineKeyboardButton("📩 Laisser un message", callback_data="leave_message")],
    ]
    if is_owner:
        status = "🟢 Désactiver l'absence" if data["absence_mode"] else "🔴 Activer l'absence"
        buttons.append([InlineKeyboardButton(status, callback_data="toggle_absence")])
        buttons.append([InlineKeyboardButton("👪 Voir la famille Ziox", callback_data="list_family")])
    return InlineKeyboardMarkup(buttons)


def back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Retour", callback_data="back_menu")]])


# ============== HANDLERS ==============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_owner = user.id == OWNER_ID
    text = f"👋 Bonjour {user.first_name} !\n\nBienvenue sur le bot personnel."
    if is_owner:
        text += "\n\n🔑 Tu es reconnu comme *owner*."
    await update.message.reply_text(
        text, reply_markup=main_menu_keyboard(is_owner), parse_mode="Markdown"
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    is_owner = user.id == OWNER_ID

    if query.data == "about":
        await query.edit_message_text(
            "🤖 Ce bot répond automatiquement quand le owner est absent.\n"
            "La famille Ziox peut être reconnue via un mot de passe secret.",
            reply_markup=back_keyboard(),
        )

    elif query.data == "leave_message":
        await query.edit_message_text(
            "✍️ Envoie simplement ton message ici, il sera transmis au owner.",
            reply_markup=back_keyboard(),
        )

    elif query.data == "back_menu":
        await query.edit_message_text(
            "📋 Menu principal :", reply_markup=main_menu_keyboard(is_owner)
        )

    elif query.data == "toggle_absence" and is_owner:
        data["absence_mode"] = not data["absence_mode"]
        save_data(data)
        status = "activé 🔴" if data["absence_mode"] else "désactivé 🟢"
        await query.edit_message_text(
            f"Mode absence {status}.", reply_markup=main_menu_keyboard(is_owner)
        )

    elif query.data == "list_family" and is_owner:
        members = data["family_members"]
        if not members:
            txt = "Aucun membre de la famille Ziox reconnu pour l'instant."
        else:
            txt = "👪 Famille Ziox reconnue :\n" + "\n".join(
                f"- {v.get('name', 'Inconnu')} (id: {k})" for k, v in members.items()
            )
        await query.edit_message_text(txt, reply_markup=back_keyboard())


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text or ""
    uid = str(user.id)
    is_owner = user.id == OWNER_ID

    # Le owner qui écrit au bot : simple accusé (utile pour tester)
    if is_owner:
        await update.message.reply_text(
            "✅ Reçu (tu es le owner).", reply_markup=main_menu_keyboard(True)
        )
        return

    # Vérifie si le message est le mot de passe famille
    if text.strip() == FAMILY_PASSWORD:
        if uid not in data["family_members"]:
            data["family_members"][uid] = {
                "name": user.full_name,
                "username": user.username,
            }
            save_data(data)
        await update.message.reply_text(
            "✅ Mot de passe reconnu ! Tu fais maintenant partie de la famille Ziox reconnue par ce bot. 👪"
        )
        # Notifie le owner
        try:
            await context.bot.send_message(
                OWNER_ID,
                f"👪 Nouveau membre famille Ziox reconnu : {user.full_name} (@{user.username}, id {uid})",
            )
        except Exception as e:
            logger.warning(f"Impossible de notifier le owner : {e}")
        return

    is_family = uid in data["family_members"]

    if data["absence_mode"]:
        if is_family:
            await update.message.reply_text(ABSENCE_MESSAGE_FAMILY)
            try:
                await context.bot.send_message(
                    OWNER_ID,
                    f"📩 [ABSENCE] Message de la famille Ziox - {user.full_name} :\n{text}",
                )
            except Exception as e:
                logger.warning(f"Impossible de notifier le owner : {e}")
        # Si absent et pas famille : silence total, aucune réponse.
        return

    # Mode normal (owner présent) : accusé de réception + transfert
    await update.message.reply_text(
        "📩 Message bien reçu, il sera transmis au owner. Merci !",
        reply_markup=main_menu_keyboard(False),
    )
    try:
        await context.bot.send_message(
            OWNER_ID,
            f"💬 Message de {user.full_name} (@{user.username}, id {uid}) :\n{text}",
        )
    except Exception as e:
        logger.warning(f"Impossible de notifier le owner : {e}")


async def absence_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /absence réservée au owner pour activer/désactiver rapidement."""
    if update.effective_user.id != OWNER_ID:
        return
    data["absence_mode"] = not data["absence_mode"]
    save_data(data)
    status = "activé 🔴" if data["absence_mode"] else "désactivé 🟢"
    await update.message.reply_text(f"Mode absence {status}.")


def main():
    if OWNER_ID == 123456789:
        logger.warning(
            "⚠️  ATTENTION : OWNER_ID n'a pas été configuré ! Remplace la valeur dans le script."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("absence", absence_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot démarré...")
    app.run_polling()


if __name__ == "__main__":
    main()
