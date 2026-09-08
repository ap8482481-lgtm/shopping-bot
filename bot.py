import telebot
from telebot import types
import sqlite3
import re
import os
from datetime import datetime

TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)
DB_FILE = "shopping.db"

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS catalog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                name TEXT,
                shop TEXT,
                price INTEGER
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS trips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                date TEXT,
                status TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS trip_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trip_id INTEGER,
                name TEXT,
                shop TEXT,
                price INTEGER,
                status TEXT DEFAULT 'need'
            )
        ''')
        conn.commit()

init_db()

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    text = (
        "🧾 **Умный бот списка покупок**\n\n"
        "1. Отправляйте новые товары строками, чтобы пополнить каталог:\n"
        "`Молоко (Пятерочка) (160)`\n\n"
        "**Команды:**\n"
        "/shop — собрать список на сегодня с разбивкой по магазинам\n"
        "/list — открыть текущий активный поход\n"
        "/history — архив прошлых покупок\n"
        "/clear — сбросить текущий активный список"
    )
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(commands=['clear'])
def clear_active_trip(message):
    chat_id = message.chat.id
    with get_db() as conn:
        conn.execute("UPDATE trips SET status = 'cancelled' WHERE chat_id = ? AND status = 'active'", (chat_id,))
        conn.commit()
    bot.reply_to(message, "🗑 Текущий активный список сброшен.")

@bot.message_handler(commands=['history'])
def show_history(message):
    chat_id = message.chat.id
    with get_db() as conn:
        trips = conn.execute(
            "SELECT * FROM trips WHERE chat_id = ? AND status = 'completed' ORDER BY id DESC LIMIT 5",
            (chat_id,)
        ).fetchall()

    if not trips:
        bot.reply_to(message, "📭 История покупок пуста.")
        return

    text = "📜 **Архив прошлых покупок:**\n\n"
    for trip in trips:
        text += f"📅 *Закупка от {trip['date']}*\n"
        items = get_db().execute("SELECT * FROM trip_items WHERE trip_id = ? AND status = 'bought'", (trip['id'],)).fetchall()
        trip_total = sum(i['price'] for i in items)
        for item in items:
            text += f"  • {item['name']} ({item['shop']}) — {item['price']} руб.\n"
        text += f"  💰 Итого потрачено: {trip_total} руб.\n\n"

    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(commands=['shop', 'list'])
def start_shopping(message):
    chat_id = message.chat.id
    with get_db() as conn:
        trip = conn.execute("SELECT * FROM trips WHERE chat_id = ? AND status = 'active'", (chat_id,)).fetchone()
        
        if not trip:
            catalog_items = conn.execute("SELECT * FROM catalog WHERE chat_id = ?", (chat_id,)).fetchall()
            if not catalog_items:
                bot.reply_to(message, "Ваш каталог пуст! Сначала отправьте список товаров текстом.")
                return
            
            cursor = conn.cursor()
            cursor.execute("INSERT INTO trips (chat_id, date, status) VALUES (?, ?, 'active')", 
                           (chat_id, datetime.now().strftime("%d.%m.%Y %H:%M")))
            trip_id = cursor.lastrowid
            
            for item in catalog_items:
                cursor.execute(
                    "INSERT INTO trip_items (trip_id, name, shop, price, status) VALUES (?, ?, ?, ?, 'need')",
                    (trip_id, item['name'], item['shop'], item['price'])
                )
            conn.commit()
            trip = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()

    send_trip_message(message.chat.id, trip['id'])

def generate_trip_content(trip_id):
    with get_db() as conn:
        items = conn.execute("SELECT * FROM trip_items WHERE trip_id = ?", (trip_id,)).fetchall()

    # Группируем по магазинам
    shops_dict = {}
    grand_total = 0
    
    for item in items:
        shop = item['shop']
        if shop not in shops_dict:
            shops_dict[shop] = []
        shops_dict[shop].append(item)
        if item['status'] == 'bought':
            grand_total += item['price']

    # Формируем текст с разбивкой по магазинам внутри блока цитаты (создает видимость фона/карточки)
    body = "🛒 *СПИСОК ПОКУПОК НА СЕГОДНЯ*\n\n"
    
    for shop, shop_items in shops_dict.items():
        body += f"🏬 *{shop}*:\n"
        for item in shop_items:
            if item['status'] == 'bought':
                icon = "✅"
                line = f"~{item['name']}~ — {item['price']} руб."
            elif item['status'] == 'skipped':
                icon = "❌"
                line = f"~~{item['name']}~~ *(исключено)*"
            else:
                icon = "🛒"
                line = f"{item['name']} — {item['price']} руб."
            body += f"  {icon} {line}\n"
        body += "\n"
    
    body += f"💰 *Сумма к оплате (купленного):* {grand_total} руб."

    # Делаем каждую строку цитатой, чтобы в Telegram был красивый плотный блок (эффект фона)
    text = "\n".join([f"> {line}" if line.strip() else ">" for line in body.split("\n")])

    # Создаем кнопки для управления
    markup = types.InlineKeyboardMarkup(row_width=1)
    for item in items:
        status_symbol = "🛒"
        if item['status'] == 'bought':
            status_symbol = "✅"
        elif item['status'] == 'skipped':
            status_symbol = "❌"
            
        btn_text = f"{status_symbol} {item['name']} ({item['shop']}) — {item['price']}р"
        markup.add(types.InlineKeyboardButton(text=btn_text, callback_data=f"toggle_{item['id']}_{trip_id}"))

    markup.add(types.InlineKeyboardButton(text="🏁 Завершить закупку (в архив)", callback_data=f"finish_{trip_id}"))
    
    return text, markup

def send_trip_message(chat_id, trip_id):
    text, markup = generate_trip_content(trip_id)
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    data = call.data
    chat_id = call.message.chat.id
    
    if data.startswith("toggle_"):
        _, item_id, trip_id = data.split("_")
        item_id = int(item_id)
        trip_id = int(trip_id)
        
        with get_db() as conn:
            item = conn.execute("SELECT * FROM trip_items WHERE id = ?", (item_id,)).fetchone()
            if item:
                current = item['status']
                next_status = 'bought' if current == 'need' else ('skipped' if current == 'bought' else 'need')
                conn.execute("UPDATE trip_items SET status = ? WHERE id = ?", (next_status, item_id))
                conn.commit()
                
        text, markup = generate_trip_content(trip_id)
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=text, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            pass
        bot.answer_callback_query(call.id)
        
    elif data.startswith("finish_"):
        _, trip_id = data.split("_")
        trip_id = int(trip_id)
        
        with get_db() as conn:
            conn.execute("UPDATE trips SET status = 'completed' WHERE id = ?", (trip_id,))
            conn.commit()
            
        bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text="🎉 Закупка завершена и сохранена в историю!")
        bot.answer_callback_query(call.id, "Успешно сохранено в архив!")

@bot.message_handler(func=lambda message: True)
def parse_items(message):
    chat_id = message.chat.id
    lines = message.text.strip().split("\n")
    added = 0

    with get_db() as conn:
        cursor = conn.cursor()
        for line in lines:
            line = line.replace("✓", "").strip()
            if not line or line.lower().startswith("итого"):
                continue

            matches = re.findall(r"\((.*?)\)", line)
            name = re.sub(r"\(.*?\)", "", line).strip()
            shop = "Прочее"
            price = 0

            for match in matches:
                clean_num = re.search(r"\d+", match)
                if clean_num and any(x in match.lower() for x in ["руб", "р", "$"]) or clean_num and match.isdigit():
                    price = int(clean_num.group())
                else:
                    shop = match.strip().capitalize()

            if name:
                cursor.execute(
                    "INSERT INTO catalog (chat_id, name, shop, price) VALUES (?, ?, ?, ?)",
                    (chat_id, name, shop, price)
                )
                added += 1
        conn.commit()

    if added > 0:
        bot.reply_to(message, f"✅ Добавлено позиций в каталог: {added}.\nТеперь вы можете запустить список командой /shop")
    else:
        bot.reply_to(message, "Не удалось распознать позиции. Формат: `Товар (Магазин) (Цена)`", parse_mode="Markdown")

if __name__ == "__main__":
    bot.infinity_polling()