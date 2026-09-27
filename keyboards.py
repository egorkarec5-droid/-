from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def get_main_menu(is_owner: bool = False) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="📊 Проверить книгу")],
        [KeyboardButton(text="🔍 Проверить контрагента"), KeyboardButton(text="📋 Список ИНН")],
        [KeyboardButton(text="🔗 Найти связи")],
        [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="ℹ️ Помощь")],
    ]
    if is_owner:
        keyboard.append([KeyboardButton(text="🛠 Админка")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


# Обычное меню (без кнопки админки) — для обратной совместимости там, где
# владелец ещё не определён по контексту; используйте menu_for(user_id) в start.py
main_menu = get_main_menu(is_owner=False)
