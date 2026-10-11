import telebot
from telebot import types
import sqlite3
import re
import os
import html
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# --- Фоновый веб-сервер для Bothost ---
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
# -------------------------------------

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

# --- Главное нижнее меню ---
def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("📋 Каталог (собрать список)"),
        types.KeyboardButton("🛒 Моя корзина")
    )
    markup.add(
        types.KeyboardButton("📜 История"),
        types.KeyboardButton("🗑 Сбросить корзину")
    )
    return markup

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    text = (
        "<b>🧾 Умный бот списка покупок и КБЖУ</b>\n\n"
        "<b>Как пополнить общий каталог:</b>\n"
        "Отправляйте товары текстом в формате:\n"
        "<code>Товар (Магазин) (Цена) (Калории)</code>\n"
        "<b>Пример:</b> <code>Творог (Чижик) (120) (450 ккал)</code>\n\n"
        "<b>Как это работает:</b>\n"
        "1. Добавьте товары текстом (они сохранятся в базу).\n"
        "2. Нажмите <b>📋 Каталог</b> в меню внизу и выберите товары на сегодня.\n"
        "3. Нажмите <b>🛒 Моя корзина</b>, чтобы пойти в магазин и отмечать купленное."
    )
    bot.reply_to(message, text, parse_mode="HTML", reply_markup=get_main_menu())

# --- Обработка кнопок нижнего меню ---
@bot.message_handler(func=lambda message: message.text in ["📋 Каталог (собрать список)", "🛒 Моя корзина", "📜 История", "🗑 Сбросить корзину"])
def handle_menu(message):
    if message.text == "📋 Каталог (собрать список)":
        show_catalog(message)
    elif message.text == "🛒 Моя корзина":
        start_shopping(message)
    elif message.text == "📜 История":
        show_history(message)
    elif message.text == "🗑 Сбросить корзину":
        clear_active_trip(message)

# --- Логика КАТАЛОГА (добавление из базы в список) ---
def generate_catalog_content(chat_id):
    with get_db() as conn:
        # Ищем или создаем активную корзину
        trip = conn.execute("SELECT * FROM trips WHERE chat_id = ? AND status = 'active'", (chat_id,)).fetchone()
        if not trip:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO trips (chat_id, date, status, budget, calorie_goal) VALUES (?, ?, 'active', 0, 0)", 
                           (chat_id, datetime.now().strftime("%d.%m.%Y %H:%M")))
            trip_id = cursor.lastrowid
            conn.commit()
        else:
            trip_id = trip['id']

        catalog_items = conn.execute("SELECT * FROM catalog WHERE chat_id = ?", (chat_id,)).fetchall()
        trip_items = conn.execute("SELECT name, shop FROM trip_items WHERE trip_id = ?", (trip_id,)).fetchall()
        
    if not catalog_items:
        return "Ваш общий список пуст. Отправьте товары текстом, чтобы пополнить его.", None

    # Создаем множество (set) из того, что уже добавлено в корзину для быстрого поиска
    trip_items_set = {(item['name'], item['shop']) for item in trip_items}

    text = "<b>📋 ОБЩИЙ КАТАЛОГ ТОВАРОВ</b>\nНажимайте на кнопки, чтобы добавить (+) или убрать (-) товар из текущей корзины на сегодня:"
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    for cat_item in catalog_items:
        is_selected = (cat_item['name'], cat_item['shop']) in trip_items_set
        icon = "✅" if is_selected else "➕"
        btn_text = f"{icon} {cat_item['name']} ({cat_item['shop']}) - {cat_item['price']}р"
        markup.add(types.InlineKeyboardButton(text=btn_text, callback_data=f"cat_{cat_item['id']}_{trip_id}"))

    markup.add(types.InlineKeyboardButton(text="➡️ Перейти в корзину", callback_data="go_to_cart"))
    return text, markup

def show_catalog(message):
    text, markup = generate_catalog_content(message.chat.id)
    if markup:
        bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, text, parse_mode="HTML")

def update_catalog_message(message):
    text, markup = generate_catalog_content(message.chat.id)
    try:
        bot.edit_message_text(chat_id=message.chat.id, message_id=message.message_id, text=text, reply_markup=markup, parse_mode="HTML")
    except Exception:
        pass # Игнорируем ошибку, если сообщение не изменилось

# --- Логика КОРЗИНЫ (отметка купленного) ---
@bot.message_handler(commands=['shop', 'list'])
def start_shopping(message):
    chat_id = message.chat.id
    with get_db() as conn:
        trip = conn.execute("SELECT * FROM trips WHERE chat_id = ? AND status = 'active'", (chat_id,)).fetchone()
        
        if not trip:
            bot.reply_to(message, "Ваша корзина пуста. Нажмите «📋 Каталог (собрать список)» в меню, чтобы добавить товары.")
            return
            
        items = conn.execute("SELECT count(*) as c FROM trip_items WHERE trip_id = ?", (trip['id'],)).fetchone()['c']
        if items == 0:
            bot.reply_to(message, "Вы не добавили ни одного товара в корзину. Зайдите в «📋 Каталог (собрать список)».")
            return
            
    send_trip_message(chat_id, trip['id'])

def generate_trip_content(trip_id):
    with get_db() as conn:
        trip = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
        items = conn.execute("SELECT * FROM trip_items WHERE trip_id = ?", (trip_id,)).fetchall()

    shops_dict = {}
    shop_totals = {}
    grand_total = 0
    grand_calories = 0
    
    for item in items:
        shop = item['shop']
        if shop not in shops_dict:
            shops_dict[shop] = []
            shop_totals[shop] = 0
        shops_dict[shop].append(item)
        
        if item['status'] == 'bought':
            grand_total += item['price']
            grand_calories += item['calories']
            shop_totals[shop] += item['price']

    body = "<b>🛒 МОЯ КОРЗИНА НА СЕГОДНЯ</b>\n\n"
    
    if shop_totals:
        body += "<b>📊 Расходы по магазинам:</b>\n"
        for shop, total_sum in shop_totals.items():
            body += f"• {html.escape(shop)}: {total_sum} руб.\n"
        body += "\n"

    for shop, shop_items in shops_dict.items():
        body += f"<b>🏬 {html.escape(shop)}:</b>\n"
        for item in shop_items:
            kcal_str = f" <i>[{item['calories']} ккал]</i>" if item['calories'] > 0 else ""
            safe_name = html.escape(item['name'])
            if item['status'] == 'bought':
                icon = "✅"
                line = f"<s>{safe_name}</s> — {item['price']} руб.{kcal_str}"
            elif item['status'] == 'skipped':
                icon = "❌"
                line = f"<s>{safe_name}</s> <i>(исключено)</i>"
            else:
                icon = "🛒"
                line = f"{safe_name} — {item['price']} руб.{kcal_str}"
            body += f"  {icon} {line}\n"
        body += "\n"
    
    body += f"<b>💰 Итого куплено:</b> {grand_total} руб. | <b>🔥 Калории:</b> {grand_calories} ккал"
    
    if trip['calorie_goal'] > 0:
        body += f"\n🎯 Цель КБЖУ: {trip['calorie_goal']} ккал"
    if trip['budget'] > 0:
        body += f"\n🎯 Лимит бюджета: {trip['budget']} руб."
        if grand_total > trip['budget']:
            body += "\n⚠️ <b>Внимание: бюджет превышен!</b>"

    markup = types.InlineKeyboardMarkup(row_width=1)
    for item in items:
        status_symbol = "🛒"
        if item['status'] == 'bought':
            status_symbol = "✅"
        elif item['status'] == 'skipped':
            status_symbol = "❌"
            
        btn_text = f"{status_symbol} {item['name']} ({item['shop']}) — {item['price']}р"
        markup.add(types.InlineKeyboardButton(text=btn_text, callback_data=f"toggle_{item['id']}_{trip_id}"))

    markup.add(types.InlineKeyboardButton(text="📤 Экспортировать для близких", callback_data=f"export_{trip_id}"))
    markup.add(types.InlineKeyboardButton(text="🏁 Завершить закупку (в архив)", callback_data=f"finish_{trip_id}"))
    
    return body, markup

def send_trip_message(chat_id, trip_id):
    text, markup = generate_trip_content(trip_id)
    try:
        if len(text) > 4000:
            parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
            for idx, part in enumerate(parts):
                if idx == len(parts) - 1:
                    bot.send_message(chat_id, part, reply_markup=markup, parse_mode="HTML")
                else:
                    bot.send_message(chat_id, part, parse_mode="HTML")
        else:
            bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")
    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Ошибка отображения списка: {e}")

def update_trip_message(message, trip_id):
    text, markup = generate_trip_content(trip_id)
    try:
        if len(text) > 4000:
            bot.edit_message_text(chat_id=message.chat.id, message_id=message.message_id, text="⚠️ Список слишком длинный для обновления.", parse_mode="HTML")
            send_trip_message(message.chat.id, trip_id)
        else:
            bot.edit_message_text(chat_id=message.chat.id, message_id=message.message_id, text=text, reply_markup=markup, parse_mode="HTML")
    except Exception:
        pass

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    data = call.data
    chat_id = call.message.chat.id
    
    # Обработка нажатий в КАТАЛОГЕ
    if data.startswith("cat_"):
        _, cat_id, trip_id = data.split("_")
        cat_id, trip_id = int(cat_id), int(trip_id)
        
        with get_db() as conn:
            cat_item = conn.execute("SELECT * FROM catalog WHERE id = ?", (cat_id,)).fetchone()
            # Проверяем, есть ли уже этот товар в корзине
            trip_item = conn.execute("SELECT id FROM trip_items WHERE trip_id = ? AND name = ? AND shop = ?", 
                                     (trip_id, cat_item['name'], cat_item['shop'])).fetchone()
            
            if trip_item:
                # Если есть — удаляем из корзины
                conn.execute("DELETE FROM trip_items WHERE id = ?", (trip_item['id'],))
            else:
                # Если нет — добавляем
                conn.execute("INSERT INTO trip_items (trip_id, name, shop, price, calories, status) VALUES (?, ?, ?, ?, ?, 'need')",
                             (trip_id, cat_item['name'], cat_item['shop'], cat_item['price'], cat_item['calories']))
            conn.commit()
            
        update_catalog_message(call.message)
        bot.answer_callback_query(call.id)
        
    # Кнопка перехода из каталога в корзину
    elif data == "go_to_cart":
        bot.answer_callback_query(call.id)
        bot.delete_message(chat_id, call.message.message_id) # Убираем каталог
        start_shopping(call.message) # Открываем корзину
    
    # Обработка нажатий в КОРЗИНЕ (куплено/не куплено)
    elif data.startswith("toggle_"):
        _, item_id, trip_id = data.split("_")
        item_id, trip_id = int(item_id), int(trip_id)
        
        with get_db() as conn:
            item = conn.execute("SELECT * FROM trip_items WHERE id = ?", (item_id,)).fetchone()
            if item:
                current = item['status']
                next_status = 'bought' if current == 'need' else ('skipped' if current == 'bought' else 'need')
                conn.execute("UPDATE trip_items SET status = ? WHERE id = ?", (next_status, item_id))
                conn.commit()
                
        update_trip_message(call.message, trip_id)
        bot.answer_callback_query(call.id)
        
    elif data.startswith("export_"):
        _, trip_id = data.split("_")
        trip_id = int(trip_id)
        
        with get_db() as conn:
            items = conn.execute("SELECT * FROM trip_items WHERE trip_id = ? AND status != 'skipped'", (trip_id,)).fetchall()
            
        export_text = "<b>📋 Мой список покупок и рацион:</b>\n\n"
        for item in items:
            kcal_str = f" ({item['calories']} ккал)" if item['calories'] > 0 else ""
            export_text += f"• {html.escape(item['name'])} ({html.escape(item['shop'])}) — {item['price']} руб.{kcal_str}\n"
            
        bot.send_message(chat_id, export_text, parse_mode="HTML")
        bot.answer_callback_query(call.id, "Список для экспорта готов!")

    elif data.startswith("finish_"):
        _, trip_id = data.split("_")
        trip_id = int(trip_id)
        
        with get_db() as conn:
            conn.execute("UPDATE trips SET status = 'completed' WHERE id = ?", (trip_id,))
            conn.commit()
            
        bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text="🎉 Закупка завершена и сохранена в архив!", parse_mode="HTML")
        bot.answer_callback_query(call.id, "Успешно сохранено в архив!")

# --- Остальные команды (бюджет, цели, история, очистка) ---
@bot.message_handler(commands=['goal'])
def set_calorie_goal(message):
    chat_id = message.chat.id
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(message, "Укажите цель по калориям числом. Пример: <code>/goal 1800</code>", parse_mode="HTML")
        return
    
    goal_val = int(parts[1])
    with get_db() as conn:
        conn.execute("UPDATE trips SET calorie_goal = ? WHERE chat_id = ? AND status = 'active'", (goal_val, chat_id))
        conn.commit()
    bot.reply_to(message, f"🎯 Цель по калориям на текущий рацион установлена: {goal_val} ккал.")

@bot.message_handler(commands=['budget'])
def set_budget(message):
    chat_id = message.chat.id
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(message, "Укажите сумму бюджета числом. Пример: <code>/budget 2500</code>", parse_mode="HTML")
        return
    
    budget_val = int(parts[1])
    with get_db() as conn:
        conn.execute("UPDATE trips SET budget = ? WHERE chat_id = ? AND status = 'active'", (budget_val, chat_id))
        conn.commit()
    bot.reply_to(message, f"🎯 Лимит бюджета на текущий поход установлен: {budget_val} руб.")

@bot.message_handler(commands=['clear'])
def clear_active_trip(message):
    chat_id = message.chat.id
    with get_db() as conn:
        conn.execute("UPDATE trips SET status = 'cancelled' WHERE chat_id = ? AND status = 'active'", (chat_id,))
        conn.commit()
    bot.reply_to(message, "🗑 Текущий активный список сброшен. Товары остались в общем каталоге.", reply_markup=get_main_menu())

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

    text = "<b>📜 Архив прошлых покупок:</b>\n\n"
    for trip in trips:
        text += f"📅 <b>Закупка от {trip['date']}</b>\n"
        items = get_db().execute("SELECT * FROM trip_items WHERE trip_id = ? AND status = 'bought'", (trip['id'],)).fetchall()
        trip_total = sum(i['price'] for i in items)
        trip_kcal = sum(i['calories'] for i in items)
        for item in items:
            kcal_str = f" ({item['calories']} ккал)" if item['calories'] > 0 else ""
            text += f"  • {html.escape(item['name'])} ({html.escape(item['shop'])}) — {item['price']} руб.{kcal_str}\n"
        text += f"  💰 Итого: {trip_total} руб. | 🔥 Калорий: {trip_kcal} ккал\n\n"

    bot.reply_to(message, text, parse_mode="HTML")

@bot.message_handler(content_types=['photo'])
def handle_receipt_photo(message):
    chat_id = message.chat.id
    bot.reply_to(message, "📷 Фото чека получено! Скачиваю и обрабатываю изображение...")
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        photo_path = f"receipt_{chat_id}.jpg"
        with open(photo_path, 'wb') as new_file:
            new_file.write(downloaded_file)
            
        bot.send_message(chat_id, "✅ Чек успешно загружен и сохранен в системе.")
        
        if os.path.exists(photo_path):
            os.remove(photo_path)
    except Exception as e:
        bot.send_message(chat_id, f"❌ Не удалось обработать фото чека: {e}")

# --- Добавление новых товаров (Парсинг текста) ---
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
            calories = 0

            for match in matches:
                m_lower = match.lower()
                clean_num = re.search(r"\d+", match)
                if clean_num:
                    val = int(clean_num.group())
                    if "ккал" in m_lower or "кал" in m_lower or "kcal" in m_lower:
                        calories = val
                    elif any(x in m_lower for x in ["руб", "р", "$", "₽"]):
                        price = val
                    else:
                        if price == 0:
                            price = val
                        elif calories == 0:
                            calories = val
                else:
                    shop = match.strip().capitalize()

            if name:
                cursor.execute(
                    "INSERT INTO catalog (chat_id, name, shop, price, calories) VALUES (?, ?, ?, ?, ?)",
                    (chat_id, name, shop, price, calories)
                )
                added += 1
        conn.commit()

    if added > 0:
        bot.reply_to(message, f"✅ Сохранено в базу: {added} шт.\n\nНажмите <b>📋 Каталог (собрать список)</b> в нижнем меню, чтобы добавить их в корзину на сегодня.", parse_mode="HTML")
    else:
        bot.reply_to(message, "Не удалось распознать позиции. Формат: <code>Товар (Магазин) (Цена) (Калории)</code>", parse_mode="HTML")

if __name__ == "__main__":
    bot.infinity_polling()
