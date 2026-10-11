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
from gigachat import GigaChat
from gigachat.models import ChatCompletionRequest, ChatMessage

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
GIGACHAT_CREDENTIALS = os.getenv("MDFhMDhjODctNzJlOS03ZTM1LTkyZDUtMzQ2NWEzNzg4MzAxOmY3YmQyODM4LWJlOTItNGYzOC1hOGZjLTM1YTU2ZjE2YTgzMg==")
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
                deposit INTEGER DEFAULT 0,
                deferred INTEGER DEFAULT 0,
                daily_kcal INTEGER DEFAULT 0
            )
        ''')
        try:
            conn.execute("ALTER TABLE users ADD COLUMN deposit INTEGER DEFAULT 0")
            conn.execute("ALTER TABLE users ADD COLUMN deferred INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

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
            return {"chat_id": chat_id, "savings": 0, "deposit": 0, "deferred": 0, "daily_kcal": 0}
        return user

def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("🛍 Каталог"),
        types.KeyboardButton("🛒 Корзина")
    )
    markup.add(
        types.KeyboardButton("📊 Профиль"),
        types.KeyboardButton("🧾 История")
    )
    return markup

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    get_user(message.chat.id)
    text = (
        "🍽 <b>Умный помощник @DishAndCheckBot</b>\n\n"
        "<blockquote><b>Как добавлять товары:</b>\n"
        "📝 Текстом: <code>Молоко Магнит 80</code>\n"
        "📸 Чеком: Отправьте фото, нейросеть всё распознает.</blockquote>\n\n"
        "<b>Команды управления:</b>\n"
        "💳 <code>/budget [сумма]</code> — задать бюджет похода\n"
        "🔥 <code>/calc [М/Ж] [вес] [рост] [возраст]</code> — расчет КБЖУ\n\n"
        "<i>Используйте нижнее меню для навигации.</i>"
    )
    bot.reply_to(message, text, parse_mode="HTML", reply_markup=get_main_menu())

@bot.message_handler(func=lambda message: message.text in ["🛍 Каталог", "🛒 Корзина", "🧾 История", "📊 Профиль"])
def handle_menu(message):
    if message.text == "🛍 Каталог":
        show_catalog(message)
    elif message.text == "🛒 Корзина":
        start_shopping(message)
    elif message.text == "🧾 История":
        show_history(message)
    elif message.text == "📊 Профиль":
        show_profile(message)

# --- ПРОФИЛЬ И ФИНАНСЫ ---
def show_profile(message):
    user = get_user(message.chat.id)
    text = (
        "📊 <b>Ваш финансовый и ЗОЖ профиль</b>\n\n"
        "<blockquote><b>💼 Капитал (Экономия):</b>\n"
        f"📈 На вкладе: <b>{user['deposit']} руб.</b>\n"
        f"⏳ Отложенные платежи: <b>{user['deferred']} руб.</b>\n"
        f"<i>Свободные сбережения: {user['savings']} руб.</i></blockquote>\n\n"
        "<blockquote><b>🔥 Здоровье:</b>\n"
        f"Суточная норма: <b>{user['daily_kcal'] if user['daily_kcal'] > 0 else 'Не задана'} ккал</b></blockquote>\n\n"
        "<i>Настроить бюджет на поход:</i> <code>/budget [сумма]</code>"
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
        
        bmr = (10 * weight) + (6.25 * height) - (5 * age) + (5 if gender == 'М' else -161)
        daily_kcal = int(bmr * 1.2)
        
        with get_db() as conn:
            conn.execute("UPDATE users SET daily_kcal = ? WHERE chat_id = ?", (daily_kcal, chat_id))
            conn.commit()
            
        bot.reply_to(message, f"✅ Ваша норма: <b>{daily_kcal} ккал</b>/день. Сохранено в профиль!", parse_mode="HTML")
    except Exception:
        bot.reply_to(message, "⚠️ Ошибка формата. Пример: <code>/calc М 85 180 30</code>", parse_mode="HTML")

# --- КАТАЛОГ С СОРТИРОВКОЙ ПО МАГАЗИНАМ ---
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

        # Сортируем каталог по магазинам, а затем по новизне
        total_items = conn.execute("SELECT count(*) as c FROM catalog WHERE chat_id = ?", (chat_id,)).fetchone()['c']
        total_pages = max(1, math.ceil(total_items / ITEMS_PER_PAGE))
        page = max(1, min(page, total_pages))
        offset = (page - 1) * ITEMS_PER_PAGE

        catalog_items = conn.execute("SELECT * FROM catalog WHERE chat_id = ? ORDER BY shop ASC, id DESC LIMIT ? OFFSET ?", (chat_id, ITEMS_PER_PAGE, offset)).fetchall()
        trip_items = conn.execute("SELECT name, shop FROM trip_items WHERE trip_id = ?", (trip_id,)).fetchall()
        
    if not catalog_items:
        return "📭 Каталог пуст. Отправьте список товаров текстом или фото чека.", None

    trip_items_set = {(item['name'], item['shop']) for item in trip_items}
    text = f"🛍 <b>Каталог товаров (по магазинам)</b> <i>(Стр {page}/{total_pages})</i>\nНажмите на товар, чтобы добавить его в корзину:"
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    for cat_item in catalog_items:
        is_selected = (cat_item['name'], cat_item['shop']) in trip_items_set
        icon = "✅" if is_selected else "➕"
        btn_text = f"{icon} [{cat_item['shop']}] {cat_item['name']} — {cat_item['price']}р"
        markup.add(types.InlineKeyboardButton(text=btn_text, callback_data=f"cat_{cat_item['id']}_{trip_id}_{page}"))

    nav_buttons = []
    if page > 1:
        nav_buttons.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"page_{page-1}"))
    if page < total_pages:
        nav_buttons.append(types.InlineKeyboardButton(text="Вперед ➡️", callback_data=f"page_{page+1}"))
    if nav_buttons:
        markup.row(*nav_buttons)

    markup.add(types.InlineKeyboardButton(text="🛒 Перейти к покупкам", callback_data="go_to_cart"))
    return text, markup

def show_catalog(message, page=1):
    text, markup = generate_catalog_content(message.chat.id, page)
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML") if markup else bot.send_message(message.chat.id, text, parse_mode="HTML")

# --- КОРЗИНА ---
def start_shopping(message):
    chat_id = message.chat.id
    with get_db() as conn:
        trip = conn.execute("SELECT * FROM trips WHERE chat_id = ? AND status = 'active'", (chat_id,)).fetchone()
        if not trip:
            bot.reply_to(message, "Ваша корзина пуста. Откройте «🛍 Каталог».")
            return
    send_trip_message(chat_id, trip['id'])

def generate_trip_content(trip_id):
    with get_db() as conn:
        trip = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
        # Сортируем товары в корзине по магазинам
        items = conn.execute("SELECT * FROM trip_items WHERE trip_id = ? ORDER BY shop ASC, id DESC", (trip_id,)).fetchall()

    shop_totals = {}
    grand_total, grand_calories, items_bought = 0, 0, 0
    
    for item in items:
        shop = item['shop']
        if shop not in shop_totals:
            shop_totals[shop] = {'sum': 0, 'items': []}
        
        shop_totals[shop]['items'].append(item)
        if item['status'] == 'bought':
            grand_total += item['price']
            grand_calories += item['calories']
            shop_totals[shop]['sum'] += item['price']
            items_bought += 1

    body = f"🛒 <b>Корзина на сегодня</b> <i>({items_bought}/{len(items)})</i>\n\n"
    
    if not items:
        body += "<i>Корзина пуста. Добавьте товары из каталога.</i>"
    else:
        for shop, data in shop_totals.items():
            body += f"🏪 <b>{html.escape(shop)}</b> <i>(Куплено на {data['sum']} р)</i>:\n<blockquote>"
            for item in data['items']:
                safe_name = html.escape(item['name'])
                icon = "🟢" if item['status'] == 'bought' else "⚪️"
                tag = f"<s>{safe_name}</s>" if item['status'] == 'bought' else safe_name
                body += f"{icon} {tag} — <b>{item['price']}р</b>\n"
            body += "</blockquote>\n"
    
    body += f"💳 <b>Сумма чека:</b> {grand_total} руб.\n"
    
    if trip['budget'] > 0:
        if grand_total > trip['budget']:
            body += f"⚠️ <b>Превышение бюджета:</b> +{grand_total - trip['budget']} руб."
        else:
            body += f"💚 <b>Экономия:</b> {trip['budget'] - grand_total} руб. из {trip['budget']} руб."

    markup = types.InlineKeyboardMarkup(row_width=1)
    for item in items:
        status_symbol = "🟢" if item['status'] == 'bought' else "⚪️"
        markup.add(types.InlineKeyboardButton(text=f"{status_symbol} [{item['shop']}] {item['name']} — {item['price']}р", callback_data=f"toggle_{item['id']}_{trip_id}"))

    markup.add(types.InlineKeyboardButton(text="🏁 Сохранить чек и завершить", callback_data=f"finish_{trip_id}"))
    markup.add(types.InlineKeyboardButton(text="🗑 Очистить корзину", callback_data=f"clearcart_{trip_id}"))
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
        with get_db() as conn:
            cat_item = conn.execute("SELECT * FROM catalog WHERE id = ?", (int(cat_id),)).fetchone()
            trip_item = conn.execute("SELECT id FROM trip_items WHERE trip_id = ? AND name = ? AND shop = ?", (int(trip_id), cat_item['name'], cat_item['shop'])).fetchone()
            if trip_item:
                conn.execute("DELETE FROM trip_items WHERE id = ?", (trip_item['id'],))
            else:
                conn.execute("INSERT INTO trip_items (trip_id, name, shop, price, calories, status) VALUES (?, ?, ?, ?, ?, 'need')",
                             (int(trip_id), cat_item['name'], cat_item['shop'], cat_item['price'], cat_item['calories']))
            conn.commit()
        text, markup = generate_catalog_content(chat_id, int(page))
        bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=text, reply_markup=markup, parse_mode="HTML")
        bot.answer_callback_query(call.id)
        
    elif data == "go_to_cart":
        bot.delete_message(chat_id, call.message.message_id)
        start_shopping(call.message)
    
    elif data.startswith("toggle_"):
        _, item_id, trip_id = data.split("_")
        with get_db() as conn:
            item = conn.execute("SELECT * FROM trip_items WHERE id = ?", (int(item_id),)).fetchone()
            if item:
                next_status = 'bought' if item['status'] == 'need' else 'need'
                conn.execute("UPDATE trip_items SET status = ? WHERE id = ?", (next_status, int(item_id)))
                conn.commit()
        text, markup = generate_trip_content(int(trip_id))
        bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=text, reply_markup=markup, parse_mode="HTML")
        bot.answer_callback_query(call.id)

    elif data.startswith("clearcart_"):
        _, trip_id = data.split("_")
        with get_db() as conn:
            conn.execute("DELETE FROM trip_items WHERE trip_id = ?", (int(trip_id),))
            conn.commit()
        text, markup = generate_trip_content(int(trip_id))
        bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=text, reply_markup=markup, parse_mode="HTML")
        bot.answer_callback_query(call.id, "Корзина очищена!")

    elif data.startswith("finish_"):
        _, trip_id = data.split("_")
        with get_db() as conn:
            trip = conn.execute("SELECT budget FROM trips WHERE id = ?", (int(trip_id),)).fetchone()
            items = conn.execute("SELECT price FROM trip_items WHERE trip_id = ? AND status = 'bought'", (int(trip_id),)).fetchall()
            
            spent = sum(i['price'] for i in items)
            saved = max(0, trip['budget'] - spent) if trip['budget'] > 0 else 0
            
            conn.execute("UPDATE trips SET status = 'completed' WHERE id = ?", (int(trip_id),))
            if saved > 0:
                conn.execute("UPDATE users SET savings = savings + ? WHERE chat_id = ?", (saved, chat_id))
            conn.commit()
            
        if saved > 0:
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton(text="📈 На Вклад", callback_data=f"invest_deposit_{saved}"),
                types.InlineKeyboardButton(text="⏳ На Отложенные", callback_data=f"invest_deferred_{saved}")
            )
            bot.edit_message_text(f"🎉 <b>Закупка завершена!</b>\nВы сэкономили <b>{saved} руб.</b> Куда направим средства?", chat_id=chat_id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=markup)
        else:
            bot.edit_message_text("🎉 <b>Закупка завершена!</b> Чек сохранен в историю.", chat_id=chat_id, message_id=call.message.message_id, parse_mode="HTML")
        bot.answer_callback_query(call.id)

    elif data.startswith("invest_"):
        parts = data.split("_")
        action, amount = parts[1], int(parts[2])
        
        with get_db() as conn:
            if action == "deposit":
                conn.execute("UPDATE users SET deposit = deposit + ?, savings = savings - ? WHERE chat_id = ?", (amount, amount, chat_id))
                target = "Вклад"
            elif action == "deferred":
                conn.execute("UPDATE users SET deferred = deferred + ?, savings = savings - ? WHERE chat_id = ?", (amount, amount, chat_id))
                target = "Отложенные платежи"
            conn.commit()
            
        bot.edit_message_text(f"✅ Средства (<b>{amount} руб.</b>) успешно переведены в <b>{target}</b>.\nПроверьте 📊 Профиль.", chat_id=chat_id, message_id=call.message.message_id, parse_mode="HTML")

# --- ПАРСЕР И GIGACHAT ---
def process_items_text(chat_id, text):
    lines = text.strip().split("\n")
    added = 0
    with get_db() as conn:
        cursor = conn.cursor()
        for line in lines:
            line = line.strip()
            if not line: continue
            price, calories = 0, 0
            shop, name_parts = "Прочее", []

            for word in line.split():
                word_clean = re.sub(r'[^\w\.]', '', word).lower()
                if re.match(r'^\d+$', word_clean) and price == 0:
                    price = int(word_clean)
                elif "ккал" in word_clean or "кал" in word_clean:
                    num = re.search(r'\d+', word_clean)
                    if num: calories = int(num.group())
                elif word_clean.capitalize() in ["Чижик", "Магнит", "Пятерочка", "Ашан", "Перекресток"]:
                    shop = word_clean.capitalize()
                elif not re.match(r'^\d+$', word_clean):
                    name_parts.append(re.sub(r'[\(\)]+', '', word))
            
            if price == 0: continue
            
            name = " ".join(name_parts)
            name = re.sub(r'\s+г$', '', name, flags=re.IGNORECASE)
            name = name.strip() if name.strip() else "Товар"
            
            cursor.execute("INSERT INTO catalog (chat_id, name, shop, price, calories) VALUES (?, ?, ?, ?, ?)", (chat_id, name, shop, price, calories))
            added += 1
        conn.commit()
    return added

@bot.message_handler(content_types=['photo'])
def handle_receipt_photo(message):
    chat_id = message.chat.id
    if not GIGACHAT_CREDENTIALS:
        bot.send_message(chat_id, "⚠️ Добавьте токен GIGACHAT_CREDENTIALS в переменные сервера.")
        return

    msg = bot.reply_to(message, "📸 Чек получен. Запускаю AI-распознавание...")
    photo_path = f"receipt_{chat_id}.jpg"
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        with open(photo_path, 'wb') as new_file:
            new_file.write(bot.download_file(file_info.file_path))
            
        bot.edit_message_text("🧠 GigaChat анализирует позиции...", chat_id=chat_id, message_id=msg.message_id)
        
        with GigaChat(credentials=GIGACHAT_CREDENTIALS, verify_ssl_certs=False, timeout=30.0) as giga:
            with open(photo_path, 'rb') as f:
                uploaded = giga.upload_file(f, purpose="general")
            req = ChatCompletionRequest(
                model="GigaChat-Max", 
                messages=[ChatMessage(role="user", content="Распознай чек. Напиши каждый товар с новой строки: Название Цена. Если виден магазин (например Чижик или Магнит), добавь его в начало строки.", attachments=[uploaded.id])]
            )
            result_text = str(giga.chat.create(req).messages[0].content)
            
        added = process_items_text(chat_id, result_text)
        bot.edit_message_text(f"✅ <b>Распознано и добавлено:</b> {added} позиций.\nОткройте 🛍 Каталог.", chat_id=chat_id, message_id=msg.message_id, parse_mode="HTML")
        
    except Exception as e:
        bot.send_message(chat_id, f"❌ Ошибка нейросети: {e}")
    finally:
        if os.path.exists(photo_path): os.remove(photo_path)

@bot.message_handler(func=lambda message: True)
def parse_smart_items(message):
    if message.text.startswith('/'): return
    added = process_items_text(message.chat.id, message.text)
    if added > 0:
        bot.reply_to(message, f"✅ Добавлено: {added} шт. Перейдите в 🛍 Каталог.", parse_mode="HTML")
    else:
        bot.reply_to(message, "⚠️ Не найдены цены. Пишите в формате: Товар Цена", parse_mode="HTML")

@bot.message_handler(commands=['budget'])
def set_budget(message):
    try:
        val = int(message.text.split()[1])
        with get_db() as conn:
            conn.execute("UPDATE trips SET budget = ? WHERE chat_id = ? AND status = 'active'", (val, message.chat.id))
            conn.commit()
        bot.reply_to(message, f"💳 <b>Бюджет установлен:</b> {val} руб.", parse_mode="HTML")
    except Exception:
        bot.reply_to(message, "⚠️ Формат: /budget 2000")

@bot.message_handler(commands=['history'])
def show_history(message):
    with get_db() as conn:
        trips = conn.execute("SELECT * FROM trips WHERE chat_id = ? AND status = 'completed' ORDER BY id DESC LIMIT 3", (message.chat.id,)).fetchall()

    if not trips:
        bot.reply_to(message, "📭 История пуста.")
        return

    text = "🧾 <b>Последние покупки:</b>\n\n"
    for trip in trips:
        text += f"📅 <b>{trip['date']}</b>\n<blockquote>"
        items = get_db().execute("SELECT * FROM trip_items WHERE trip_id = ? AND status = 'bought'", (trip['id'],)).fetchall()
        for item in items:
            text += f"• {html.escape(item['name'])} — {item['price']}р\n"
        text += f"</blockquote>💰 Итог: <b>{sum(i['price'] for i in items)} руб.</b>\n\n"

    bot.reply_to(message, text, parse_mode="HTML")

if __name__ == "__main__":
    bot.infinity_polling()
