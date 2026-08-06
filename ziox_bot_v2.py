#!/usr/bin/env python3
"""
Ziox Bot v2 - Bot Telegram personnel enrichi
- Messages décoratifs (Markdown + emojis) + photo de profil du bot
- Réponses vocales IA via Fish Audio (TTS) pour CHAQUE réponse
- Reconnaissance famille Ziox via mot de passe
- Mode absence (owner)
- Welcome / Goodbye personnalisables avec boutons dans le groupe famille Ziox
"""

import json
import logging
import subprocess
from pathlib import Path

import requests
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ChatMemberHandler,
    ContextTypes,
    filters,
)

# ============== CONFIGURATION ==============
BOT_TOKEN = "8230994359:AAE5R_UYe3UYKuuLhCDa-rsp4wK1JwwGMa0"  # ⚠️ à régénérer via BotFather

OWNER_ID = 8914448661  # <-- REMPLACE par ton ID Telegram (via @userinfobot)

FAMILY_PASSWORD = "ZIOX2026"  # mot de passe famille Ziox, modifiable

# --- Fish Audio TTS ---
FISH_API_KEY = "sk-fish-qW4eIJUOZuqwXGhzjm7n3sS44Bdr-SE4WFpBx4iDsho"  # <-- REMPLACE
FISH_VOICE_REFERENCE_ID = "98655a12fa944e26b274c535e5e03842"  # optionnel : laisse vide pour la voix par défaut
FISH_TTS_ENABLED = True  # passe à False pour désactiver le vocal partout

# --- Messages ---
ABSENCE_MESSAGE_FAMILY = (
    "👋 *Salut !* Ici le bot de la famille Ziox.\n\n"
    "🚫 Le owner est actuellement *absent*, mais votre message a bien été "
    "reçu et lui sera transmis dès son retour. 🙏\n\n"
    "_Merci de votre patience !_ ✨"
)

WELCOME_MESSAGE = (
    "🎉 *Bienvenue {name} dans la famille Ziox !* 👪\n\n"
    "On est ravis de t'accueillir parmi nous. N'hésite pas à te présenter "
    "et à consulter le règlement. ✨"
)

GOODBYE_MESSAGE = (
    "😢 *{name} nous a quitté...*\n\n"
    "On espère te revoir bientôt parmi la famille Ziox. 👋"
)

DATA_FILE = Path(__file__).parent / "ziox_data.json"
TMP_DIR = Path(__file__).parent / "tmp_audio"
TMP_DIR.mkdir(exist_ok=True)

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


# ============== FISH AUDIO TTS ==============
def generate_voice(text: str, out_path: Path) -> bool:
    """Génère un vocal .ogg (opus) à partir du texte via Fish Audio. Retourne True si succès."""
    if not FISH_TTS_ENABLED or not FISH_API_KEY or FISH_API_KEY == "TA_CLE_FISH_AUDIO_ICI":
        return False

    # Nettoie le texte des balises Markdown pour une voix plus naturelle
    clean_text = (
        text.replace("*", "").replace("_", "").replace("`", "").replace("~", "")
    )
    mp3_path = out_path.with_suffix(".mp3")

    try:
        payload = {"text": clean_text, "format": "mp3"}
        if FISH_VOICE_REFERENCE_ID:
            payload["reference_id"] = FISH_VOICE_REFERENCE_ID

        resp = requests.post(
            "https://api.fish.audio/v1/tts",
            headers={
                "Authorization": f"Bearer {FISH_API_KEY}",
                "Content-Type": "application/json",
                "model": "s2.1-pro",
            },
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        with open(mp3_path, "wb") as f:
            f.write(resp.content)

        # Convertit en .ogg opus pour que Telegram l'affiche comme un vrai vocal
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(mp3_path), "-c:a", "libopus", str(out_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        mp3_path.unlink(missing_ok=True)
        return out_path.exists()
    except Exception as e:
        logger.warning(f"Échec génération vocale Fish Audio : {e}")
        return False


async def reply_with_voice(update_or_bot, chat_id, text, context, reply_markup=None):
    """Envoie le texte formaté PUIS le vocal correspondant (si activé)."""
    await context.bot.send_message(
        chat_id, text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup
    )
    ogg_path = TMP_DIR / f"voice_{chat_id}_{abs(hash(text)) % 100000}.ogg"
    if generate_voice(text, ogg_path):
        try:
            with open(ogg_path, "rb") as f:
                await context.bot.send_voice(chat_id, InputFile(f))
        finally:
            ogg_path.unlink(missing_ok=True)


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


def welcome_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📜 Règlement", callback_data="show_rules")],
            [InlineKeyboardButton("👋 Se présenter", callback_data="present_self")],
        ]
    )


# ============== HANDLERS PRINCIPAUX ==============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_owner = user.id == OWNER_ID

    text = f"✨👋 *Bonjour {user.first_name} !* 👋✨\n\n🤖 Bienvenue sur le *bot officiel de la famille Ziox*."
    if is_owner:
        text += "\n\n🔑 _Tu es reconnu comme_ *owner*."

    # Envoie la photo de profil du bot en en-tête
    try:
        bot_photos = await context.bot.get_user_profile_photos(context.bot.id, limit=1)
        if bot_photos.total_count > 0:
            await update.message.reply_photo(
                bot_photos.photos[0][-1].file_id,
                caption=text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu_keyboard(is_owner),
            )
        else:
            raise ValueError("no photo")
    except Exception:
        await update.message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard(is_owner)
        )

    # Vocal de bienvenue
    ogg_path = TMP_DIR / f"start_{user.id}.ogg"
    voice_text = f"Bonjour {user.first_name}, bienvenue sur le bot officiel de la famille Ziox."
    if generate_voice(voice_text, ogg_path):
        try:
            with open(ogg_path, "rb") as f:
                await update.message.reply_voice(InputFile(f))
        finally:
            ogg_path.unlink(missing_ok=True)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    is_owner = user.id == OWNER_ID

    if query.data == "about":
        await query.edit_message_caption(
            caption="🤖✨ *À propos*\n\nCe bot répond automatiquement quand le owner est absent, "
            "reconnaît la famille Ziox via mot de passe, et peut te parler avec une vraie voix IA 🎙️.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard(),
        ) if query.message.caption else await query.edit_message_text(
            "🤖✨ *À propos*\n\nCe bot répond automatiquement quand le owner est absent, "
            "reconnaît la famille Ziox via mot de passe, et peut te parler avec une vraie voix IA 🎙️.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard(),
        )

    elif query.data == "leave_message":
        txt = "✍️ *Envoie simplement ton message ici*, il sera transmis au owner. 📨"
        if query.message.caption:
            await query.edit_message_caption(caption=txt, parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())
        else:
            await query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())

    elif query.data == "back_menu":
        txt = "📋 *Menu principal* ✨"
        if query.message.caption:
            await query.edit_message_caption(caption=txt, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard(is_owner))
        else:
            await query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard(is_owner))

    elif query.data == "toggle_absence" and is_owner:
        data["absence_mode"] = not data["absence_mode"]
        save_data(data)
        status = "activé 🔴" if data["absence_mode"] else "désactivé 🟢"
        await query.edit_message_caption(
            caption=f"✅ Mode absence *{status}*.", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard(is_owner)
        ) if query.message.caption else await query.edit_message_text(
            f"✅ Mode absence *{status}*.", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard(is_owner)
        )

    elif query.data == "list_family" and is_owner:
        members = data["family_members"]
        if not members:
            txt = "👪 Aucun membre de la famille Ziox reconnu pour l'instant."
        else:
            txt = "👪 *Famille Ziox reconnue :*\n\n" + "\n".join(
                f"• {v.get('name', 'Inconnu')} — `{k}`" for k, v in members.items()
            )
        if query.message.caption:
            await query.edit_message_caption(caption=txt, parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())
        else:
            await query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())

    elif query.data == "show_rules":
        await query.message.reply_text(
            "📜 *Règlement de la famille Ziox*\n\n"
            "1️⃣ Respect entre tous les membres 🤝\n"
            "2️⃣ Pas de spam 🚫\n"
            "3️⃣ Bonne ambiance obligatoire 🎉",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif query.data == "present_self":
        await query.message.reply_text(
            "👋 *Vas-y, présente-toi !* Ton prénom, ce que tu aimes, etc. On a hâte de te lire ✨",
            parse_mode=ParseMode.MARKDOWN,
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text or ""
    uid = str(user.id)
    is_owner = user.id == OWNER_ID
    chat_id = update.effective_chat.id

    if is_owner:
        await update.message.reply_text(
            "✅ *Reçu* (tu es le owner). 🔑", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard(True)
        )
        return

    if text.strip() == FAMILY_PASSWORD:
        if uid not in data["family_members"]:
            data["family_members"][uid] = {"name": user.full_name, "username": user.username}
            save_data(data)
        await reply_with_voice(
            update, chat_id,
            "✅🎉 *Mot de passe reconnu !* Tu fais maintenant partie de la famille Ziox reconnue par ce bot. 👪✨",
            context,
        )
        try:
            await context.bot.send_message(
                OWNER_ID, f"👪 Nouveau membre famille Ziox reconnu : {user.full_name} (@{user.username}, id {uid})"
            )
        except Exception as e:
            logger.warning(e)
        return

    is_family = uid in data["family_members"]

    if data["absence_mode"]:
        if is_family:
            await reply_with_voice(update, chat_id, ABSENCE_MESSAGE_FAMILY, context)
            try:
                await context.bot.send_message(
                    OWNER_ID, f"📩 [ABSENCE] Message famille Ziox — {user.full_name} :\n{text}"
                )
            except Exception as e:
                logger.warning(e)
        return  # silence pour les non-famille

    await reply_with_voice(
        update, chat_id,
        "📩✨ *Message bien reçu !* Il sera transmis au owner. Merci ! 🙏",
        context, reply_markup=main_menu_keyboard(False),
    )
    try:
        await context.bot.send_message(
            OWNER_ID, f"💬 Message de {user.full_name} (@{user.username}, id {uid}) :\n{text}"
        )
    except Exception as e:
        logger.warning(e)


async def absence_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    data["absence_mode"] = not data["absence_mode"]
    save_data(data)
    status = "activé 🔴" if data["absence_mode"] else "désactivé 🟢"
    await update.message.reply_text(f"✅ Mode absence *{status}*.", parse_mode=ParseMode.MARKDOWN)


# ============== WELCOME / GOODBYE (GROUPE) ==============
async def track_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if result is None:
        return
    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    member = result.new_chat_member.user
    chat_id = result.chat.id

    became_member = old_status in (
        ChatMemberStatus.LEFT, ChatMemberStatus.BANNED, ChatMemberStatus.RESTRICTED,
    ) and new_status == ChatMemberStatus.MEMBER

    left_chat = old_status == ChatMemberStatus.MEMBER and new_status in (
        ChatMemberStatus.LEFT, ChatMemberStatus.BANNED,
    )

    if became_member:
        await context.bot.send_message(
            chat_id,
            WELCOME_MESSAGE.format(name=member.full_name),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=welcome_keyboard(),
        )
    elif left_chat:
        await context.bot.send_message(
            chat_id, GOODBYE_MESSAGE.format(name=member.full_name), parse_mode=ParseMode.MARKDOWN
        )


def main():
    if OWNER_ID == 123456789:
        logger.warning("⚠️ OWNER_ID non configuré !")
    if FISH_API_KEY == "TA_CLE_FISH_AUDIO_ICI":
        logger.warning("⚠️ FISH_API_KEY non configurée — les réponses vocales seront désactivées.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("absence", absence_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(ChatMemberHandler(track_chat_members, ChatMemberHandler.CHAT_MEMBER))

    logger.info("Bot démarré...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
