from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Проверить книгу")],
        [KeyboardButton(text="🔍 Проверить контрагента"), KeyboardButton(text="📋 Список ИНН")],
        [KeyboardButton(text="🔗 Найти связи")],
        [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="ℹ️ Помощь")],
    ],
    resize_keyboard=True,
)
