import vk_api
import time
import random
import schedule
import json
import os
import threading
import logging
import ssl
from datetime import datetime
from vk_api.exceptions import ApiError
from flask import Flask, request, jsonify

app = Flask(__name__)

ssl._create_default_https_context = ssl._create_unverified_context

CONFIG_FILE = "autopiar_config.json"

DEFAULT_CONFIG = {
    "vk_token": "",
    "group_id": 240367640,
    "admin_ids": [],
    "whitelist_enabled": False,
    "whitelist_ids": [],
    "target_chats": [],
    "target_groups": [],
    "advertised_id": 240367640,
    "greeting_message": "👋 Всем привет! Я бот для пиара vk.com/club240367640",
    "spam_messages": [
        "Подпишись на vk.com/club240367640 — там крутой контент!",
        "Залетайте в vk.com/club240367640, у нас движуха",
        "vk.com/club240367640 — лучшее сообщество, подписывайся!"
    ],
    "post_messages": [
        "Новый пост в vk.com/club240367640 — заходите!",
        "Ежедневный контент уже в vk.com/club240367640",
        "Подписывайтесь на vk.com/club240367640, там свежие новости!"
    ],
    "min_delay": 30,
    "max_delay": 90,
    "spam_interval_hours": 6,
    "max_per_day": 50,
    "post_times": ["10:00", "14:00", "18:00"],
    "stats": {
        "sent_today": 0,
        "total_sent": 0,
        "last_reset": ""
    }
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("vk_autopiar.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if "greeting_message" not in cfg:
                    cfg["greeting_message"] = "👋 Всем привет! Я бот для пиара vk.com/club240367640"
                return cfg
        except:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except:
        pass

config = load_config()

vk_session = None
vk = None
sent_this_cycle = set()
scheduler_running = False

def init_vk():
    global vk_session, vk
    if config["vk_token"] and config["group_id"]:
        try:
            vk_session = vk_api.VkApi(token=config["vk_token"])
            vk = vk_session.get_api()
            log.info("✅ VK API подключён")
            return True
        except Exception as e:
            log.error(f"❌ Ошибка: {e}")
    return False

def check_access(user_id):
    if not config["admin_ids"]:
        config["admin_ids"].append(user_id)
        save_config(config)
        return True, "👑 Вы назначены главным администратором!"
    if config["whitelist_enabled"]:
        if user_id in config["admin_ids"] or user_id in config["whitelist_ids"]:
            return True, None
        return False, "🚫 Доступ запрещён."
    else:
        if user_id in config["admin_ids"]:
            return True, None
        return False, "⛔ У вас нет прав администратора."

def safe_send(peer_id, message, retry=0):
    if config["stats"]["sent_today"] >= config["max_per_day"]:
        return False
    try:
        vk.messages.send(
            peer_id=peer_id,
            message=message,
            random_id=random.randint(1, 2**31)
        )
        config["stats"]["sent_today"] += 1
        config["stats"]["total_sent"] += 1
        save_config(config)
        log.info(f"✅ Отправлено в {peer_id}")
        return True
    except ApiError as e:
        if e.code == 9:
            time.sleep(15)
            return safe_send(peer_id, message, retry)
        elif e.code == 945 and retry < 2:
            time.sleep(5)
            return safe_send(peer_id, message, retry + 1)
        elif e.code in [902, 917]:
            if peer_id in config["target_chats"]:
                config["target_chats"].remove(peer_id)
                save_config(config)
        return False
    except:
        return False

def send_greeting(peer_id):
    try:
        result = vk.messages.send(
            peer_id=peer_id,
            message=config.get("greeting_message", "👋 Привет!"),
            random_id=random.randint(1, 2**31)
        )
        log.info(f"👋 Приветствие отправлено в {peer_id}")
        return True
    except Exception as e:
        log.error(f"❌ Ошибка приветствия в {peer_id}: {e}")
        return False

def spam_cycle():
    global sent_this_cycle
    sent_this_cycle.clear()
    
    if not config["target_chats"]:
        log.warning("📭 Список чатов пуст!")
        return
    if not config["spam_messages"]:
        log.warning("📭 Нет сообщений!")
        return
    
    log.info(f"📢 Рассылка по {len(config['target_chats'])} чатам...")
    for chat_id in config["target_chats"]:
        if chat_id in sent_this_cycle:
            continue
        if config["stats"]["sent_today"] >= config["max_per_day"]:
            break
        
        msg = random.choice(config["spam_messages"])
        success = safe_send(chat_id, msg)
        
        if success:
            sent_this_cycle.add(chat_id)
        
        time.sleep(random.randint(config["min_delay"], config["max_delay"]))
    
    log.info(f"🏁 Готово! Отправлено: {len(sent_this_cycle)}")

def post_to_groups():
    if not config["target_groups"] or not config["post_messages"]:
        return
    post = random.choice(config["post_messages"])
    for group_id in config["target_groups"]:
        try:
            vk.wall.post(owner_id=group_id, message=post, from_group=1)
            log.info(f"📝 Пост в {group_id}")
        except:
            pass

def reset_daily_counter():
    config["stats"]["sent_today"] = 0
    config["stats"]["last_reset"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_config(config)

def auto_scan_chats():
    if not vk:
        return "❌ VK не подключён"
    try:
        all_found = []
        
        conversations = vk.messages.getConversations(count=200, offset=0)
        total = conversations.get("count", 0)
        log.info(f"🔍 Всего диалогов: {total}")
        
        for offset in range(0, total, 200):
            if offset > 0:
                time.sleep(1)
                conversations = vk.messages.getConversations(count=200, offset=offset)
            
            for conv in conversations.get("items", []):
                try:
                    chat = conv.get("conversation", {})
                    peer = peer.get("peer", {})
                    peer_id = peer.get("id")
                    chat_type = peer.get("type")
                    
                    can_write = chat.get("can_write", True)
                    is_kicked = chat.get("is_kicked", False)
                    
                    if chat_type == "chat" and not is_kicked and can_write:
                        title = chat.get("chat_settings", {}).get("title", "Без названия")
                        
                        if peer_id not in config["target_chats"]:
                            config["target_chats"].append(peer_id)
                            all_found.append(f"➕ {title} (peer_id: {peer_id})")
                            log.info(f"👋 Отправляю приветствие в {title} ({peer_id})...")
                            send_greeting(peer_id)
                            time.sleep(2)
                        else:
                            log.info(f"⏭ Уже в списке: {title} ({peer_id})")
                    else:
                        log.info(f"🚫 Пропускаю (недоступен): {peer_id}")
                except Exception as e:
                    log.error(f"❌ Ошибка обработки: {e}")
                    continue
            
            if total <= 200:
                break
        
        save_config(config)
        
        if all_found:
            return f"✅ Найдено бесед: {len(all_found)}\n" + "\n".join(all_found)
        
        return f"ℹ️ Новых бесед не найдено. Всего в списке: {len(config['target_chats'])}"
    except Exception as e:
        log.error(f"❌ Ошибка сканирования: {e}")
        return f"❌ Ошибка: {e}"

def auto_scan_groups():
    if not vk:
        return "❌ VK не подключён"
    try:
        groups = vk.groups.get(filter="admin", extended=1)
        found = []
        for group in groups.get("items", []):
            try:
                gid = -group["id"]
                name = group.get("name", "Без названия")
                if gid not in config["target_groups"]:
                    config["target_groups"].append(gid)
                    found.append(f"➕ {name} (ID: {gid})")
            except:
                continue
        save_config(config)
        if found:
            return "✅ Найдены:\n" + "\n".join(found)
        return "ℹ️ Новых групп не найдено"
    except:
        return "ℹ️ Сканирование групп недоступно"

def process_command(user_id, text):
    try:
        text = text.strip()
        
        for p in ["!", "/", ".", "?"]:
            if text.startswith(p):
                text = text[1:].strip()
                break
        
        ru_to_en = {
            "помощь": "help", "хелп": "help", "help": "help",
            "старт": "start", "start": "start",
            "спам": "spam", "рассылка": "spam", "spam": "spam",
            "пост": "post", "постинг": "post", "post": "post",
            "список": "list", "лист": "list", "list": "list",
            "статистика": "stats", "стата": "stats", "stats": "stats",
            "сканировать": "scan_chats", "скан": "scan_chats", "scan_chats": "scan_chats",
            "сканировать_группы": "scan_groups", "scan_groups": "scan_groups",
            "задержка": "delay", "delay": "delay",
            "интервал": "spam_interval", "spam_interval": "spam_interval",
            "лимит": "max_per_day", "max_per_day": "max_per_day",
            "сброс": "reset", "reset": "reset",
            "дебаг": "debug", "debug": "debug",
            "админы": "admins", "admins": "admins",
            "белый_список": "whitelist", "whitelist": "whitelist",
            "цель": "target", "target": "target",
            "рестарт": "restart", "перезапуск": "restart", "restart": "restart",
            "приветствие": "set_greeting", "greeting": "set_greeting",
            "сменить_цель": "set_target", "set_target": "set_target",
        }
        
        parts = text.strip().split()
        if not parts:
            return "❓ Введите команду"
        
        cmd = parts[0].lower()
        args = parts[1:]
        
        if cmd in ru_to_en:
            cmd = ru_to_en[cmd]
        
    except:
        return "❌ Ошибка"

    has_access, access_msg = check_access(user_id)
    if not has_access:
        return access_msg

    prefix = ""
    if access_msg:
        prefix = access_msg + "\n\n"

    if cmd in ["start", "help"]:
        return prefix + f"""🤖 *АВТОПИАР БОТ v5.8*

🎯 Пиарим: vk.com/club{config['advertised_id']}

💡 Команды: / ! . ? или без префикса
🌐 Русский и English поддерживаются

╔════════════════════════════════╗
║  📋 *ОСНОВНЫЕ КОМАНДЫ*       ║
╠════════════════════════════════╣
║ /scan_chats — найти беседы    ║
║ /scan_groups — найти группы   ║
║ /list — чаты и группы         ║
║ /spam — запустить рассылку    ║
║ /post — запустить постинг     ║
║ /stats — статистика           ║
╚════════════════════════════════╝

╔════════════════════════════════╗
║  ⚙️ *ИНТЕРВАЛЫ*              ║
╠════════════════════════════════╣
║ /delay [мин] [макс]           ║
║ /spam_interval [часы]         ║
║ /max_per_day [число]          ║
║ /post_time добавить [ЧЧ:ММ]   ║
║ /post_time удалить [ЧЧ:ММ]    ║
║ /post_time_clear              ║
╚════════════════════════════════╝

╔════════════════════════════════╗
║  📝 *ТЕКСТЫ*                 ║
╠════════════════════════════════╣
║ /set_spam [текст] — заменить  ║
║ /add_spam [текст] — добавить  ║
║ /show_spam — показать         ║
║ /del_spam [номер] — удалить   ║
║ /set_post [текст] — пост      ║
║ /show_post — показать посты   ║
║ /set_greeting [текст]         ║
║ /set_target [ID] — цель пиара ║
╚════════════════════════════════╝

╔════════════════════════════════╗
║  👥 *ЧАТЫ И ГРУППЫ*          ║
╠════════════════════════════════╣
║ /add_chat [peer_id]           ║
║ /del_chat [peer_id]           ║
║ /add_group [id]               ║
║ /del_group [id]               ║
╚════════════════════════════════╝

╔════════════════════════════════╗
║  🔐 *БЕЛЫЙ СПИСОК*           ║
╠════════════════════════════════╣
║ /whitelist — показать         ║
║ /whitelist_add [ID]           ║
║ /whitelist_remove [ID]        ║
║ /whitelist_on / _off          ║
╚════════════════════════════════╝

╔════════════════════════════════╗
║  🔧 *ПРОЧЕЕ*                 ║
╠════════════════════════════════╣
║ /target — что пиарим          ║
║ /debug — тех. информация      ║
║ /reset — сброс счётчика       ║
║ /restart — перезапуск         ║
║ /admins — админы              ║
║ /add_admin [ID]               ║
╚════════════════════════════════╝"""

    elif cmd == "scan_chats":
        return prefix + auto_scan_chats()
    elif cmd == "scan_groups":
        return prefix + auto_scan_groups()

    elif cmd == "list":
        chats_str = "\n".join([f"• {c}" for c in config["target_chats"]]) or "пусто"
        groups_str = "\n".join([f"• {g}" for g in config["target_groups"]]) or "пусто"
        return f"💬 *Беседы:*\n{chats_str}\n\n📢 *Группы:*\n{groups_str}"

    elif cmd == "spam":
        threading.Thread(target=spam_cycle, daemon=True).start()
        return prefix + "✅ Рассылка запущена"
    elif cmd == "post":
        threading.Thread(target=post_to_groups, daemon=True).start()
        return prefix + "✅ Постинг запущен"

    elif cmd == "stats":
        wl = "✅" if config["whitelist_enabled"] else "❌"
        return f"""📊 *СТАТИСТИКА*

• Отправлено сегодня: {config['stats']['sent_today']}/{config['max_per_day']}
• Всего отправлено: {config['stats']['total_sent']}
• Чатов в списке: {len(config['target_chats'])}
• Групп в списке: {len(config['target_groups'])}
• Интервал рассылки: {config['spam_interval_hours']}ч
• Время постинга: {', '.join(config['post_times']) or 'нет'}
• Белый список: {wl}
• Пиарим: vk.com/club{config['advertised_id']}"""

    elif cmd == "delay":
        if len(args) >= 2:
            try:
                config["min_delay"] = int(args[0])
                config["max_delay"] = int(args[1])
                save_config(config)
                return f"✅ Задержка: {config['min_delay']}-{config['max_delay']} сек"
            except:
                return "❌ Числа!"
        return "❌ delay [мин] [макс]"

    elif cmd == "spam_interval":
        if args:
            try:
                config["spam_interval_hours"] = float(args[0])
                save_config(config)
                return f"✅ Интервал: каждые {config['spam_interval_hours']}ч"
            except:
                pass
        return "❌ spam_interval [часы]"

    elif cmd == "max_per_day":
        if args:
            try:
                config["max_per_day"] = int(args[0])
                save_config(config)
                return f"✅ Лимит: {config['max_per_day']}/день"
            except:
                pass
        return "❌ max_per_day [число]"

    elif cmd == "post_time":
        if len(args) >= 2 and args[0] == "добавить":
            t = args[1]
            if t not in config["post_times"]:
                config["post_times"].append(t)
                save_config(config)
                return f"✅ Добавлено: {t}"
            return "⚠️ Уже есть"
        elif len(args) >= 2 and args[0] == "удалить":
            t = args[1]
            if t in config["post_times"]:
                config["post_times"].remove(t)
                save_config(config)
                return f"✅ Удалено: {t}"
            return "⚠️ Нет"
        return "❌ post_time добавить/удалить ЧЧ:ММ"

    elif cmd == "post_time_clear":
        config["post_times"] = []
        save_config(config)
        return "✅ Расписание очищено"

    elif cmd == "set_spam":
        text = " ".join(args)
        if text:
            config["spam_messages"] = [text]
            save_config(config)
            return f"✅ Текст заменён на:\n«{text}»"
        return "❌ set_spam [текст]"

    elif cmd == "add_spam":
        text = " ".join(args)
        if text:
            config["spam_messages"].append(text)
            save_config(config)
            return f"✅ Добавлен вариант #{len(config['spam_messages'])}"
        return "❌ add_spam [текст]"

    elif cmd == "show_spam":
        if not config["spam_messages"]:
            return "📭 Список пуст"
        return "📝 *Тексты рассылки:*\n" + "\n".join([f"{i+1}. {m}" for i, m in enumerate(config["spam_messages"])])

    elif cmd == "del_spam":
        if args:
            try:
                idx = int(args[0]) - 1
                if 0 <= idx < len(config["spam_messages"]):
                    removed = config["spam_messages"].pop(idx)
                    save_config(config)
                    return f"✅ Удалён: «{removed}»"
            except:
                pass
        return "❌ del_spam [номер]"

    elif cmd == "set_post":
        text = " ".join(args)
        if text:
            config["post_messages"] = [text]
            save_config(config)
            return f"✅ Текст постов заменён на:\n«{text}»"
        return "❌ set_post [текст]"

    elif cmd == "add_post":
        text = " ".join(args)
        if text:
            config["post_messages"].append(text)
            save_config(config)
            return f"✅ Добавлен пост #{len(config['post_messages'])}"
        return "❌ add_post [текст]"

    elif cmd == "show_post":
        if not config["post_messages"]:
            return "📭 Список пуст"
        return "📝 *Тексты постов:*\n" + "\n".join([f"{i+1}. {m}" for i, m in enumerate(config["post_messages"])])

    elif cmd == "del_post":
        if args:
            try:
                idx = int(args[0]) - 1
                if 0 <= idx < len(config["post_messages"]):
                    removed = config["post_messages"].pop(idx)
                    save_config(config)
                    return f"✅ Удалён: «{removed}»"
            except:
                pass
        return "❌ del_post [номер]"

    elif cmd == "set_greeting":
        text = " ".join(args)
        if text:
            config["greeting_message"] = text
            save_config(config)
            return f"✅ Приветствие: «{text}»"
        return "❌ set_greeting [текст]"

    elif cmd == "set_target":
        if args:
            try:
                new_id = int(args[0])
                old_id = config["advertised_id"]
                config["advertised_id"] = new_id
                save_config(config)
                return f"""✅ Цель изменена!

📤 Было: vk.com/club{old_id}
📥 Стало: vk.com/club{new_id}"""
            except:
                return "❌ ID должен быть числом"
        return "❌ set_target [ID]"

    elif cmd == "add_chat":
        if args:
            try:
                pid = int(args[0])
                if pid not in config["target_chats"]:
                    config["target_chats"].append(pid)
                    save_config(config)
                    return f"✅ Беседа {pid} добавлена"
                return "⚠️ Уже в списке"
            except:
                return "❌ ID должен быть числом"
        return "❌ add_chat [peer_id]"

    elif cmd == "del_chat":
        if args:
            try:
                pid = int(args[0])
                if pid in config["target_chats"]:
                    config["target_chats"].remove(pid)
                    save_config(config)
                    return f"✅ Беседа {pid} удалена"
                return "⚠️ Не найдена"
            except:
                pass
        return "❌ del_chat [peer_id]"

    elif cmd == "add_group":
        if args:
            try:
                gid = int(args[0])
                if gid > 0:
                    gid = -gid
                if gid not in config["target_groups"]:
                    config["target_groups"].append(gid)
                    save_config(config)
                    return f"✅ Группа {gid} добавлена"
                return "⚠️ Уже в списке"
            except:
                return "❌ ID должен быть числом"
        return "❌ add_group [id]"

    elif cmd == "del_group":
        if args:
            try:
                gid = int(args[0])
                if gid > 0:
                    gid = -gid
                if gid in config["target_groups"]:
                    config["target_groups"].remove(gid)
                    save_config(config)
                    return f"✅ Группа {gid} удалена"
                return "⚠️ Не найдена"
            except:
                pass
        return "❌ del_group [id]"

    elif cmd == "whitelist":
        if not config["whitelist_ids"]:
            return "📭 Белый список пуст"
        wl = "\n".join([f"• {u}" for u in config["whitelist_ids"]])
        st = "✅ Включён" if config["whitelist_enabled"] else "❌ Выключен"
        return f"🔐 *Белый список ({st}):*\n{wl}"

    elif cmd == "whitelist_add":
        if args:
            try:
                u = int(args[0])
                if u not in config["whitelist_ids"]:
                    config["whitelist_ids"].append(u)
                    save_config(config)
                    return f"✅ Пользователь {u} добавлен в белый список"
            except:
                pass
        return "❌ whitelist_add [ID]"

    elif cmd == "whitelist_remove":
        if args:
            try:
                u = int(args[0])
                if u in config["whitelist_ids"]:
                    config["whitelist_ids"].remove(u)
                    save_config(config)
                    return f"✅ Пользователь {u} удалён из белого списка"
            except:
                pass
        return "❌ whitelist_remove [ID]"

    elif cmd == "whitelist_clear":
        config["whitelist_ids"] = []
        save_config(config)
        return "✅ Белый список очищен"

    elif cmd == "whitelist_on":
        config["whitelist_enabled"] = True
        save_config(config)
        return "🔐 Белый список ВКЛЮЧЕН"

    elif cmd == "whitelist_off":
        config["whitelist_enabled"] = False
        save_config(config)
        return "🔓 Белый список ВЫКЛЮЧЕН"

    elif cmd == "add_admin":
        if args:
            try:
                a = int(args[0])
                if a not in config["admin_ids"]:
                    config["admin_ids"].append(a)
                    save_config(config)
                    return f"✅ Администратор {a} добавлен"
            except:
                pass
        return "❌ add_admin [ID]"

    elif cmd == "admins":
        return "👑 *Администраторы:*\n" + "\n".join([f"• {a}" for a in config["admin_ids"]])

    elif cmd == "target":
        return f"""🎯 *ТЕКУЩАЯ ЦЕЛЬ ПИАРА:*
📢 vk.com/club{config['advertised_id']}

📝 Для смены: set_target [ID]"""

    elif cmd == "debug":
        return f"""🔧 *ТЕХНИЧЕСКАЯ ИНФОРМАЦИЯ:*
• Токен: {config['vk_token'][:15]}...
• Группа бота: {config['group_id']}
• Пиарим: club{config['advertised_id']}
• VK API: {'✅' if vk else '❌'}
• Чатов: {len(config['target_chats'])}
• Групп: {len(config['target_groups'])}
• Приветствие: {config.get('greeting_message', 'Нет')[:50]}..."""

    elif cmd == "reset":
        reset_daily_counter()
        return "✅ Дневной счётчик сброшен"

    elif cmd == "restart":
        return "✅ Планировщик перезапущен (на Webhook это не нужно)"

    else:
        return "❓ Неизвестная команда. Напиши help"

# ========== WEBHOOK ОБРАБОТЧИКИ ==========

@app.route('/', methods=['POST'])
def webhook():
    try:
        data = request.json
        log.info(f"📩 Получен запрос от ВК: {data.get('type')}")
        
        # === ПОДТВЕРЖДЕНИЕ СЕРВЕРА ===
        if data.get('type') == 'confirmation':
            return "2309a801"  # <-- ВАША СТРОКА!
        
        # === ОБРАБОТКА СООБЩЕНИЙ ===
        if data.get('type') == 'message_new':
            msg = data['object']['message']
            user_id = msg.get('from_id')
            text = msg.get('text', '')
            peer_id = msg.get('peer_id')
            
            if user_id and peer_id:
                log.info(f"📩 {user_id}: {text[:50]}")
                response = process_command(user_id, text)
                
                try:
                    vk.messages.send(
                        peer_id=peer_id,
                        message=response,
                        random_id=random.randint(1, 2**31)
                    )
                except Exception as e:
                    log.error(f"❌ Ошибка отправки: {e}")
        
        return "ok"  # <-- ВК ждёт просто "ok"
    
    except Exception as e:
        log.error(f"❌ Ошибка webhook: {e}")
        return "error", 500

@app.route('/', methods=['GET'])
def health():
    return "🤖 Бот работает!", 200

# ========== ЗАПУСК ==========

if __name__ == "__main__":
    print("""
╔════════════════════════════════╗
║   🤖 VK АВТОПИАР БОТ v5.8    ║
║   WEBHOOK ВЕРСИЯ              ║
╚════════════════════════════════╝
    """)
    
    if not config["vk_token"]:
        config["vk_token"] = input("🔑 Токен: ").strip()
        save_config(config)
    
    if init_vk():
        log.info("✅ Бот готов! Используй Webhook")
        port = int(os.environ.get('PORT', 5000))
        app.run(host='0.0.0.0', port=port)
    else:
        log.error("❌ Не удалось подключиться")        "Ежедневный контент уже в vk.com/club240367640",
        "Подписывайтесь на vk.com/club240367640, там свежие новости!"
    ],
    "min_delay": 30,
    "max_delay": 90,
    "spam_interval_hours": 6,
    "max_per_day": 50,
    "post_times": ["10:00", "14:00", "18:00"],
    "stats": {
        "sent_today": 0,
        "total_sent": 0,
        "last_reset": ""
    }
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("vk_autopiar.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if "greeting_message" not in cfg:
                    cfg["greeting_message"] = "👋 Всем привет! Я бот для пиара vk.com/club240367640"
                return cfg
        except:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except:
        pass

config = load_config()

vk_session = None
vk = None
sent_this_cycle = set()
scheduler_running = False

def init_vk():
    global vk_session, vk
    if config["vk_token"] and config["group_id"]:
        try:
            vk_session = vk_api.VkApi(token=config["vk_token"])
            vk = vk_session.get_api()
            log.info("✅ VK API подключён")
            return True
        except Exception as e:
            log.error(f"❌ Ошибка: {e}")
    return False

def check_access(user_id):
    if not config["admin_ids"]:
        config["admin_ids"].append(user_id)
        save_config(config)
        return True, "👑 Вы назначены главным администратором!"
    if config["whitelist_enabled"]:
        if user_id in config["admin_ids"] or user_id in config["whitelist_ids"]:
            return True, None
        return False, "🚫 Доступ запрещён."
    else:
        if user_id in config["admin_ids"]:
            return True, None
        return False, "⛔ У вас нет прав администратора."

def safe_send(peer_id, message, retry=0):
    if config["stats"]["sent_today"] >= config["max_per_day"]:
        return False
    try:
        vk.messages.send(
            peer_id=peer_id,
            message=message,
            random_id=random.randint(1, 2**31)
        )
        config["stats"]["sent_today"] += 1
        config["stats"]["total_sent"] += 1
        save_config(config)
        log.info(f"✅ Отправлено в {peer_id}")
        return True
    except ApiError as e:
        if e.code == 9:
            time.sleep(15)
            return safe_send(peer_id, message, retry)
        elif e.code == 945 and retry < 2:
            time.sleep(5)
            return safe_send(peer_id, message, retry + 1)
        elif e.code in [902, 917]:
            if peer_id in config["target_chats"]:
                config["target_chats"].remove(peer_id)
                save_config(config)
        return False
    except:
        return False

def send_greeting(peer_id):
    try:
        result = vk.messages.send(
            peer_id=peer_id,
            message=config.get("greeting_message", "👋 Привет!"),
            random_id=random.randint(1, 2**31)
        )
        log.info(f"👋 Приветствие отправлено в {peer_id}")
        return True
    except Exception as e:
        log.error(f"❌ Ошибка приветствия в {peer_id}: {e}")
        return False

def spam_cycle():
    global sent_this_cycle
    sent_this_cycle.clear()
    
    if not config["target_chats"]:
        log.warning("📭 Список чатов пуст!")
        return
    if not config["spam_messages"]:
        log.warning("📭 Нет сообщений!")
        return
    
    log.info(f"📢 Рассылка по {len(config['target_chats'])} чатам...")
    for chat_id in config["target_chats"]:
        if chat_id in sent_this_cycle:
            continue
        if config["stats"]["sent_today"] >= config["max_per_day"]:
            break
        
        msg = random.choice(config["spam_messages"])
        success = safe_send(chat_id, msg)
        
        if success:
            sent_this_cycle.add(chat_id)
        
        time.sleep(random.randint(config["min_delay"], config["max_delay"]))
    
    log.info(f"🏁 Готово! Отправлено: {len(sent_this_cycle)}")

def post_to_groups():
    if not config["target_groups"] or not config["post_messages"]:
        return
    post = random.choice(config["post_messages"])
    for group_id in config["target_groups"]:
        try:
            vk.wall.post(owner_id=group_id, message=post, from_group=1)
            log.info(f"📝 Пост в {group_id}")
        except:
            pass

def reset_daily_counter():
    config["stats"]["sent_today"] = 0
    config["stats"]["last_reset"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_config(config)

def auto_scan_chats():
    if not vk:
        return "❌ VK не подключён"
    try:
        all_found = []
        
        conversations = vk.messages.getConversations(count=200, offset=0)
        total = conversations.get("count", 0)
        log.info(f"🔍 Всего диалогов: {total}")
        
        for offset in range(0, total, 200):
            if offset > 0:
                time.sleep(1)
                conversations = vk.messages.getConversations(count=200, offset=offset)
            
            for conv in conversations.get("items", []):
                try:
                    chat = conv.get("conversation", {})
                    peer = chat.get("peer", {})
                    peer_id = peer.get("id")
                    chat_type = peer.get("type")
                    
                    can_write = chat.get("can_write", True)
                    is_kicked = chat.get("is_kicked", False)
                    
                    if chat_type == "chat" and not is_kicked and can_write:
                        title = chat.get("chat_settings", {}).get("title", "Без названия")
                        
                        if peer_id not in config["target_chats"]:
                            config["target_chats"].append(peer_id)
                            all_found.append(f"➕ {title} (peer_id: {peer_id})")
                            log.info(f"👋 Отправляю приветствие в {title} ({peer_id})...")
                            send_greeting(peer_id)
                            time.sleep(2)
                        else:
                            log.info(f"⏭ Уже в списке: {title} ({peer_id})")
                    else:
                        log.info(f"🚫 Пропускаю (недоступен): {peer_id}")
                except Exception as e:
                    log.error(f"❌ Ошибка обработки: {e}")
                    continue
            
            if total <= 200:
                break
        
        save_config(config)
        
        if all_found:
            return f"✅ Найдено бесед: {len(all_found)}\n" + "\n".join(all_found)
        
        return f"ℹ️ Новых бесед не найдено. Всего в списке: {len(config['target_chats'])}"
    except Exception as e:
        log.error(f"❌ Ошибка сканирования: {e}")
        return f"❌ Ошибка: {e}"

def auto_scan_groups():
    if not vk:
        return "❌ VK не подключён"
    try:
        groups = vk.groups.get(filter="admin", extended=1)
        found = []
        for group in groups.get("items", []):
            try:
                gid = -group["id"]
                name = group.get("name", "Без названия")
                if gid not in config["target_groups"]:
                    config["target_groups"].append(gid)
                    found.append(f"➕ {name} (ID: {gid})")
            except:
                continue
        save_config(config)
        if found:
            return "✅ Найдены:\n" + "\n".join(found)
        return "ℹ️ Новых групп не найдено"
    except:
        return "ℹ️ Сканирование групп недоступно"

def process_command(user_id, text):
    try:
        text = text.strip()
        
        for p in ["!", "/", ".", "?"]:
            if text.startswith(p):
                text = text[1:].strip()
                break
        
        ru_to_en = {
            "помощь": "help", "хелп": "help", "help": "help",
            "старт": "start", "start": "start",
            "спам": "spam", "рассылка": "spam", "spam": "spam",
            "пост": "post", "постинг": "post", "post": "post",
            "список": "list", "лист": "list", "list": "list",
            "статистика": "stats", "стата": "stats", "stats": "stats",
            "сканировать": "scan_chats", "скан": "scan_chats", "scan_chats": "scan_chats",
            "сканировать_группы": "scan_groups", "scan_groups": "scan_groups",
            "задержка": "delay", "delay": "delay",
            "интервал": "spam_interval", "spam_interval": "spam_interval",
            "лимит": "max_per_day", "max_per_day": "max_per_day",
            "сброс": "reset", "reset": "reset",
            "дебаг": "debug", "debug": "debug",
            "админы": "admins", "admins": "admins",
            "белый_список": "whitelist", "whitelist": "whitelist",
            "цель": "target", "target": "target",
            "рестарт": "restart", "перезапуск": "restart", "restart": "restart",
            "приветствие": "set_greeting", "greeting": "set_greeting",
            "сменить_цель": "set_target", "set_target": "set_target",
        }
        
        parts = text.strip().split()
        if not parts:
            return "❓ Введите команду"
        
        cmd = parts[0].lower()
        args = parts[1:]
        
        if cmd in ru_to_en:
            cmd = ru_to_en[cmd]
        
    except:
        return "❌ Ошибка"

    has_access, access_msg = check_access(user_id)
    if not has_access:
        return access_msg

    prefix = ""
    if access_msg:
        prefix = access_msg + "\n\n"

    if cmd in ["start", "help"]:
        return prefix + f"""🤖 *АВТОПИАР БОТ v5.8*

🎯 Пиарим: vk.com/club{config['advertised_id']}

💡 Команды: / ! . ? или без префикса
🌐 Русский и English поддерживаются

╔════════════════════════════════╗
║  📋 *ОСНОВНЫЕ КОМАНДЫ*       ║
╠════════════════════════════════╣
║ /scan_chats — найти беседы    ║
║ /scan_groups — найти группы   ║
║ /list — чаты и группы         ║
║ /spam — запустить рассылку    ║
║ /post — запустить постинг     ║
║ /stats — статистика           ║
╚════════════════════════════════╝

╔════════════════════════════════╗
║  ⚙️ *ИНТЕРВАЛЫ*              ║
╠════════════════════════════════╣
║ /delay [мин] [макс]           ║
║ /spam_interval [часы]         ║
║ /max_per_day [число]          ║
║ /post_time добавить [ЧЧ:ММ]   ║
║ /post_time удалить [ЧЧ:ММ]    ║
║ /post_time_clear              ║
╚════════════════════════════════╝

╔════════════════════════════════╗
║  📝 *ТЕКСТЫ*                 ║
╠════════════════════════════════╣
║ /set_spam [текст] — заменить  ║
║ /add_spam [текст] — добавить  ║
║ /show_spam — показать         ║
║ /del_spam [номер] — удалить   ║
║ /set_post [текст] — пост      ║
║ /show_post — показать посты   ║
║ /set_greeting [текст]         ║
║ /set_target [ID] — цель пиара ║
╚════════════════════════════════╝

╔════════════════════════════════╗
║  👥 *ЧАТЫ И ГРУППЫ*          ║
╠════════════════════════════════╣
║ /add_chat [peer_id]           ║
║ /del_chat [peer_id]           ║
║ /add_group [id]               ║
║ /del_group [id]               ║
╚════════════════════════════════╝

╔════════════════════════════════╗
║  🔐 *БЕЛЫЙ СПИСОК*           ║
╠════════════════════════════════╣
║ /whitelist — показать         ║
║ /whitelist_add [ID]           ║
║ /whitelist_remove [ID]        ║
║ /whitelist_on / _off          ║
╚════════════════════════════════╝

╔════════════════════════════════╗
║  🔧 *ПРОЧЕЕ*                 ║
╠════════════════════════════════╣
║ /target — что пиарим          ║
║ /debug — тех. информация      ║
║ /reset — сброс счётчика       ║
║ /restart — перезапуск         ║
║ /admins — админы              ║
║ /add_admin [ID]               ║
╚════════════════════════════════╝"""

    elif cmd == "scan_chats":
        return prefix + auto_scan_chats()
    elif cmd == "scan_groups":
        return prefix + auto_scan_groups()

    elif cmd == "list":
        chats_str = "\n".join([f"• {c}" for c in config["target_chats"]]) or "пусто"
        groups_str = "\n".join([f"• {g}" for g in config["target_groups"]]) or "пусто"
        return f"💬 *Беседы:*\n{chats_str}\n\n📢 *Группы:*\n{groups_str}"

    elif cmd == "spam":
        threading.Thread(target=spam_cycle, daemon=True).start()
        return prefix + "✅ Рассылка запущена"
    elif cmd == "post":
        threading.Thread(target=post_to_groups, daemon=True).start()
        return prefix + "✅ Постинг запущен"

    elif cmd == "stats":
        wl = "✅" if config["whitelist_enabled"] else "❌"
        return f"""📊 *СТАТИСТИКА*

• Отправлено сегодня: {config['stats']['sent_today']}/{config['max_per_day']}
• Всего отправлено: {config['stats']['total_sent']}
• Чатов в списке: {len(config['target_chats'])}
• Групп в списке: {len(config['target_groups'])}
• Интервал рассылки: {config['spam_interval_hours']}ч
• Время постинга: {', '.join(config['post_times']) or 'нет'}
• Белый список: {wl}
• Пиарим: vk.com/club{config['advertised_id']}"""

    elif cmd == "delay":
        if len(args) >= 2:
            try:
                config["min_delay"] = int(args[0])
                config["max_delay"] = int(args[1])
                save_config(config)
                return f"✅ Задержка: {config['min_delay']}-{config['max_delay']} сек"
            except:
                return "❌ Числа!"
        return "❌ delay [мин] [макс]"

    elif cmd == "spam_interval":
        if args:
            try:
                config["spam_interval_hours"] = float(args[0])
                save_config(config)
                return f"✅ Интервал: каждые {config['spam_interval_hours']}ч"
            except:
                pass
        return "❌ spam_interval [часы]"

    elif cmd == "max_per_day":
        if args:
            try:
                config["max_per_day"] = int(args[0])
                save_config(config)
                return f"✅ Лимит: {config['max_per_day']}/день"
            except:
                pass
        return "❌ max_per_day [число]"

    elif cmd == "post_time":
        if len(args) >= 2 and args[0] == "добавить":
            t = args[1]
            if t not in config["post_times"]:
                config["post_times"].append(t)
                save_config(config)
                return f"✅ Добавлено: {t}"
            return "⚠️ Уже есть"
        elif len(args) >= 2 and args[0] == "удалить":
            t = args[1]
            if t in config["post_times"]:
                config["post_times"].remove(t)
                save_config(config)
                return f"✅ Удалено: {t}"
            return "⚠️ Нет"
        return "❌ post_time добавить/удалить ЧЧ:ММ"

    elif cmd == "post_time_clear":
        config["post_times"] = []
        save_config(config)
        return "✅ Расписание очищено"

    elif cmd == "set_spam":
        text = " ".join(args)
        if text:
            config["spam_messages"] = [text]
            save_config(config)
            return f"✅ Текст заменён на:\n«{text}»"
        return "❌ set_spam [текст]"

    elif cmd == "add_spam":
        text = " ".join(args)
        if text:
            config["spam_messages"].append(text)
            save_config(config)
            return f"✅ Добавлен вариант #{len(config['spam_messages'])}"
        return "❌ add_spam [текст]"

    elif cmd == "show_spam":
        if not config["spam_messages"]:
            return "📭 Список пуст"
        return "📝 *Тексты рассылки:*\n" + "\n".join([f"{i+1}. {m}" for i, m in enumerate(config["spam_messages"])])

    elif cmd == "del_spam":
        if args:
            try:
                idx = int(args[0]) - 1
                if 0 <= idx < len(config["spam_messages"]):
                    removed = config["spam_messages"].pop(idx)
                    save_config(config)
                    return f"✅ Удалён: «{removed}»"
            except:
                pass
        return "❌ del_spam [номер]"

    elif cmd == "set_post":
        text = " ".join(args)
        if text:
            config["post_messages"] = [text]
            save_config(config)
            return f"✅ Текст постов заменён на:\n«{text}»"
        return "❌ set_post [текст]"

    elif cmd == "add_post":
        text = " ".join(args)
        if text:
            config["post_messages"].append(text)
            save_config(config)
            return f"✅ Добавлен пост #{len(config['post_messages'])}"
        return "❌ add_post [текст]"

    elif cmd == "show_post":
        if not config["post_messages"]:
            return "📭 Список пуст"
        return "📝 *Тексты постов:*\n" + "\n".join([f"{i+1}. {m}" for i, m in enumerate(config["post_messages"])])

    elif cmd == "del_post":
        if args:
            try:
                idx = int(args[0]) - 1
                if 0 <= idx < len(config["post_messages"]):
                    removed = config["post_messages"].pop(idx)
                    save_config(config)
                    return f"✅ Удалён: «{removed}»"
            except:
                pass
        return "❌ del_post [номер]"

    elif cmd == "set_greeting":
        text = " ".join(args)
        if text:
            config["greeting_message"] = text
            save_config(config)
            return f"✅ Приветствие: «{text}»"
        return "❌ set_greeting [текст]"

    elif cmd == "set_target":
        if args:
            try:
                new_id = int(args[0])
                old_id = config["advertised_id"]
                config["advertised_id"] = new_id
                save_config(config)
                return f"""✅ Цель изменена!

📤 Было: vk.com/club{old_id}
📥 Стало: vk.com/club{new_id}"""
            except:
                return "❌ ID должен быть числом"
        return "❌ set_target [ID]"

    elif cmd == "add_chat":
        if args:
            try:
                pid = int(args[0])
                if pid not in config["target_chats"]:
                    config["target_chats"].append(pid)
                    save_config(config)
                    return f"✅ Беседа {pid} добавлена"
                return "⚠️ Уже в списке"
            except:
                return "❌ ID должен быть числом"
        return "❌ add_chat [peer_id]"

    elif cmd == "del_chat":
        if args:
            try:
                pid = int(args[0])
                if pid in config["target_chats"]:
                    config["target_chats"].remove(pid)
                    save_config(config)
                    return f"✅ Беседа {pid} удалена"
                return "⚠️ Не найдена"
            except:
                pass
        return "❌ del_chat [peer_id]"

    elif cmd == "add_group":
        if args:
            try:
                gid = int(args[0])
                if gid > 0:
                    gid = -gid
                if gid not in config["target_groups"]:
                    config["target_groups"].append(gid)
                    save_config(config)
                    return f"✅ Группа {gid} добавлена"
                return "⚠️ Уже в списке"
            except:
                return "❌ ID должен быть числом"
        return "❌ add_group [id]"

    elif cmd == "del_group":
        if args:
            try:
                gid = int(args[0])
                if gid > 0:
                    gid = -gid
                if gid in config["target_groups"]:
                    config["target_groups"].remove(gid)
                    save_config(config)
                    return f"✅ Группа {gid} удалена"
                return "⚠️ Не найдена"
            except:
                pass
        return "❌ del_group [id]"

    elif cmd == "whitelist":
        if not config["whitelist_ids"]:
            return "📭 Белый список пуст"
        wl = "\n".join([f"• {u}" for u in config["whitelist_ids"]])
        st = "✅ Включён" if config["whitelist_enabled"] else "❌ Выключен"
        return f"🔐 *Белый список ({st}):*\n{wl}"

    elif cmd == "whitelist_add":
        if args:
            try:
                u = int(args[0])
                if u not in config["whitelist_ids"]:
                    config["whitelist_ids"].append(u)
                    save_config(config)
                    return f"✅ Пользователь {u} добавлен в белый список"
            except:
                pass
        return "❌ whitelist_add [ID]"

    elif cmd == "whitelist_remove":
        if args:
            try:
                u = int(args[0])
                if u in config["whitelist_ids"]:
                    config["whitelist_ids"].remove(u)
                    save_config(config)
                    return f"✅ Пользователь {u} удалён из белого списка"
            except:
                pass
        return "❌ whitelist_remove [ID]"

    elif cmd == "whitelist_clear":
        config["whitelist_ids"] = []
        save_config(config)
        return "✅ Белый список очищен"

    elif cmd == "whitelist_on":
        config["whitelist_enabled"] = True
        save_config(config)
        return "🔐 Белый список ВКЛЮЧЕН"

    elif cmd == "whitelist_off":
        config["whitelist_enabled"] = False
        save_config(config)
        return "🔓 Белый список ВЫКЛЮЧЕН"

    elif cmd == "add_admin":
        if args:
            try:
                a = int(args[0])
                if a not in config["admin_ids"]:
                    config["admin_ids"].append(a)
                    save_config(config)
                    return f"✅ Администратор {a} добавлен"
            except:
                pass
        return "❌ add_admin [ID]"

    elif cmd == "admins":
        return "👑 *Администраторы:*\n" + "\n".join([f"• {a}" for a in config["admin_ids"]])

    elif cmd == "target":
        return f"""🎯 *ТЕКУЩАЯ ЦЕЛЬ ПИАРА:*
📢 vk.com/club{config['advertised_id']}

📝 Для смены: set_target [ID]"""

    elif cmd == "debug":
        return f"""🔧 *ТЕХНИЧЕСКАЯ ИНФОРМАЦИЯ:*
• Токен: {config['vk_token'][:15]}...
• Группа бота: {config['group_id']}
• Пиарим: club{config['advertised_id']}
• VK API: {'✅' if vk else '❌'}
• Чатов: {len(config['target_chats'])}
• Групп: {len(config['target_groups'])}
• Приветствие: {config.get('greeting_message', 'Нет')[:50]}..."""

    elif cmd == "reset":
        reset_daily_counter()
        return "✅ Дневной счётчик сброшен"

    elif cmd == "restart":
        return "✅ Планировщик перезапущен (на Webhook это не нужно)"

    else:
        return "❓ Неизвестная команда. Напиши help"

# ========== WEBHOOK ОБРАБОТЧИКИ ==========

@app.route('/', methods=['POST'])
def webhook():
    try:
        data = request.json
        log.info(f"📩 Получен запрос от ВК")
        
        if data.get('type') == 'message_new':
            msg = data['object']['message']
            user_id = msg.get('from_id')
            text = msg.get('text', '')
            peer_id = msg.get('peer_id')
            
            if user_id and peer_id:
                log.info(f"📩 {user_id}: {text[:50]}")
                response = process_command(user_id, text)
                
                try:
                    vk.messages.send(
                        peer_id=peer_id,
                        message=response,
                        random_id=random.randint(1, 2**31)
                    )
                except Exception as e:
                    log.error(f"❌ Ошибка отправки: {e}")
        
        return jsonify({'ok': True})
    
    except Exception as e:
        log.error(f"❌ Ошибка webhook: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/', methods=['GET'])
def health():
    return "🤖 Бот работает!", 200

# ========== ЗАПУСК ==========

if __name__ == "__main__":
    print("""
╔════════════════════════════════╗
║   🤖 VK АВТОПИАР БОТ v5.8    ║
║   WEBHOOK ВЕРСИЯ              ║
╚════════════════════════════════╝
    """)
    
    if not config["vk_token"]:
        config["vk_token"] = input("🔑 Токен: ").strip()
        save_config(config)
    
    if init_vk():
        log.info("✅ Бот готов! Используй Webhook")
        port = int(os.environ.get('PORT', 5000))
        app.run(host='0.0.0.0', port=port)
    else:
        log.error("❌ Не удалось подключиться")
