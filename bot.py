import telebot
from telebot import types
import re
import os

TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

# Хранилище покупок в памяти: {chat_id: {магазин: [(товар, цена)]}}
user_carts = {}

DEFAULT_SHOP = "Прочее"

def get_cart(chat_id):
    if chat_id not in user_carts:
        user_carts[chat_id] = {}
    return user_carts[chat_id]

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    text = (
        "🧾 **Бот списка покупок**\n\n"
        "Отправляйте покупки строками в формате:\n"
        "`Товар (Магазин) (Цена)`\n\n"
        "**Примеры:**\n"
        "• `Молоко (Пятерочка) (160)`\n"
        "• `Вода (Чижик) (57)`\n"
        "• `Лампочка (150)` (уйдет в Прочее)\n\n"
        "Команды:\n"
        "/list — посмотреть чек и итоги\n"
        "/clear — очистить список"
    )
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(commands=['clear'])
def clear_cart(message):
    user_carts[message.chat.id] = {}
    bot.reply_to(message, "🗑 Список покупок очищен!")

@bot.message_handler(commands=['list'])
def show_list(message):
    cart = get_cart(message.chat.id)
    if not cart:
        bot.reply_to(message, "Ваш список покупок пуст.")
        return

    text = "📋 **Текущий список покупок:**\n\n"
    grand_total = 0

    for shop, items in cart.items():
        shop_total = sum(price for _, price in items)
        grand_total += shop_total
        text += f"🏬 *{shop}* (сумма: {shop_total} руб.):\n"
        for name, price in items:
            price_str = f" — {price} руб." if price > 0 else ""
            text += f"  • {name}{price_str}\n"
        text += "\n"

    text += f"➖➖➖➖➖➖\n**ИТОГО: {grand_total} руб.**"
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(func=lambda message: True)
def parse_items(message):
    cart = get_cart(message.chat.id)
    lines = message.text.strip().split("\n")
    added = 0

    for line in lines:
        line = line.replace("✓", "").strip()
        if not line or line.lower().startswith("итого"):
            continue

        # Парсинг скобок: Название (Магазин) (Цена рублей)
        matches = re.findall(r"\((.*?)\)", line)
        name = re.sub(r"\(.*?\)", "", line).strip()
        shop = DEFAULT_SHOP
        price = 0

        for match in matches:
            clean_num = re.search(r"\d+", match)
            if clean_num and any(x in match.lower() for x in ["руб", "р", "$"]) or clean_num and match.isdigit():
                price = int(clean_num.group())
            else:
                shop = match.strip().capitalize()

        if name:
            if shop not in cart:
                cart[shop] = []
            cart[shop].append((name, price))
            added += 1

    if added > 0:
        bot.reply_to(message, f"✅ Добавлено позиций: {added}. Напишите /list для просмотра чека.")
    else:
        bot.reply_to(message, "Не удалось распознать позиции. Используйте формат: `Товар (Магазин) (Цена)`", parse_mode="Markdown")

if __name__ == "__main__":
    bot.infinity_polling()