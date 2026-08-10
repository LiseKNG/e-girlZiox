#!/usr/bin/env python3
"""
Ziox Bot v6 - Bot Telegram complet pour la famille Ziox
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

import hashlib
import hmac
import json
import logging
import os
import subprocess
import threading
import urllib.parse
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template, request, send_from_directory
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    LabeledPrice,
    WebAppInfo,
)
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ChatMemberHandler,
    ChatBoostHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)

# ============== CONFIGURATION ==============
BOT_TOKEN = "8230994359:AAE5R_UYe3UYKuuLhCDa-rsp4wK1JwwGMa0"  # ⚠️ à régénérer via BotFather

OWNER_ID = 8914448661  # ton ID Telegram (owner principal)
SECOND_OWNER_USERNAME = "Dame_Zioxie"  # reconnu comme owner via son @username, sans besoin de son ID
SECOND_OWNER_ID = 8997141271  # ID de @MRS_ZIOXY, reçoit aussi les notifications privées

# --- "Notre histoire" (mini app profils de couple) ---
COUPLE_OWNER_ID = 8914448661   # ID Telegram autorisé à éditer le profil "owner"
COUPLE_PARTNER_ID = 8997141271  # ID Telegram autorisé à éditer le profil "partner"
UNLOCK_PRICE_STARS = 10000  # prix en étoiles pour qu'un visiteur voie les profils (lecture seule)

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

DEFAULT_BOOST_TEXT = "🚀✨ *Merci {mention} pour ton boost !* 💜\n\nTon soutien fait vraiment plaisir à toute la famille Ziox 🙏🎉"
DEFAULT_BOOST_POPUP = "Merci infiniment pour ton soutien à la famille Ziox ! 🚀💜"

RULES_TEXT = (
    "📜 *Règlement de la famille Ziox* 📜\n\n"
    "1️⃣ 🤝 Respect entre tous les membres, en toutes circonstances.\n"
    "2️⃣ 🚫 Pas de spam, pas de publicité non autorisée.\n"
    "3️⃣ 🔞 Pas de contenu inapproprié ou choquant.\n"
    "4️⃣ 😄 Bonne ambiance et bienveillance obligatoires.\n"
    "5️⃣ 🗣️ Les décisions des owners sont à respecter.\n\n"
    "Merci de faire vivre cette belle famille ! ✨👪"
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

COUPLE_DATA_FILE = Path(__file__).parent / "couple_data.json"
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

# URL publique de ce bot une fois déployé (ex: https://ton-projet.up.railway.app)
# <-- REMPLACE après ton premier déploiement Railway, sinon le bouton "Notre histoire" ne s'ouvrira pas.
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://e-girlziox-production.up.railway.app")
BOT_USERNAME = "TonBotZiox"  # <-- REMPLACE par le username de ton bot (sans @)

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
        "boost": {"text": DEFAULT_BOOST_TEXT, "image_file_id": None, "popup_text": DEFAULT_BOOST_POPUP},
        "rules_image_file_id": None,
        "unlocked_profiles": [],  # liste des IDs Telegram ayant payé les 10 000 ⭐ pour voir "Notre histoire"
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


# ============== DONNÉES "NOTRE HISTOIRE" (couple) ==============
def load_couple_data():
    if COUPLE_DATA_FILE.exists():
        with open(COUPLE_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"owner": {}, "partner": {}, "since_date": None}


def save_couple_data(cd):
    with open(COUPLE_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(cd, f, ensure_ascii=False, indent=2)


couple_data = load_couple_data()


# ============== SERVEUR WEB (mini app Telegram) ==============
flask_app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"), static_folder=str(STATIC_DIR))


def verify_telegram_init_data(init_data: str):
    """Vérifie officiellement (HMAC) que initData vient bien de Telegram, et retourne l'utilisateur.
    Voir la doc officielle Telegram Mini Apps 'Validating data received via the Mini App'.
    Retourne le dict utilisateur si valide, sinon None."""
    if not init_data:
        return None
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, strict_parsing=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed_hash, received_hash):
            return None
        user_raw = parsed.get("user")
        return json.loads(user_raw) if user_raw else None
    except Exception as e:
        logger.warning(f"Échec validation initData : {e}")
        return None


@flask_app.route("/")
def health():
    return "Ziox Bot en ligne ✅"


@flask_app.route("/couple")
def couple_page():
    return render_template("couple.html", unlock_price=UNLOCK_PRICE_STARS, bot_username=BOT_USERNAME)


@flask_app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@flask_app.route("/api/status")
def api_status():
    """Renvoie qui visite, si iel peut éditer/voir, et les données des profils (si autorisé)."""
    init_data = request.args.get("initData", "")
    user = verify_telegram_init_data(init_data)
    uid = user.get("id") if user else None

    is_owner_visitor = uid == COUPLE_OWNER_ID
    is_partner_visitor = uid == COUPLE_PARTNER_ID
    is_editor = is_owner_visitor or is_partner_visitor
    is_unlocked = uid is not None and uid in data.get("unlocked_profiles", [])
    can_view = is_editor or is_unlocked

    cd = load_couple_data()
    payload = {
        "authenticated": user is not None,
        "telegram_id": uid,
        "isEditor": is_editor,
        "editorRole": "owner" if is_owner_visitor else ("partner" if is_partner_visitor else None),
        "canView": can_view,
        "sinceDate": cd.get("since_date"),
    }
    if can_view:
        owner = cd.get("owner", {})
        partner = cd.get("partner", {})
        payload["profiles"] = {
            "owner": {**owner.get("info", {}), "photo": (f"/static/{owner['photo']}" if owner.get("photo") else None), "name": owner.get("name")},
            "partner": {**partner.get("info", {}), "photo": (f"/static/{partner['photo']}" if partner.get("photo") else None), "name": partner.get("name")},
        }
    return jsonify(payload)


@flask_app.route("/api/profile", methods=["POST"])
def api_save_profile():
    """Permet à l'un des deux owners de mettre à jour SON profil (jamais l'autre)."""
    body = request.get_json(force=True, silent=True) or {}
    init_data = body.get("initData", "")
    user = verify_telegram_init_data(init_data)
    uid = user.get("id") if user else None

    who = body.get("who")
    if who == "owner" and uid != COUPLE_OWNER_ID:
        return jsonify({"error": "unauthorized"}), 403
    if who == "partner" and uid != COUPLE_PARTNER_ID:
        return jsonify({"error": "unauthorized"}), 403
    if who not in ("owner", "partner"):
        return jsonify({"error": "invalid target"}), 400

    profile_fields = body.get("profile", {})
    allowed_keys = {"prenom", "nom", "telegram_id", "telephone", "pays", "bio"}
    clean = {k: str(v)[:200] for k, v in profile_fields.items() if k in allowed_keys}

    cd = load_couple_data()
    cd.setdefault(who, {})
    cd[who]["info"] = clean
    save_couple_data(cd)
    return jsonify({"ok": True})


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)


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


async def notify_owners(context, text, parse_mode=None):
    """Envoie une notification privée aux deux owners (silencieux si l'un d'eux n'a jamais démarré le bot)."""
    for oid in {OWNER_ID, SECOND_OWNER_ID}:
        try:
            await context.bot.send_message(oid, text, parse_mode=parse_mode)
        except Exception as e:
            logger.warning(f"Impossible de notifier {oid} : {e}")


# ============== HELPERS PERMISSIONS ==============
def is_owner(user_id, username=None):
    if user_id == OWNER_ID:
        return True
    if username and username.lstrip("@").lower() == SECOND_OWNER_USERNAME.lstrip("@").lower():
        return True
    return False


def is_owner_user(user):
    """Pratique : accepte l'objet telegram.User directement."""
    return is_owner(user.id, getattr(user, "username", None))


def is_family(user_id):
    return str(user_id) in data["family_members"]


def get_profile(uid):
    return data["family_members"].get(str(uid), {}).get("profile", {"image_file_id": None, "country": None, "bio": None})


# ============== MENUS ==============
def owner_main_menu():
    rows = [
        [btn("👋 Welcome", "menu_welcome"), btn("🚪 Goodbye", "menu_goodbye")],
        [btn("🚀 Boost", "menu_boost"), btn("📜 Rules", "menu_rules")],
        [btn("🌙 Mode absence", "toggle_absence")],
        [btn("👪 Famille Ziox", "list_family"), btn("🙋 Demandes", "list_requests")],
        [btn("🙎 Mon profil", "profile_view_self")],
        [btn("🎨 Thème boutons", "menu_theme"), btn("⭐ Étoiles", "menu_stars")],
        [InlineKeyboardButton(f"{theme_emoji()} 💞 Notre histoire", web_app=WebAppInfo(url=f"{WEBAPP_URL}/couple"))],
    ]
    return InlineKeyboardMarkup(rows)


def family_main_menu():
    rows = [
        [btn("🙎 Mon profil", "profile_view_self"), btn("✏️ Modifier profil", "menu_setprofile")],
        [btn("📜 Rules", "show_rules"), btn("⭐ Soutenir", "menu_stars")],
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


def setboost_keyboard():
    rows = [
        [btn("📝 Message par défaut", "setboost_default")],
        [btn("✍️ Message personnalisé", "setboost_customtext")],
        [btn("🖼️ Ajouter une image", "setboost_image")],
        [btn("🗑️ Retirer l'image", "setboost_removeimage")],
        [btn("💬 Message pop-up", "setboost_popup")],
        [btn("⬅️ Retour", "back_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def rules_menu_keyboard():
    rows = [
        [btn("🖼️ Ajouter/changer l'image", "setrules_image")],
        [btn("🗑️ Retirer l'image", "setrules_removeimage")],
        [btn("👀 Aperçu", "show_rules")],
        [btn("⬅️ Retour", "back_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def stars_menu_keyboard():
    rows = [
        [InlineKeyboardButton("☕ 10 ⭐", callback_data="stars_10"), InlineKeyboardButton("🎁 50 ⭐", callback_data="stars_50")],
        [InlineKeyboardButton("💎 100 ⭐", callback_data="stars_100")],
        [InlineKeyboardButton("🔁 Abonnement mensuel · 30 ⭐/mois", callback_data="stars_sub")],
        [InlineKeyboardButton(f"💞 Débloquer Notre Histoire · {UNLOCK_PRICE_STARS} ⭐", callback_data="stars_unlock")],
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
    if is_owner_user(user):
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
    if not is_owner_user(update.effective_user):
        return
    await update.message.reply_text("👋 *Gestion du Welcome*", parse_mode=ParseMode.MARKDOWN, reply_markup=welcome_menu_keyboard())


async def setwelcome_command(update, context):
    if not is_owner_user(update.effective_user):
        return
    await update.message.reply_text("⚙️ *Configuration du Welcome*", parse_mode=ParseMode.MARKDOWN, reply_markup=setwelcome_keyboard())


async def goodbye_command(update, context):
    if not is_owner_user(update.effective_user):
        return
    await update.message.reply_text("🚪 *Gestion du Goodbye*", parse_mode=ParseMode.MARKDOWN, reply_markup=goodbye_menu_keyboard())


async def setgoodbye_command(update, context):
    if not is_owner_user(update.effective_user):
        return
    await update.message.reply_text("⚙️ *Configuration du Goodbye*", parse_mode=ParseMode.MARKDOWN, reply_markup=setgoodbye_keyboard())


async def profile_command(update, context):
    user = update.effective_user
    if not is_owner_user(user) and not is_family(user.id):
        await update.message.reply_text(
            "🔐 Tu dois d'abord faire partie de la famille Ziox (tape le mot de passe) pour voir un profil."
        )
        return
    await show_profile(update.message, user)


async def setprofile_command(update, context):
    user = update.effective_user
    if not is_owner_user(user) and not is_family(user.id):
        await update.message.reply_text("🔐 Tu dois d'abord faire partie de la famille Ziox pour configurer un profil.")
        return
    await update.message.reply_text("✏️ *Configuration de ton profil*", parse_mode=ParseMode.MARKDOWN, reply_markup=setprofile_keyboard())


async def couple_command(update, context):
    """Ouvre la mini app 'Notre histoire' via un bouton Web App."""
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("💞 Ouvrir Notre histoire", web_app=WebAppInfo(url=f"{WEBAPP_URL}/couple"))]])
    await update.message.reply_text("💞 Clique pour ouvrir votre page à deux :", reply_markup=kb)


async def setcouplephoto_command(update, context):
    user = update.effective_user
    if not is_owner_user(user):
        await update.message.reply_text("🔐 Réservé aux owners (toi et ta moitié).")
        return
    context.user_data["awaiting"] = "couple_photo"
    await update.message.reply_text("🖼️ Envoie maintenant TA photo pour la page 'Notre histoire'.")


async def setsince_command(update, context):
    user = update.effective_user
    if not is_owner_user(user):
        return
    context.user_data["awaiting"] = "couple_since"
    await update.message.reply_text("📅 Envoie la date de votre début, au format AAAA-MM-JJ (ex: 2023-06-14).")


async def setboost_command(update, context):
    if not is_owner_user(update.effective_user):
        return
    await update.message.reply_text("🚀 *Configuration du message de Boost*", parse_mode=ParseMode.MARKDOWN, reply_markup=setboost_keyboard())


async def rules_command(update, context):
    await show_rules(update.message)


async def setrulesimage_command(update, context):
    if not is_owner_user(update.effective_user):
        return
    await update.message.reply_text("🖼️ *Gestion de l'image des règles*", parse_mode=ParseMode.MARKDOWN, reply_markup=rules_menu_keyboard())


async def show_rules(message_target):
    if data.get("rules_image_file_id"):
        await message_target.reply_photo(data["rules_image_file_id"], caption=RULES_TEXT, parse_mode=ParseMode.MARKDOWN)
    else:
        await message_target.reply_text(RULES_TEXT, parse_mode=ParseMode.MARKDOWN)


async def stars_command(update, context):
    await update.message.reply_text(
        "⭐ *Soutenir la famille Ziox*\n\nEnvoie des étoiles Telegram en cadeau, ou abonne-toi mensuellement ! 💜",
        parse_mode=ParseMode.MARKDOWN, reply_markup=stars_menu_keyboard(),
    )


async def theme_command(update, context):
    if not is_owner_user(update.effective_user):
        return
    await update.message.reply_text("🎨 *Choisis un thème de boutons*", parse_mode=ParseMode.MARKDOWN, reply_markup=theme_menu_keyboard())


async def absence_command(update, context):
    if not is_owner_user(update.effective_user):
        return
    data["absence_mode"] = not data["absence_mode"]
    save_data(data)
    status = "activé 🔴" if data["absence_mode"] else "désactivé 🟢"
    await update.message.reply_text(f"✅ Mode absence *{status}*.", parse_mode=ParseMode.MARKDOWN)


async def show_profile(message_or_query_msg, user, edit=False):
    profile = get_profile(user.id)
    is_owner_u = is_owner_user(user)
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
    owner_u = is_owner_user(user)
    family_u = is_family(user.id)

    # --- Pop-up spéciaux (accessibles à tous, montrent une alerte) ---
    if d == "welcomepopup_showpopup":
        await query.answer(text=data["welcome"].get("popup_text") or "🎉", show_alert=True)
        return
    if d == "goodbyepopup_showpopup":
        await query.answer(text=data["goodbye"].get("popup_text") or "👋", show_alert=True)
        return
    if d == "boostpopup_show":
        await query.answer(text=data["boost"].get("popup_text") or "🚀💜", show_alert=True)
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
        "welcome_toggle", "goodbye_toggle", "menu_setwelcome", "menu_setgoodbye", "menu_boost", "menu_rules",
    ) or (not owner_u and d.startswith(("setwelcome_", "setgoodbye_", "settheme_", "setboost_", "setrules_"))):
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

    elif d == "menu_boost":
        await edit("🚀 *Configuration du message de Boost*", setboost_keyboard())
    elif d == "setboost_default":
        data["boost"]["text"] = DEFAULT_BOOST_TEXT
        save_data(data)
        await edit("✅ Message boost remis par défaut.", setboost_keyboard())
    elif d == "setboost_customtext":
        context.user_data["awaiting"] = "setboost_text"
        await edit("✍️ Envoie le nouveau texte de remerciement boost.\nUtilise `{mention}` pour mentionner la personne.", back_keyboard("menu_boost"))
    elif d == "setboost_image":
        context.user_data["awaiting"] = "setboost_image"
        await edit("🖼️ Envoie-moi l'image à attacher au message de remerciement boost.", back_keyboard("menu_boost"))
    elif d == "setboost_removeimage":
        data["boost"]["image_file_id"] = None
        save_data(data)
        await edit("✅ Image boost retirée.", setboost_keyboard())
    elif d == "setboost_popup":
        context.user_data["awaiting"] = "setboost_popup"
        await edit("💬 Envoie le texte du pop-up affiché quand on clique sur le bouton de remerciement.", back_keyboard("menu_boost"))

    elif d == "menu_rules":
        await edit("📜 *Gestion des Rules*", rules_menu_keyboard())
    elif d == "setrules_image":
        context.user_data["awaiting"] = "setrules_image"
        await edit("🖼️ Envoie-moi l'image à attacher aux règles.", back_keyboard("menu_rules"))
    elif d == "setrules_removeimage":
        data["rules_image_file_id"] = None
        save_data(data)
        await edit("✅ Image des règles retirée.", rules_menu_keyboard())
    elif d == "show_rules":
        await show_rules(query.message)

    elif d == "menu_stars" or d == "stars_menu":
        await edit("⭐ *Soutenir la famille Ziox*", stars_menu_keyboard())
    elif d in ("stars_10", "stars_50", "stars_100"):
        amount = int(d.split("_")[1])
        await context.bot.send_invoice(
            chat_id=query.message.chat_id,
            title=f"Soutien Ziox · {amount} ⭐",
            description="Merci pour ton soutien à la famille Ziox ! 💜",
            payload=f"support_{amount}",
            currency="XTR",
            prices=[LabeledPrice(f"{amount} étoiles", amount)],
        )
    elif d == "stars_sub":
        await context.bot.send_invoice(
            chat_id=query.message.chat_id,
            title="Abonnement mensuel Ziox · 30 ⭐/mois",
            description="Soutien mensuel récurrent à la famille Ziox 💜",
            payload="subscription_30",
            currency="XTR",
            prices=[LabeledPrice("30 étoiles / mois", 30)],
            subscription_period=2592000,
        )
    elif d == "stars_unlock":
        await context.bot.send_invoice(
            chat_id=query.message.chat_id,
            title=f"Accès Notre Histoire · {UNLOCK_PRICE_STARS} ⭐",
            description="Débloque la lecture des profils de la famille Ziox (lecture seule, non modifiable). 💞",
            payload="unlock_profiles",
            currency="XTR",
            prices=[LabeledPrice(f"{UNLOCK_PRICE_STARS} étoiles", UNLOCK_PRICE_STARS)],
        )

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
        if awaiting == "setwelcome_text" and is_owner_user(user):
            data["welcome"]["text"] = text
            save_data(data)
            await update.message.reply_text("✅ Nouveau texte welcome enregistré.", reply_markup=setwelcome_keyboard())
        elif awaiting == "setgoodbye_text" and is_owner_user(user):
            data["goodbye"]["text"] = text
            save_data(data)
            await update.message.reply_text("✅ Nouveau texte goodbye enregistré.", reply_markup=setgoodbye_keyboard())
        elif awaiting == "setwelcome_popup" and is_owner_user(user):
            data["welcome"]["popup_text"] = text
            save_data(data)
            await update.message.reply_text("✅ Pop-up welcome enregistré.", reply_markup=setwelcome_keyboard())
        elif awaiting == "setgoodbye_popup" and is_owner_user(user):
            data["goodbye"]["popup_text"] = text
            save_data(data)
            await update.message.reply_text("✅ Pop-up goodbye enregistré.", reply_markup=setgoodbye_keyboard())
        elif awaiting == "setboost_text" and is_owner_user(user):
            data["boost"]["text"] = text
            save_data(data)
            await update.message.reply_text("✅ Nouveau texte boost enregistré.", reply_markup=setboost_keyboard())
        elif awaiting == "setboost_popup" and is_owner_user(user):
            data["boost"]["popup_text"] = text
            save_data(data)
            await update.message.reply_text("✅ Pop-up boost enregistré.", reply_markup=setboost_keyboard())
        elif awaiting == "couple_since" and is_owner_user(user):
            cd = load_couple_data()
            cd["since_date"] = text.strip()
            save_couple_data(cd)
            await update.message.reply_text(f"✅ Date enregistrée : {text.strip()} 💞")
        elif awaiting == "setprofile_country" and (is_owner_user(user) or is_family(user.id)):
            data["family_members"].setdefault(uid, {"name": user.full_name, "username": user.username, "profile": {}})
            data["family_members"][uid].setdefault("profile", {})
            data["family_members"][uid]["profile"]["country"] = text
            save_data(data)
            await update.message.reply_text("✅ Pays enregistré.", reply_markup=setprofile_keyboard())
        elif awaiting == "setprofile_bio" and (is_owner_user(user) or is_family(user.id)):
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
                await notify_owners(
                    context,
                    f"🙋 Nouvelle demande de {user.full_name} :\n_{text}_\n\nUtilise /welcome ou le menu pour la traiter.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
        context.user_data["awaiting"] = None
        return

    is_owner_u = is_owner_user(user)

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
            await notify_owners(context, f"👪 Nouveau membre famille Ziox : {user.full_name} (id {uid})")
        except Exception:
            pass
        return

    is_family_u = uid in data["family_members"]

    if data["absence_mode"]:
        if is_family_u:
            await reply_with_voice(context, chat_id, ABSENCE_MESSAGE_FAMILY)
            try:
                await notify_owners(context, f"📩 [ABSENCE] {user.full_name} :\n{text}")
            except Exception:
                pass
        return

    await reply_with_voice(context, chat_id, "📩✨ *Message bien reçu !* Merci 🙏", reply_markup=family_main_menu() if is_family_u else None)
    try:
        await notify_owners(context, f"💬 {user.full_name} (id {uid}) :\n{text}")
    except Exception:
        pass


# ============== PHOTOS (images pour welcome/goodbye/profil) ==============
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    awaiting = context.user_data.get("awaiting")
    if not awaiting:
        return
    user = update.effective_user
    file_id = update.message.photo[-1].file_id

    if awaiting == "setwelcome_image" and is_owner_user(user):
        data["welcome"]["image_file_id"] = file_id
        save_data(data)
        await update.message.reply_text("✅ Image welcome enregistrée.", reply_markup=setwelcome_keyboard())
    elif awaiting == "setgoodbye_image" and is_owner_user(user):
        data["goodbye"]["image_file_id"] = file_id
        save_data(data)
        await update.message.reply_text("✅ Image goodbye enregistrée.", reply_markup=setgoodbye_keyboard())
    elif awaiting == "setboost_image" and is_owner_user(user):
        data["boost"]["image_file_id"] = file_id
        save_data(data)
        await update.message.reply_text("✅ Image boost enregistrée.", reply_markup=setboost_keyboard())
    elif awaiting == "setrules_image" and is_owner_user(user):
        data["rules_image_file_id"] = file_id
        save_data(data)
        await update.message.reply_text("✅ Image des règles enregistrée.", reply_markup=rules_menu_keyboard())
    elif awaiting == "setprofile_image" and (is_owner_user(user) or is_family(user.id)):
        uid = str(user.id)
        data["family_members"].setdefault(uid, {"name": user.full_name, "username": user.username, "profile": {}})
        data["family_members"][uid].setdefault("profile", {})
        data["family_members"][uid]["profile"]["image_file_id"] = file_id
        save_data(data)
        await update.message.reply_text("✅ Photo de profil enregistrée.", reply_markup=setprofile_keyboard())
    elif awaiting == "couple_photo" and is_owner_user(user):
        role = "owner" if user.id == OWNER_ID else "partner"
        filename = f"{role}.jpg"
        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(str(STATIC_DIR / filename))
        cd = load_couple_data()
        cd[role] = {"name": user.first_name, "photo": filename}
        save_couple_data(cd)
        await update.message.reply_text(f"✅ Ta photo est enregistrée sur la page 'Notre histoire' ! 💞\n{WEBAPP_URL}/couple")

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


async def handle_chat_boost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Détecte un boost de chaîne/canal et remercie l'utilisateur."""
    cb = update.chat_boost
    if cb is None:
        return
    chat_id = cb.chat.id
    source = cb.boost.source
    boost_user = getattr(source, "user", None)
    if boost_user is None:
        return  # boost anonyme ou via un cadeau non attribuable à un utilisateur identifiable

    mention = f"[{boost_user.full_name}](tg://user?id={boost_user.id})"
    text = data["boost"]["text"].format(mention=mention)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎉 Voir le message", callback_data="boostpopup_show")]]) if data["boost"].get("popup_text") else None

    if data["boost"].get("image_file_id"):
        await context.bot.send_photo(chat_id, data["boost"]["image_file_id"], caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def handle_precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)


async def handle_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    user = update.effective_user

    if payment.invoice_payload == "unlock_profiles":
        if user.id not in data["unlocked_profiles"]:
            data["unlocked_profiles"].append(user.id)
            save_data(data)
        await update.message.reply_text(
            f"🎉💞 *Paiement confirmé !* L'accès à 'Notre Histoire' est débloqué pour toi.\n\n{WEBAPP_URL}/couple",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await update.message.reply_text(
            f"🎉⭐ *Merci infiniment {user.first_name} !* Ton soutien de {payment.total_amount} ⭐ compte énormément pour la famille Ziox 💜",
            parse_mode=ParseMode.MARKDOWN,
        )
    try:
        await notify_owners(context, f"⭐ {user.full_name} (id {user.id}) a envoyé {payment.total_amount} étoiles ! ({payment.invoice_payload}) 🎉")
    except Exception:
        pass


def main():
    if FISH_API_KEY == "TA_CLE_FISH_AUDIO_ICI":
        logger.warning("⚠️ FISH_API_KEY non configurée — vocal désactivé.")
    if "TON-DOMAINE-RAILWAY" in WEBAPP_URL:
        logger.warning("⚠️ WEBAPP_URL non configurée — le bouton 'Notre histoire' ne fonctionnera pas encore.")

    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("Serveur web (mini app) démarré...")

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
    app.add_handler(CommandHandler("setboost", setboost_command))
    app.add_handler(CommandHandler("rules", rules_command))
    app.add_handler(CommandHandler("setrulesimage", setrulesimage_command))
    app.add_handler(CommandHandler("stars", stars_command))
    app.add_handler(CommandHandler("couple", couple_command))
    app.add_handler(CommandHandler("setcouplephoto", setcouplephoto_command))
    app.add_handler(CommandHandler("setsince", setsince_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, handle_successful_payment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(ChatMemberHandler(track_chat_members, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatBoostHandler(handle_chat_boost))
    app.add_handler(PreCheckoutQueryHandler(handle_precheckout))

    logger.info("Bot démarré...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
