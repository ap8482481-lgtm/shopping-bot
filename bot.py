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

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    text = (
        "<b>🧾 Умный бот списка покупок и КБЖУ</b>\n\n"
        "1. Отправляйте товары в формате:\n"
        "<code>Товар (Магазин) (Цена) (Калории)</code>\n"
        "<b>Пример:</b> <code>Творог (Чижик) (120) (450 ккал)</code>\n\n"
        "<b>Команды:</b>\n"
        "/shop — открыть текущий список покупок\n"
        "/goal [ккал] — задать цель по калориям (например, <code>/goal 1800</code>)\n"
        "/gen_menu — автогенерация рациона под вашу цель\n"
        "/budget [сумма] — установить лимит бюджета\n"
        "/history — архив прошлых покупок\n"
        "/clear — сбросить текущий список\n\n"
        "📷 <i>Отправьте фото чека, чтобы бот обработал покупку!</i>"
    )
    bot.reply_to(message, text, parse_mode="HTML")

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

@bot.message_handler(commands=['gen_menu'])
def generate_menu(message):
    chat_id = message.chat.id
    with get_db() as conn:
        trip = conn.execute("SELECT * FROM trips WHERE chat_id = ? AND status = 'active'", (chat_id,)).fetchone()
        if not trip or trip['calorie_goal'] <= 0:
            bot.reply_to(message, "⚠️ Сначала задайте цель по калориям командой <code>/goal [число]</code> и создайте список через /shop.", parse_mode="HTML")
            return
        
        goal = trip['calorie_goal']
        trip_id = trip['id']
        
        conn.execute("UPDATE trip_items SET status = 'skipped' WHERE trip_id = ?", (trip_id,))
        
        items = conn.execute("SELECT * FROM trip_items WHERE trip_id = ? AND calories > 0", (trip_id,)).fetchall()
        if not items:
            bot.reply_to(message, "В вашем каталоге нет товаров с указанными калориями. Добавьте калории в скобках!")
            return

        items = sorted(items, key=lambda x: x['calories'], reverse=True)
        current_sum = 0
        selected_ids = []
        
        for item in items:
            if current_sum + item['calories'] <= goal + 200:
                selected_ids.append(item['id'])
                current_sum += item['calories']
                
        if selected_ids:
            placeholders = ','.join(['?'] * len(selected_ids))
            conn.execute(f"UPDATE trip_items SET status = 'need' WHERE id IN ({placeholders})", selected_ids)
            conn.commit()
            bot.reply_to(message, f"🤖 Рацион сгенерирован! Подобрано продуктов на ~{current_sum} ккал (цель: {goal} ккал). Откройте /shop для просмотра.")
        else:
            bot.reply_to(message, "Не удалось подобрать рацион под заданную цель.")

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
            cursor.execute("INSERT INTO trips (chat_id, date, status, budget, calorie_goal) VALUES (?, ?, 'active', 0, 0)", 
                           (chat_id, datetime.now().strftime("%d.%m.%Y %H:%M")))
            trip_id = cursor.lastrowid
            
            for item in catalog_items:
                cursor.execute(
                    "INSERT INTO trip_items (trip_id, name, shop, price, calories, status) VALUES (?, ?, ?, ?, ?, 'need')",
                    (trip_id, item['name'], item['shop'], item['price'], item['calories'])
                )
            conn.commit()
            trip = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()

    send_trip_message(message.chat.id, trip['id'])

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

    body = "<b>🛒 РАЦИОН, ЗАТРАТЫ И КБЖУ</b>\n\n"
    
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

    text = f"<blockquote>{body}</blockquote>"

    markup = types.InlineKeyboardMarkup(row_width=1)
    for item in items:
        status_symbol = "🛒"
        if item['status'] == 'bought':
            status_symbol = "✅"
        elif item['status'] == 'skipped':
            status_symbol = "❌"
            
        btn_text = f"{status_symbol} {item['name']} ({item['shop']}) — {item['price']}р"
        markup.add(types.InlineKeyboardButton(text=btn_text, callback_data=f"toggle_{item['id']}_{trip_id}"))

    markup.add(types.InlineKeyboardButton(text="📤 Экспортировать список для близких", callback_data=f"export_{trip_id}"))
    markup.add(types.InlineKeyboardButton(text="🏁 Завершить закупку (в архив)", callback_data=f"finish_{trip_id}"))
    
    return text, markup

def send_trip_message(chat_id, trip_id):
    text, markup = generate_trip_content(trip_id)
    if len(text) > 4000:
        parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for idx, part in enumerate(parts):
            if idx == len(parts) - 1:
                bot.send_message(chat_id, part, reply_markup=markup, parse_mode="HTML")
            else:
                bot.send_message(chat_id, part, parse_mode="HTML")
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")

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
            
        bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text="🎉 Закупка завершена и сохранена в историю!", parse_mode="HTML")
        bot.answer_callback_query(call.id, "Успешно сохранено в архив!")

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
        bot.reply_to(message, f"✅ Добавлено позиций в каталог: {added}.\nТеперь вы можете запустить список командой /shop")
    else:
        bot.reply_to(message, "Не удалось распознать позиции. Формат: <code>Товар (Магазин) (Цена) (Калории)</code>", parse_mode="HTML")

if __name__ == "__main__":
    bot.infinity_polling()
