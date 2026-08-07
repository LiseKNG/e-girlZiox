#!/usr/bin/env python3
"""
Ziox Bot v3 - Bot Telegram complet pour la famille Ziox
Fonctionnalités :
- Menu esthétique avec thème de boutons (emoji) personnalisable
- /welcome /setwelcome : activer/configurer le message de bienvenue (texte, image, pop-up)
- /goodbye /setgoodbye : pareil pour le message d'au revoir
- /profile /setprofile : profil des membres famille (photo, pays, bio)
- Reconnaissance famille Ziox via mot de passe
- Mode absence (owner)
- Voix IA Fish Audio
- Membres famille = permissions limitées + peuvent demander une commande à l'owner
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

OWNER_ID = 123456789  # <-- REMPLACE par ton ID Telegram (via @userinfobot)

FAMILY_PASSWORD = "ZIOX2026"

FISH_API_KEY = "TA_CLE_FISH_AUDIO_ICI"
FISH_VOICE_REFERENCE_ID = ""
FISH_TTS_ENABLED = True

DEFAULT_WELCOME_TEXT = (
    "🎉 *Bienvenue {name} dans la famille Ziox !* 👪\n\n"
    "On est ravis de t'accueillir parmi nous. ✨"
)
DEFAULT_GOODBYE_TEXT = (
    "😢 *{name} nous a quitté...*\n\n"
    "On espère te revoir bientôt parmi la famille Ziox. 👋"
)
ABSENCE_MESSAGE_FAMILY = (
    "👋 *Salut !* Ici le bot de la famille Ziox.\n\n"
    "🚫 Le owner est actuellement *absent*, ton message a bien été reçu "
    "et lui sera transmis dès son retour. 🙏"
)

AVAILABLE_THEMES = {
    "blue": "🔵",
    "green": "🟢",
    "red": "🔴",
    "purple": "🟣",
    "orange": "🟠",
    "yellow": "🟡",
    "black": "⚫",
    "white": "⚪",
}

DATA_FILE = Path(__file__).parent / "ziox_data.json"
TMP_DIR = Path(__file__).parent / "tmp_audio"
TMP_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# ============== STOCKAGE ==============
def default_data():
    return {
        "absence_mode": False,
        "family_members": {},  # uid -> {name, username, profile:{image_file_id,country,bio}, custom_commands:[]}
        "welcome": {"enabled": True, "text": DEFAULT_WELCOME_TEXT, "image_file_id": None, "popup_text": None},
        "goodbye": {"enabled": True, "text": DEFAULT_GOODBYE_TEXT, "image_file_id": None, "popup_text": None},
        "button_theme": "blue",
        "command_requests": [],  # {id, uid, name, text, status}
    }


def load_data():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
            base = default_data()
            base.update(loaded)
            return base
    return default_data()


def save_data(d):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


data = load_data()


def theme_emoji():
    return AVAILABLE_THEMES.get(data.get("button_theme", "blue"), "🔵")


def btn(label, callback_data):
    """Bouton avec le thème emoji appliqué devant le texte."""
    return InlineKeyboardButton(f"{theme_emoji()} {label}", callback_data=callback_data)


# ============== FISH AUDIO TTS ==============
def generate_voice(text: str, out_path: Path) -> bool:
    if not FISH_TTS_ENABLED or not FISH_API_KEY or FISH_API_KEY == "TA_CLE_FISH_AUDIO_ICI":
        return False
    clean_text = text.replace("*", "").replace("_", "").replace("`", "").replace("~", "")
    mp3_path = out_path.with_suffix(".mp3")
    try:
        payload = {"text": clean_text, "format": "mp3"}
        if FISH_VOICE_REFERENCE_ID:
            payload["reference_id"] = FISH_VOICE_REFERENCE_ID
        resp = requests.post(
            "https://api.fish.audio/v1/tts",
            headers={"Authorization": f"Bearer {FISH_API_KEY}", "Content-Type": "application/json", "model": "s2.1-pro"},
            json=payload, timeout=30,
        )
        resp.raise_for_status()
        with open(mp3_path, "wb") as f:
            f.write(resp.content)
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(mp3_path), "-c:a", "libopus", str(out_path)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        mp3_path.unlink(missing_ok=True)
        return out_path.exists()
    except Exception as e:
        logger.warning(f"Échec vocal Fish Audio : {e}")
        return False


async def send_voice_for(context, chat_id, text):
    ogg_path = TMP_DIR / f"voice_{chat_id}_{abs(hash(text)) % 100000}.ogg"
    if generate_voice(text, ogg_path):
        try:
            with open(ogg_path, "rb") as f:
                await context.bot.send_voice(chat_id, InputFile(f))
        finally:
            ogg_path.unlink(missing_ok=True)


async def reply_with_voice(context, chat_id, text, reply_markup=None):
    await context.bot.send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    await send_voice_for(context, chat_id, text)


# ============== HELPERS PERMISSIONS ==============
def is_owner(user_id):
    return user_id == OWNER_ID


def is_family(user_id):
    return str(user_id) in data["family_members"]


def get_profile(uid):
    return data["family_members"].get(str(uid), {}).get("profile", {"image_file_id": None, "country": None, "bio": None})


# ============== MENUS ==============
def owner_main_menu():
    rows = [
        [btn("👋 Welcome", "menu_welcome"), btn("🚪 Goodbye", "menu_goodbye")],
        [btn("🌙 Mode absence", "toggle_absence")],
        [btn("👪 Famille Ziox", "list_family"), btn("🙋 Demandes", "list_requests")],
        [btn("🙎 Mon profil", "profile_view_self")],
        [btn("🎨 Thème boutons", "menu_theme")],
    ]
    return InlineKeyboardMarkup(rows)


def family_main_menu():
    rows = [
        [btn("🙎 Mon profil", "profile_view_self"), btn("✏️ Modifier profil", "menu_setprofile")],
        [btn("🙋 Demander une commande", "request_command")],
    ]
    return InlineKeyboardMarkup(rows)


def back_keyboard(to="back_menu"):
    return InlineKeyboardMarkup([[btn("⬅️ Retour", to)]])


def welcome_menu_keyboard():
    status = "🟢 Désactiver" if data["welcome"]["enabled"] else "🔴 Activer"
    rows = [
        [btn(status, "welcome_toggle")],
        [btn("⚙️ Configurer", "menu_setwelcome")],
        [btn("⬅️ Retour", "back_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def goodbye_menu_keyboard():
    status = "🟢 Désactiver" if data["goodbye"]["enabled"] else "🔴 Activer"
    rows = [
        [btn(status, "goodbye_toggle")],
        [btn("⚙️ Configurer", "menu_setgoodbye")],
        [btn("⬅️ Retour", "back_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def setwelcome_keyboard():
    rows = [
        [btn("📝 Message par défaut", "setwelcome_default")],
        [btn("✍️ Message personnalisé", "setwelcome_customtext")],
        [btn("🖼️ Ajouter une image", "setwelcome_image")],
        [btn("🗑️ Retirer l'image", "setwelcome_removeimage")],
        [btn("💬 Message pop-up", "setwelcome_popup")],
        [btn("🗑️ Retirer le pop-up", "setwelcome_removepopup")],
        [btn("⬅️ Retour", "menu_welcome")],
    ]
    return InlineKeyboardMarkup(rows)


def setgoodbye_keyboard():
    rows = [
        [btn("📝 Message par défaut", "setgoodbye_default")],
        [btn("✍️ Message personnalisé", "setgoodbye_customtext")],
        [btn("🖼️ Ajouter une image", "setgoodbye_image")],
        [btn("🗑️ Retirer l'image", "setgoodbye_removeimage")],
        [btn("💬 Message pop-up", "setgoodbye_popup")],
        [btn("🗑️ Retirer le pop-up", "setgoodbye_removepopup")],
        [btn("⬅️ Retour", "menu_goodbye")],
    ]
    return InlineKeyboardMarkup(rows)


def setprofile_keyboard():
    rows = [
        [btn("🖼️ Changer ma photo", "setprofile_image")],
        [btn("🌍 Définir mon pays", "setprofile_country")],
        [btn("📝 Modifier ma bio", "setprofile_bio")],
        [btn("⬅️ Retour", "back_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def theme_menu_keyboard():
    rows = []
    row = []
    for key, emoji in AVAILABLE_THEMES.items():
        row.append(InlineKeyboardButton(f"{emoji}", callback_data=f"settheme_{key}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([btn("⬅️ Retour", "back_menu")])
    return InlineKeyboardMarkup(rows)


def popup_button(popup_text, prefix):
    return InlineKeyboardButton("🎉 Voir le mot spécial", callback_data=f"{prefix}_showpopup")


# ============== ENVOI D'UN MESSAGE (texte + éventuelle image + éventuel bouton pop-up) ==============
async def send_configured_message(context, chat_id, config, name):
    text = config["text"].format(name=name)
    kb = None
    if config.get("popup_text"):
        prefix = "welcomepopup" if config is data["welcome"] else "goodbyepopup"
        kb = InlineKeyboardMarkup([[popup_button(config["popup_text"], prefix)]])
    if config.get("image_file_id"):
        await context.bot.send_photo(chat_id, config["image_file_id"], caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


# ============== COMMANDES ==============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = f"✨👋 *Bonjour {user.first_name} !* ✨\n\n🤖 Bienvenue sur le *bot officiel de la famille Ziox*."
    if is_owner(user.id):
        text += "\n\n🔑 _Tu es reconnu comme_ *owner*."
        menu = owner_main_menu()
    elif is_family(user.id):
        text += "\n\n👪 _Tu fais partie de la famille Ziox._"
        menu = family_main_menu()
    else:
        text += "\n\n🔐 _Tape le mot de passe famille pour débloquer plus d'options._"
        menu = None
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=menu)


async def welcome_command(update, context):
    if not is_owner(update.effective_user.id):
        return
    await update.message.reply_text("👋 *Gestion du Welcome*", parse_mode=ParseMode.MARKDOWN, reply_markup=welcome_menu_keyboard())


async def setwelcome_command(update, context):
    if not is_owner(update.effective_user.id):
        return
    await update.message.reply_text("⚙️ *Configuration du Welcome*", parse_mode=ParseMode.MARKDOWN, reply_markup=setwelcome_keyboard())


async def goodbye_command(update, context):
    if not is_owner(update.effective_user.id):
        return
    await update.message.reply_text("🚪 *Gestion du Goodbye*", parse_mode=ParseMode.MARKDOWN, reply_markup=goodbye_menu_keyboard())


async def setgoodbye_command(update, context):
    if not is_owner(update.effective_user.id):
        return
    await update.message.reply_text("⚙️ *Configuration du Goodbye*", parse_mode=ParseMode.MARKDOWN, reply_markup=setgoodbye_keyboard())


async def profile_command(update, context):
    user = update.effective_user
    if not is_owner(user.id) and not is_family(user.id):
        await update.message.reply_text(
            "🔐 Tu dois d'abord faire partie de la famille Ziox (tape le mot de passe) pour voir un profil."
        )
        return
    await show_profile(update.message, user)


async def setprofile_command(update, context):
    user = update.effective_user
    if not is_owner(user.id) and not is_family(user.id):
        await update.message.reply_text("🔐 Tu dois d'abord faire partie de la famille Ziox pour configurer un profil.")
        return
    await update.message.reply_text("✏️ *Configuration de ton profil*", parse_mode=ParseMode.MARKDOWN, reply_markup=setprofile_keyboard())


async def theme_command(update, context):
    if not is_owner(update.effective_user.id):
        return
    await update.message.reply_text("🎨 *Choisis un thème de boutons*", parse_mode=ParseMode.MARKDOWN, reply_markup=theme_menu_keyboard())


async def absence_command(update, context):
    if not is_owner(update.effective_user.id):
        return
    data["absence_mode"] = not data["absence_mode"]
    save_data(data)
    status = "activé 🔴" if data["absence_mode"] else "désactivé 🟢"
    await update.message.reply_text(f"✅ Mode absence *{status}*.", parse_mode=ParseMode.MARKDOWN)


async def show_profile(message_or_query_msg, user, edit=False):
    profile = get_profile(user.id)
    is_owner_u = is_owner(user.id)
    caption = (
        f"🙎 *Profil de {user.full_name}*\n\n"
        f"🌍 Pays : {profile.get('country') or '_non renseigné_'}\n"
        f"📝 Bio : {profile.get('bio') or '_non renseignée_'}\n"
        f"👑 Rôle : {'Owner' if is_owner_u else 'Famille Ziox'}"
    )
    kb = InlineKeyboardMarkup([[btn("✏️ Modifier", "menu_setprofile")], [btn("⬅️ Retour", "back_menu")]])
    if profile.get("image_file_id"):
        await message_or_query_msg.reply_photo(profile["image_file_id"], caption=caption, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    else:
        await message_or_query_msg.reply_text(caption, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


# ============== CALLBACKS (boutons) ==============
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    d = query.data
    owner_u = is_owner(user.id)
    family_u = is_family(user.id)

    # --- Pop-up spéciaux (accessibles à tous, montrent une alerte) ---
    if d == "welcomepopup_showpopup":
        await query.answer(text=data["welcome"].get("popup_text") or "🎉", show_alert=True)
        return
    if d == "goodbyepopup_showpopup":
        await query.answer(text=data["goodbye"].get("popup_text") or "👋", show_alert=True)
        return

    await query.answer()

    async def edit(text, markup=None):
        try:
            if query.message.photo:
                await query.edit_message_caption(caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
            else:
                await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
        except Exception:
            await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

    # ---- Menu retour ----
    if d == "back_menu":
        if owner_u:
            await edit("📋 *Menu principal* ✨", owner_main_menu())
        elif family_u:
            await edit("📋 *Menu* ✨", family_main_menu())
        return

    # ---- OWNER ONLY ----
    if not owner_u and d in (
        "menu_welcome", "menu_goodbye", "toggle_absence", "list_family", "list_requests", "menu_theme",
        "welcome_toggle", "goodbye_toggle", "menu_setwelcome", "menu_setgoodbye",
    ) or (not owner_u and d.startswith(("setwelcome_", "setgoodbye_", "settheme_"))):
        await query.answer("🚫 Réservé au owner.", show_alert=True)
        return

    if d == "menu_welcome":
        await edit("👋 *Gestion du Welcome*", welcome_menu_keyboard())
    elif d == "menu_goodbye":
        await edit("🚪 *Gestion du Goodbye*", goodbye_menu_keyboard())
    elif d == "welcome_toggle":
        data["welcome"]["enabled"] = not data["welcome"]["enabled"]
        save_data(data)
        await edit("👋 *Gestion du Welcome*", welcome_menu_keyboard())
    elif d == "goodbye_toggle":
        data["goodbye"]["enabled"] = not data["goodbye"]["enabled"]
        save_data(data)
        await edit("🚪 *Gestion du Goodbye*", goodbye_menu_keyboard())
    elif d == "menu_setwelcome":
        await edit("⚙️ *Configuration du Welcome*", setwelcome_keyboard())
    elif d == "menu_setgoodbye":
        await edit("⚙️ *Configuration du Goodbye*", setgoodbye_keyboard())
    elif d == "setwelcome_default":
        data["welcome"]["text"] = DEFAULT_WELCOME_TEXT
        save_data(data)
        await edit("✅ Message welcome remis par défaut.", setwelcome_keyboard())
    elif d == "setgoodbye_default":
        data["goodbye"]["text"] = DEFAULT_GOODBYE_TEXT
        save_data(data)
        await edit("✅ Message goodbye remis par défaut.", setgoodbye_keyboard())
    elif d == "setwelcome_customtext":
        context.user_data["awaiting"] = "setwelcome_text"
        await edit("✍️ Envoie-moi le nouveau texte de welcome.\nUtilise `{name}` pour insérer le prénom.", back_keyboard("menu_setwelcome"))
    elif d == "setgoodbye_customtext":
        context.user_data["awaiting"] = "setgoodbye_text"
        await edit("✍️ Envoie-moi le nouveau texte de goodbye.\nUtilise `{name}` pour insérer le prénom.", back_keyboard("menu_setgoodbye"))
    elif d == "setwelcome_image":
        context.user_data["awaiting"] = "setwelcome_image"
        await edit("🖼️ Envoie-moi maintenant l'image à attacher au welcome.", back_keyboard("menu_setwelcome"))
    elif d == "setgoodbye_image":
        context.user_data["awaiting"] = "setgoodbye_image"
        await edit("🖼️ Envoie-moi maintenant l'image à attacher au goodbye.", back_keyboard("menu_setgoodbye"))
    elif d == "setwelcome_removeimage":
        data["welcome"]["image_file_id"] = None
        save_data(data)
        await edit("✅ Image welcome retirée.", setwelcome_keyboard())
    elif d == "setgoodbye_removeimage":
        data["goodbye"]["image_file_id"] = None
        save_data(data)
        await edit("✅ Image goodbye retirée.", setgoodbye_keyboard())
    elif d == "setwelcome_popup":
        context.user_data["awaiting"] = "setwelcome_popup"
        await edit("💬 Envoie le texte du pop-up welcome (affiché quand on clique sur le bouton).", back_keyboard("menu_setwelcome"))
    elif d == "setgoodbye_popup":
        context.user_data["awaiting"] = "setgoodbye_popup"
        await edit("💬 Envoie le texte du pop-up goodbye.", back_keyboard("menu_setgoodbye"))
    elif d == "setwelcome_removepopup":
        data["welcome"]["popup_text"] = None
        save_data(data)
        await edit("✅ Pop-up welcome retiré.", setwelcome_keyboard())
    elif d == "setgoodbye_removepopup":
        data["goodbye"]["popup_text"] = None
        save_data(data)
        await edit("✅ Pop-up goodbye retiré.", setgoodbye_keyboard())
    elif d == "toggle_absence":
        data["absence_mode"] = not data["absence_mode"]
        save_data(data)
        status = "activé 🔴" if data["absence_mode"] else "désactivé 🟢"
        await edit(f"✅ Mode absence *{status}*.", owner_main_menu())
    elif d == "list_family":
        members = data["family_members"]
        txt = "👪 Aucun membre pour l'instant." if not members else "👪 *Famille Ziox :*\n\n" + "\n".join(
            f"• {v.get('name','?')} — `{k}`" for k, v in members.items()
        )
        await edit(txt, back_keyboard("back_menu"))
    elif d == "list_requests":
        reqs = [r for r in data["command_requests"] if r["status"] == "pending"]
        if not reqs:
            await edit("🙋 Aucune demande en attente.", back_keyboard("back_menu"))
        else:
            for r in reqs:
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Approuver", callback_data=f"reqapprove_{r['id']}"),
                    InlineKeyboardButton("❌ Refuser", callback_data=f"reqreject_{r['id']}"),
                ]])
                await query.message.reply_text(f"🙋 *{r['name']}* demande :\n_{r['text']}_", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    elif d.startswith("reqapprove_") or d.startswith("reqreject_"):
        rid = d.split("_", 1)[1]
        for r in data["command_requests"]:
            if r["id"] == rid:
                r["status"] = "approved" if d.startswith("reqapprove_") else "rejected"
                save_data(data)
                try:
                    msg = "✅ Ta demande a été approuvée ! L'owner va y donner suite." if r["status"] == "approved" else "❌ Ta demande a été refusée."
                    await context.bot.send_message(int(r["uid"]), msg)
                except Exception:
                    pass
        await query.edit_message_text("✅ Traité.")
    elif d == "menu_theme":
        await edit("🎨 *Choisis un thème de boutons*", theme_menu_keyboard())
    elif d.startswith("settheme_"):
        key = d.split("_", 1)[1]
        if key in AVAILABLE_THEMES:
            data["button_theme"] = key
            save_data(data)
        await edit(f"✅ Thème changé : {theme_emoji()}", owner_main_menu())

    # ---- Profil (owner + famille) ----
    elif d == "profile_view_self":
        await show_profile(query.message, user)
    elif d == "menu_setprofile":
        if owner_u or family_u:
            await edit("✏️ *Configuration de ton profil*", setprofile_keyboard())
        else:
            await query.answer("🔐 Réservé à la famille Ziox.", show_alert=True)
    elif d == "setprofile_image":
        context.user_data["awaiting"] = "setprofile_image"
        await edit("🖼️ Envoie-moi ta photo de profil.", back_keyboard("back_menu"))
    elif d == "setprofile_country":
        context.user_data["awaiting"] = "setprofile_country"
        await edit("🌍 Envoie-moi ton pays.", back_keyboard("back_menu"))
    elif d == "setprofile_bio":
        context.user_data["awaiting"] = "setprofile_bio"
        await edit("📝 Envoie-moi ta nouvelle bio.", back_keyboard("back_menu"))
    elif d == "request_command":
        context.user_data["awaiting"] = "request_command"
        await edit("🙋 Décris la commande que tu aimerais avoir (ou une commande existante à débloquer).", back_keyboard("back_menu"))


# ============== MESSAGES TEXTE ==============
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text or ""
    uid = str(user.id)
    chat_id = update.effective_chat.id
    awaiting = context.user_data.get("awaiting")

    # ---- Étapes de configuration en cours ----
    if awaiting:
        if awaiting == "setwelcome_text" and is_owner(user.id):
            data["welcome"]["text"] = text
            save_data(data)
            await update.message.reply_text("✅ Nouveau texte welcome enregistré.", reply_markup=setwelcome_keyboard())
        elif awaiting == "setgoodbye_text" and is_owner(user.id):
            data["goodbye"]["text"] = text
            save_data(data)
            await update.message.reply_text("✅ Nouveau texte goodbye enregistré.", reply_markup=setgoodbye_keyboard())
        elif awaiting == "setwelcome_popup" and is_owner(user.id):
            data["welcome"]["popup_text"] = text
            save_data(data)
            await update.message.reply_text("✅ Pop-up welcome enregistré.", reply_markup=setwelcome_keyboard())
        elif awaiting == "setgoodbye_popup" and is_owner(user.id):
            data["goodbye"]["popup_text"] = text
            save_data(data)
            await update.message.reply_text("✅ Pop-up goodbye enregistré.", reply_markup=setgoodbye_keyboard())
        elif awaiting == "setprofile_country" and (is_owner(user.id) or is_family(user.id)):
            data["family_members"].setdefault(uid, {"name": user.full_name, "username": user.username, "profile": {}})
            data["family_members"][uid].setdefault("profile", {})
            data["family_members"][uid]["profile"]["country"] = text
            save_data(data)
            await update.message.reply_text("✅ Pays enregistré.", reply_markup=setprofile_keyboard())
        elif awaiting == "setprofile_bio" and (is_owner(user.id) or is_family(user.id)):
            data["family_members"].setdefault(uid, {"name": user.full_name, "username": user.username, "profile": {}})
            data["family_members"][uid].setdefault("profile", {})
            data["family_members"][uid]["profile"]["bio"] = text
            save_data(data)
            await update.message.reply_text("✅ Bio enregistrée.", reply_markup=setprofile_keyboard())
        elif awaiting == "request_command":
            rid = f"{uid}_{len(data['command_requests'])}"
            data["command_requests"].append({"id": rid, "uid": uid, "name": user.full_name, "text": text, "status": "pending"})
            save_data(data)
            await update.message.reply_text("✅ Ta demande a été envoyée au owner ! 🙏")
            try:
                await context.bot.send_message(
                    OWNER_ID,
                    f"🙋 Nouvelle demande de {user.full_name} :\n_{text}_\n\nUtilise /welcome ou le menu pour la traiter.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
        context.user_data["awaiting"] = None
        return

    is_owner_u = is_owner(user.id)

    if is_owner_u:
        await update.message.reply_text("✅ *Reçu* (tu es le owner). 🔑", parse_mode=ParseMode.MARKDOWN, reply_markup=owner_main_menu())
        return

    if text.strip() == FAMILY_PASSWORD:
        if uid not in data["family_members"]:
            data["family_members"][uid] = {"name": user.full_name, "username": user.username, "profile": {}}
            save_data(data)
        await reply_with_voice(
            context, chat_id,
            "✅🎉 *Mot de passe reconnu !* Tu fais maintenant partie de la famille Ziox. 👪✨",
            reply_markup=family_main_menu(),
        )
        try:
            await context.bot.send_message(OWNER_ID, f"👪 Nouveau membre famille Ziox : {user.full_name} (id {uid})")
        except Exception:
            pass
        return

    is_family_u = uid in data["family_members"]

    if data["absence_mode"]:
        if is_family_u:
            await reply_with_voice(context, chat_id, ABSENCE_MESSAGE_FAMILY)
            try:
                await context.bot.send_message(OWNER_ID, f"📩 [ABSENCE] {user.full_name} :\n{text}")
            except Exception:
                pass
        return

    await reply_with_voice(context, chat_id, "📩✨ *Message bien reçu !* Merci 🙏", reply_markup=family_main_menu() if is_family_u else None)
    try:
        await context.bot.send_message(OWNER_ID, f"💬 {user.full_name} (id {uid}) :\n{text}")
    except Exception:
        pass


# ============== PHOTOS (images pour welcome/goodbye/profil) ==============
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    awaiting = context.user_data.get("awaiting")
    if not awaiting:
        return
    user = update.effective_user
    file_id = update.message.photo[-1].file_id

    if awaiting == "setwelcome_image" and is_owner(user.id):
        data["welcome"]["image_file_id"] = file_id
        save_data(data)
        await update.message.reply_text("✅ Image welcome enregistrée.", reply_markup=setwelcome_keyboard())
    elif awaiting == "setgoodbye_image" and is_owner(user.id):
        data["goodbye"]["image_file_id"] = file_id
        save_data(data)
        await update.message.reply_text("✅ Image goodbye enregistrée.", reply_markup=setgoodbye_keyboard())
    elif awaiting == "setprofile_image" and (is_owner(user.id) or is_family(user.id)):
        uid = str(user.id)
        data["family_members"].setdefault(uid, {"name": user.full_name, "username": user.username, "profile": {}})
        data["family_members"][uid].setdefault("profile", {})
        data["family_members"][uid]["profile"]["image_file_id"] = file_id
        save_data(data)
        await update.message.reply_text("✅ Photo de profil enregistrée.", reply_markup=setprofile_keyboard())

    context.user_data["awaiting"] = None


# ============== WELCOME / GOODBYE (GROUPE) ==============
async def track_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if result is None:
        return
    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    member = result.new_chat_member.user
    chat_id = result.chat.id

    became_member = old_status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED, ChatMemberStatus.RESTRICTED) and new_status == ChatMemberStatus.MEMBER
    left_chat = old_status == ChatMemberStatus.MEMBER and new_status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED)

    if became_member and data["welcome"]["enabled"]:
        await send_configured_message(context, chat_id, data["welcome"], member.full_name)
    elif left_chat and data["goodbye"]["enabled"]:
        await send_configured_message(context, chat_id, data["goodbye"], member.full_name)


def main():
    if OWNER_ID == 123456789:
        logger.warning("⚠️ OWNER_ID non configuré !")
    if FISH_API_KEY == "TA_CLE_FISH_AUDIO_ICI":
        logger.warning("⚠️ FISH_API_KEY non configurée — vocal désactivé.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("welcome", welcome_command))
    app.add_handler(CommandHandler("setwelcome", setwelcome_command))
    app.add_handler(CommandHandler("goodbye", goodbye_command))
    app.add_handler(CommandHandler("setgoodbye", setgoodbye_command))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("setprofile", setprofile_command))
    app.add_handler(CommandHandler("theme", theme_command))
    app.add_handler(CommandHandler("absence", absence_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(ChatMemberHandler(track_chat_members, ChatMemberHandler.CHAT_MEMBER))

    logger.info("Bot démarré...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
