import telebot
from telebot import types
import sqlite3
import re
import os
import html
import threading
import math
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# --- Фоновый веб-сервер ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")
    def log_message(self, format, *args):
        pass

def run_health_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()
# --------------------------

TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)
DB_FILE = "shopping.db"
ITEMS_PER_PAGE = 7

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                savings INTEGER DEFAULT 0,
                daily_kcal INTEGER DEFAULT 0
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS catalog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                name TEXT,
                shop TEXT,
                price INTEGER,
                calories INTEGER DEFAULT 0
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS trips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                date TEXT,
                status TEXT,
                budget INTEGER DEFAULT 0,
                calorie_goal INTEGER DEFAULT 0
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS trip_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trip_id INTEGER,
                name TEXT,
                shop TEXT,
                price INTEGER,
                calories INTEGER DEFAULT 0,
                status TEXT DEFAULT 'need'
            )
        ''')
        conn.commit()

init_db()

def get_user(chat_id):
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
        if not user:
            conn.execute("INSERT INTO users (chat_id) VALUES (?)", (chat_id,))
            conn.commit()
            return {"chat_id": chat_id, "savings": 0, "daily_kcal": 0}
        return user

def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("📋 Каталог"),
        types.KeyboardButton("🛒 Моя корзина")
    )
    markup.add(
        types.KeyboardButton("👤 Профиль"),
        types.KeyboardButton("📜 История")
    )
    return markup

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    get_user(message.chat.id)
    text = (
        "<b>🧾 Умный бот списка покупок</b>\n\n"
        "<b>Добавление товаров:</b>\n"
        "Пишите текстом (можно без скобок):\n"
        "<code>Молоко Магнит 80 50ккал</code>\n"
        "<code>Творог 120</code> (магазин и калории авто-пропустятся)\n\n"
        "<b>Расчет КБЖУ:</b>\n"
        "Используйте команду: <code>/calc [М/Ж] [вес] [рост] [возраст]</code>\n"
        "Пример: <code>/calc М 85 180 30</code>"
    )
    bot.reply_to(message, text, parse_mode="HTML", reply_markup=get_main_menu())

@bot.message_handler(func=lambda message: message.text in ["📋 Каталог", "🛒 Моя корзина", "📜 История", "👤 Профиль"])
def handle_menu(message):
    if message.text == "📋 Каталог":
        show_catalog(message)
    elif message.text == "🛒 Моя корзина":
        start_shopping(message)
    elif message.text == "📜 История":
        show_history(message)
    elif message.text == "👤 Профиль":
        show_profile(message)

# --- ПРОФИЛЬ И КОПИЛКА ---
def show_profile(message):
    user = get_user(message.chat.id)
    text = (
        "<b>👤 Ваш профиль</b>\n\n"
        f"💰 <b>Копилка (сэкономлено):</b> {user['savings']} руб.\n"
        f"🔥 <b>Суточная норма:</b> {user['daily_kcal'] if user['daily_kcal'] > 0 else 'Не задана'} ккал\n\n"
        "<i>Настроить бюджет на поход:</i> <code>/budget [сумма]</code>\n"
        "<i>Рассчитать норму калорий:</i> <code>/calc [М/Ж] [вес] [рост] [возраст]</code>\n"
        "<i>Задать цель вручную:</i> <code>/goal [ккал]</code>\n\n"
        "💡 <i>Сэкономленные средства (разница между бюджетом и тратами) автоматически идут в Копилку при завершении похода.</i>"
    )
    bot.reply_to(message, text, parse_mode="HTML")

@bot.message_handler(commands=['calc'])
def calculate_calories(message):
    chat_id = message.chat.id
    parts = message.text.split()
    try:
        gender = parts[1].upper()
        weight = float(parts[2])
        height = float(parts[3])
        age = int(parts[4])
        
        if gender not in ['М', 'Ж']:
            raise ValueError
            
        # Формула Миффлина-Сан Жеора (базовый метаболизм + легкая активность)
        if gender == 'М':
            bmr = (10 * weight) + (6.25 * height) - (5 * age) + 5
        else:
            bmr = (10 * weight) + (6.25 * height) - (5 * age) - 161
            
        daily_kcal = int(bmr * 1.2) # Коэффициент минимальной активности
        
        with get_db() as conn:
            conn.execute("UPDATE users SET daily_kcal = ? WHERE chat_id = ?", (daily_kcal, chat_id))
            conn.commit()
            
        bot.reply_to(message, f"✅ Ваша примерная норма: <b>{daily_kcal} ккал</b>/день.\nЗначение сохранено в профиль!", parse_mode="HTML")
    except Exception:
        bot.reply_to(message, "⚠️ Ошибка формата. Пример: <code>/calc М 85 180 30</code>", parse_mode="HTML")

# --- ПАГИНАЦИЯ КАТАЛОГА ---
def generate_catalog_content(chat_id, page=1):
    with get_db() as conn:
        trip = conn.execute("SELECT * FROM trips WHERE chat_id = ? AND status = 'active'", (chat_id,)).fetchone()
        if not trip:
            user = get_user(chat_id)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO trips (chat_id, date, status, budget, calorie_goal) VALUES (?, ?, 'active', 0, ?)", 
                           (chat_id, datetime.now().strftime("%d.%m.%Y %H:%M"), user['daily_kcal']))
            trip_id = cursor.lastrowid
            conn.commit()
        else:
            trip_id = trip['id']

        total_items = conn.execute("SELECT count(*) as c FROM catalog WHERE chat_id = ?", (chat_id,)).fetchone()['c']
        total_pages = max(1, math.ceil(total_items / ITEMS_PER_PAGE))
        page = max(1, min(page, total_pages)) # Защита от выхода за границы
        offset = (page - 1) * ITEMS_PER_PAGE

        catalog_items = conn.execute("SELECT * FROM catalog WHERE chat_id = ? LIMIT ? OFFSET ?", (chat_id, ITEMS_PER_PAGE, offset)).fetchall()
        trip_items = conn.execute("SELECT name, shop FROM trip_items WHERE trip_id = ?", (trip_id,)).fetchall()
        
    if not catalog_items:
        return "Каталог пуст. Напишите товары в чат.", None

    trip_items_set = {(item['name'], item['shop']) for item in trip_items}

    text = f"<b>📋 КАТАЛОГ (Стр {page}/{total_pages})</b>\nДобавляйте (+) товары в корзину:"
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    for cat_item in catalog_items:
        is_selected = (cat_item['name'], cat_item['shop']) in trip_items_set
        icon = "✅" if is_selected else "➕"
        btn_text = f"{icon} {cat_item['name']} - {cat_item['price']}р"
        markup.add(types.InlineKeyboardButton(text=btn_text, callback_data=f"cat_{cat_item['id']}_{trip_id}_{page}"))

    # Кнопки пагинации
    nav_buttons = []
    if page > 1:
        nav_buttons.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"page_{page-1}"))
    if page < total_pages:
        nav_buttons.append(types.InlineKeyboardButton(text="Вперед ➡️", callback_data=f"page_{page+1}"))
    if nav_buttons:
        markup.row(*nav_buttons)

    markup.add(types.InlineKeyboardButton(text="➡️ Перейти в корзину", callback_data="go_to_cart"))
    return text, markup

def show_catalog(message, page=1):
    text, markup = generate_catalog_content(message.chat.id, page)
    if markup:
        bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, text, parse_mode="HTML")

# --- КОРЗИНА И ЗАВЕРШЕНИЕ ПОХОДА ---
def start_shopping(message):
    chat_id = message.chat.id
    with get_db() as conn:
        trip = conn.execute("SELECT * FROM trips WHERE chat_id = ? AND status = 'active'", (chat_id,)).fetchone()
        if not trip:
            bot.reply_to(message, "Ваша корзина пуста. Откройте «📋 Каталог».")
            return
    send_trip_message(chat_id, trip['id'])

def generate_trip_content(trip_id):
    with get_db() as conn:
        trip = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
        items = conn.execute("SELECT * FROM trip_items WHERE trip_id = ?", (trip_id,)).fetchall()

    shop_totals = {}
    grand_total = 0
    grand_calories = 0
    
    body = "<b>🛒 МОЯ КОРЗИНА НА СЕГОДНЯ</b>\n\n"
    
    for item in items:
        shop = item['shop']
        if shop not in shop_totals:
            shop_totals[shop] = {'sum': 0, 'items': []}
        
        shop_totals[shop]['items'].append(item)
        if item['status'] == 'bought':
            grand_total += item['price']
            grand_calories += item['calories']
            shop_totals[shop]['sum'] += item['price']

    for shop, data in shop_totals.items():
        body += f"<b>🏬 {html.escape(shop)}</b> (Куплено на {data['sum']} р):\n"
        for item in data['items']:
            safe_name = html.escape(item['name'])
            icon = "✅" if item['status'] == 'bought' else ("❌" if item['status'] == 'skipped' else "🛒")
            tag = f"<s>{safe_name}</s>" if item['status'] in ['bought', 'skipped'] else safe_name
            body += f"  {icon} {tag} — {item['price']}р\n"
        body += "\n"
    
    body += f"<b>💰 Итого:</b> {grand_total} руб. | <b>🔥 Калории:</b> {grand_calories} ккал"
    
    if trip['calorie_goal'] > 0:
        body += f"\n🎯 Норма: {trip['calorie_goal']} ккал"
    if trip['budget'] > 0:
        body += f"\n🎯 Бюджет: {trip['budget']} руб."
        if grand_total > trip['budget']:
            body += "\n⚠️ <b>Бюджет превышен!</b>"
        else:
            body += f"\n💚 Экономия: {trip['budget'] - grand_total} руб."

    markup = types.InlineKeyboardMarkup(row_width=1)
    for item in items:
        status_symbol = "✅" if item['status'] == 'bought' else "🛒"
        markup.add(types.InlineKeyboardButton(text=f"{status_symbol} {item['name']} — {item['price']}р", callback_data=f"toggle_{item['id']}_{trip_id}"))

    markup.add(types.InlineKeyboardButton(text="🏁 Завершить (в архив + копилка)", callback_data=f"finish_{trip_id}"))
    return body, markup

def send_trip_message(chat_id, trip_id):
    text, markup = generate_trip_content(trip_id)
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")

# --- КОЛБЕКИ ---
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    data = call.data
    chat_id = call.message.chat.id
    
    if data.startswith("page_"):
        page = int(data.split("_")[1])
        text, markup = generate_catalog_content(chat_id, page)
        bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=text, reply_markup=markup, parse_mode="HTML")
        bot.answer_callback_query(call.id)

    elif data.startswith("cat_"):
        _, cat_id, trip_id, page = data.split("_")
        cat_id, trip_id, page = int(cat_id), int(trip_id), int(page)
        
        with get_db() as conn:
            cat_item = conn.execute("SELECT * FROM catalog WHERE id = ?", (cat_id,)).fetchone()
            trip_item = conn.execute("SELECT id FROM trip_items WHERE trip_id = ? AND name = ? AND shop = ?", (trip_id, cat_item['name'], cat_item['shop'])).fetchone()
            
            if trip_item:
                conn.execute("DELETE FROM trip_items WHERE id = ?", (trip_item['id'],))
            else:
                conn.execute("INSERT INTO trip_items (trip_id, name, shop, price, calories, status) VALUES (?, ?, ?, ?, ?, 'need')",
                             (trip_id, cat_item['name'], cat_item['shop'], cat_item['price'], cat_item['calories']))
            conn.commit()
            
        text, markup = generate_catalog_content(chat_id, page)
        bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=text, reply_markup=markup, parse_mode="HTML")
        bot.answer_callback_query(call.id)
        
    elif data == "go_to_cart":
        bot.delete_message(chat_id, call.message.message_id)
        start_shopping(call.message)
    
    elif data.startswith("toggle_"):
        _, item_id, trip_id = data.split("_")
        
        with get_db() as conn:
            item = conn.execute("SELECT * FROM trip_items WHERE id = ?", (item_id,)).fetchone()
            if item:
                next_status = 'bought' if item['status'] == 'need' else 'need'
                conn.execute("UPDATE trip_items SET status = ? WHERE id = ?", (next_status, item_id))
                conn.commit()
                
        text, markup = generate_trip_content(trip_id)
        bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=text, reply_markup=markup, parse_mode="HTML")
        bot.answer_callback_query(call.id)

    elif data.startswith("finish_"):
        _, trip_id = data.split("_")
        
        with get_db() as conn:
            trip = conn.execute("SELECT budget FROM trips WHERE id = ?", (trip_id,)).fetchone()
            items = conn.execute("SELECT price FROM trip_items WHERE trip_id = ? AND status = 'bought'", (trip_id,)).fetchall()
            
            spent = sum(i['price'] for i in items)
            saved = max(0, trip['budget'] - spent) if trip['budget'] > 0 else 0
            
            conn.execute("UPDATE trips SET status = 'completed' WHERE id = ?", (trip_id,))
            if saved > 0:
                conn.execute("UPDATE users SET savings = savings + ? WHERE chat_id = ?", (saved, chat_id))
            conn.commit()
            
        msg = "🎉 Закупка завершена!"
        if saved > 0:
            msg += f"\n💰 В Копилку добавлено сэкономленных: {saved} руб."
        
        bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=msg, parse_mode="HTML")
        bot.answer_callback_query(call.id)

# --- УМНЫЙ ПАРСИНГ ТЕКСТА ---
@bot.message_handler(func=lambda message: True)
def parse_smart_items(message):
    chat_id = message.chat.id
    lines = message.text.strip().split("\n")
    added = 0

    with get_db() as conn:
        cursor = conn.cursor()
        for line in lines:
            line = line.strip()
            if not line: continue

            price = 0
            calories = 0
            shop = "Прочее"
            name_parts = []

            words = line.split()
            for word in words:
                word_clean = re.sub(r'[^\w\.]', '', word).lower()
                
                if re.match(r'^\d+$', word_clean) and price == 0:
                    price = int(word_clean)
                elif "ккал" in word_clean or "кал" in word_clean:
                    num = re.search(r'\d+', word_clean)
                    if num: calories = int(num.group())
                elif word_clean.capitalize() in ["Чижик", "Магнит", "Пятерочка", "Ашан", "Перекресток"]:
                    shop = word.capitalize()
                else:
                    if not re.match(r'^\d+$', word_clean): # Не добавляем числа в название, если это не бренд
                        name_parts.append(word)

            name = " ".join(name_parts) if name_parts else "Неизвестно"
            
            cursor.execute(
                "INSERT INTO catalog (chat_id, name, shop, price, calories) VALUES (?, ?, ?, ?, ?)",
                (chat_id, name, shop, price, calories)
            )
            added += 1
        conn.commit()

    if added > 0:
        bot.reply_to(message, f"✅ Сохранено в базу: {added} шт.\nОткройте <b>📋 Каталог</b> для сборки корзины.", parse_mode="HTML")

@bot.message_handler(commands=['budget'])
def set_budget(message):
    try:
        val = int(message.text.split()[1])
        with get_db() as conn:
            conn.execute("UPDATE trips SET budget = ? WHERE chat_id = ? AND status = 'active'", (val, message.chat.id))
            conn.commit()
        bot.reply_to(message, f"🎯 Бюджет {val} руб. установлен.")
    except Exception:
        bot.reply_to(message, "Ошибка. Формат: /budget 2000")

if __name__ == "__main__":
    bot.infinity_polling()
