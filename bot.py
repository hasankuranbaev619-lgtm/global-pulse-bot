import os
import asyncio
import logging
import aiosqlite
import feedparser
from datetime import datetime
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, html, F
from aiogram.types import Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode

from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 6206995558))

if not BOT_TOKEN:
    raise ValueError("ОШИБКА: Переменная BOT_TOKEN не найдена в файле .env!")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

DB_NAME = "database.db"

RSS_FEEDS = {
    "BBC World": "http://feeds.bbci.co.uk/news/world/rss.xml",
    "Reuters": "https://www.reutersagency.com/feed/?best-topics=top-news&post_type=best",
    "TechCrunch": "https://techcrunch.com/feed/"
}


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY
            )
        """)

        try:
            await db.execute("ALTER TABLE users ADD COLUMN joined_at TEXT")
        except Exception:
            pass

        try:
            await db.execute("ALTER TABLE users ADD COLUMN news_read_count INTEGER DEFAULT 0")
        except Exception:
            pass

        try:
            await db.execute("ALTER TABLE users ADD COLUMN is_subscribed INTEGER DEFAULT 0")
        except Exception:
            pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS news (
                link TEXT PRIMARY KEY,
                title TEXT,
                source TEXT
            )
        """)
        await db.commit()


async def add_user(user_id: int):
    today_date = datetime.now().strftime("%d.%m.%Y")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, joined_at, news_read_count, is_subscribed) VALUES (?, ?, 0, 0)",
            (user_id, today_date)
        )
        await db.execute(
            "UPDATE users SET joined_at = ? WHERE user_id = ? AND joined_at IS NULL",
            (today_date, user_id)
        )
        await db.commit()


async def increment_user_news_count(user_id: int, count: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET news_read_count = COALESCE(news_read_count, 0) + ? WHERE user_id = ?",
            (count, user_id)
        )
        await db.commit()


async def set_subscription(user_id: int, status: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET is_subscribed = ? WHERE user_id = ?",
            (status, user_id)
        )
        await db.commit()


async def get_subscribed_users():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users WHERE is_subscribed = 1") as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def get_all_users():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def is_news_saved(link: str) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT 1 FROM news WHERE link = ?", (link,)) as cursor:
            return await cursor.fetchone() is not None


async def save_news(link: str, title: str, source: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO news (link, title, source) VALUES (?, ?, ?)",
            (link, title, source)
        )
        await db.commit()


async def get_latest_saved_news(limit: int = 5):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT source, title, link FROM news ORDER BY rowid DESC LIMIT ?", (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [{"source": r[0], "title": r[1], "link": r[2]} for r in rows]


async def get_user_stats(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
                "SELECT joined_at, news_read_count, is_subscribed FROM users WHERE user_id = ?",
                (user_id,)
        ) as cursor:
            user_data = await cursor.fetchone()

        async with db.execute("SELECT COUNT(*) FROM news") as cursor:
            total_system_news = (await cursor.fetchone())[0]

        if user_data:
            joined_at = user_data[0] or datetime.now().strftime("%d.%m.%Y")
            news_read_count = user_data[1] or 0
            is_subscribed = bool(user_data[2])
        else:
            joined_at = datetime.now().strftime("%d.%m.%Y")
            news_read_count = 0
            is_subscribed = False

        return joined_at, news_read_count, total_system_news, is_subscribed


async def fetch_news():
    news_items = []
    for source, url in RSS_FEEDS.items():
        feed = feedparser.parse(url)
        for entry in feed.entries[:3]:
            if not await is_news_saved(entry.link):
                await save_news(entry.link, entry.title, source)
                news_items.append({
                    "source": source,
                    "title": entry.title,
                    "link": entry.link
                })
    return news_items


async def send_daily_news():
    subscribed_users = await get_subscribed_users()
    if not subscribed_users:
        return

    news_items = await fetch_news()
    if not news_items:
        news_items = await get_latest_saved_news(limit=5)

    if not news_items:
        return

    response_text = "<b>☀️ Ежедневная утренняя сводка новостей GlobalPulse:</b>\n\n"
    for item in news_items:
        response_text += (
            f"🔹 <b>[{item['source']}]</b>\n"
            f"{html.quote(item['title'])}\n"
            f"🔗 <a href='{item['link']}'>Читать источник</a>\n\n"
        )

    for user_id in subscribed_users:
        try:
            await bot.send_message(
                chat_id=user_id,
                text=response_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            await increment_user_news_count(user_id, len(news_items))
            await asyncio.sleep(0.05)
        except Exception as e:
            logging.error(f"Ошибка отправки пользователю {user_id}: {e}")


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await add_user(message.from_user.id)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🌍 Свежие новости", callback_data="get_news")]
        ]
    )

    welcome_text = (
        f"<b>Добро пожаловать в GlobalPulse, {html.quote(message.from_user.first_name)}!</b> 🌍\n\n"
        "Я — умный агрегатор главных мировых новостей.\n"
        "Собираю свежие сводки из ведущих международных источников в режиме реального времени.\n\n"
        "Нажмите кнопку ниже, чтобы получить последние новости!"
    )

    photo_path = "welcome.jpg"
    if os.path.exists(photo_path):
        photo = FSInputFile(photo_path)
        await message.answer_photo(
            photo=photo,
            caption=welcome_text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    else:
        await message.answer(
            text=welcome_text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )


@dp.callback_query(F.data == "get_news")
@dp.message(Command("news"))
async def send_news(event):
    message = event.message if hasattr(event, "message") else event
    user_id = event.from_user.id

    await add_user(user_id)

    status_msg = await message.answer("🔄 Сканирую мировые источники...")

    news_items = await fetch_news()

    await status_msg.delete()

    is_fallback = False
    if not news_items:
        news_items = await get_latest_saved_news(limit=5)
        is_fallback = True

    if not news_items:
        await message.answer("В данный момент не удалось загрузить новости. Попробуйте чуть позже!")
        return

    await increment_user_news_count(user_id, len(news_items))

    if is_fallback:
        response_text = "<b>📌 Последние актуальные новости:</b>\n<i>(Новые ленты обновляются, вот свежие из архива)</i>\n\n"
    else:
        response_text = "<b>🌍 Свежие мировые новости:</b>\n\n"

    for item in news_items:
        response_text += (
            f"🔹 <b>[{item['source']}]</b>\n"
            f"{html.quote(item['title'])}\n"
            f"🔗 <a href='{item['link']}'>Читать источник</a>\n\n"
        )

    await message.answer(
        text=response_text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )


@dp.message(Command("subscribe"))
async def cmd_subscribe(message: Message):
    user_id = message.from_user.id
    await add_user(user_id)
    await set_subscription(user_id, 1)

    await message.answer(
        "<b>🔔 Авторассылка успешно включена!</b>\n\n"
        "Теперь вы будете ежедневно получать утренний дайджест самых важных мировых новостей.\n"
        "Чтобы отключить рассылку, используйте команду /unsubscribe.",
        parse_mode=ParseMode.HTML
    )


@dp.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message):
    user_id = message.from_user.id
    await add_user(user_id)
    await set_subscription(user_id, 0)

    await message.answer(
        "<b>🔕 Ежедневная рассылка отключена.</b>\n\n"
        "Вы больше не будете получать автоматические сводки, но в любое время можете запросить новости вручную с помощью команды /news.",
        parse_mode=ParseMode.HTML
    )


@dp.message(Command("support"))
async def process_support(message: Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Написать разработчику", url="https://t.me/k_4430")]
        ]
    )

    support_text = (
        "<b>🛠️ Служба поддержки GlobalPulse</b>\n\n"
        "Возникли проблемы в работе бота или нашли ошибку? Мы всегда готовы помочь!\n\n"
        "<b>По каким вопросам можно обращаться:</b>\n"
        "• Сообщить о баге или сбое в работе\n"
        "• Предложить идею по улучшению функционала\n"
        "• Вопросы по работе новостных лент и подписок\n\n"
        "👤 <b>Контакт разработчика:</b> @k_4430\n\n"
        "<i>Нажмите кнопку ниже, чтобы перейти в личный диалог:</i>"
    )

    await message.answer(support_text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    all_users = await get_all_users()
    subscribed_users = await get_subscribed_users()

    admin_text = (
        "<b>👑 Панель администратора GlobalPulse</b>\n\n"
        f"📊 Всего пользователей в базе: <b>{len(all_users)}</b>\n"
        f"🔔 Активных подписчиков рассылки: <b>{len(subscribed_users)}</b>\n\n"
        "<b>Команды администратора:</b>\n"
        "• <code>/broadcast Ваш текст</code> — отправить рассылку всем пользователям бота."
    )
    await message.answer(admin_text, parse_mode=ParseMode.HTML)


@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    command_args = message.text.split(maxsplit=1)
    if len(command_args) < 2:
        await message.answer(
            "<b>⚠️ Ошибка:</b> Укажите текст для рассылки!\nПример: <code>/broadcast Внимание! Обновление бота.</code>",
            parse_mode=ParseMode.HTML)
        return

    broadcast_text = f"<b>📢 Сообщение от администрации:</b>\n\n{command_args[1]}"
    all_users = await get_all_users()

    count_success = 0
    count_errors = 0

    status_msg = await message.answer("🔄 Рассылка запущена...")

    for u_id in all_users:
        try:
            await bot.send_message(u_id, broadcast_text, parse_mode=ParseMode.HTML)
            count_success += 1
            await asyncio.sleep(0.05)
        except Exception:
            count_errors += 1

    await status_msg.edit_text(
        f"<b>✅ Рассылка завершена!</b>\n\n"
        f"👍 Доставлено: <b>{count_success}</b>\n"
        f"❌ Ошибок (заблокировали бота): <b>{count_errors}</b>",
        parse_mode=ParseMode.HTML
    )


@dp.message(Command("stats"))
async def show_stats(message: Message):
    user_id = message.from_user.id
    await add_user(user_id)

    joined_at, news_read_count, total_system_news, is_subscribed = await get_user_stats(user_id)

    if news_read_count == 0:
        status = "🌱 Новичок"
    elif news_read_count < 15:
        status = "🧐 Читатель"
    elif news_read_count < 50:
        status = "⚡ Активный следопыт"
    else:
        status = "🔥 Главный аналитик"

    sub_status = "🔔 Включена" if is_subscribed else "🔕 Отключена"

    stats_text = (
        f"<b>📊 Ваша личная карточка GlobalPulse</b>\n\n"
        f"👤 Пользователь: <b>{html.quote(message.from_user.first_name)}</b>\n"
        f"📅 С нами с: <b>{joined_at}</b>\n"
        f"🏆 Ваш статус: <b>{status}</b>\n"
        f"📩 Авторассылка: <b>{sub_status}</b>\n\n"
        f"📥 Получено новостей лично вами: <b>{news_read_count}</b>\n"
        f"🌐 Всего обработано базой бота: <b>{total_system_news}</b>\n\n"
        f"<i>💡 Используйте команды /subscribe и /unsubscribe для управления рассылкой!</i>"
    )

    await message.answer(stats_text, parse_mode=ParseMode.HTML)


@dp.message(Command("rules"))
async def process_rules(message: Message):
    rules_text = (
        "<b>📜 Правила использования GlobalPulse</b>\n\n"
        "Чтобы сервис оставался удобным и безопасным для всех, пожалуйста, соблюдайте следующие правила:\n\n"
        "<b>1. Ограничение запросов (Спам)</b>\n"
        "• Запрещено часто отправлять команды бота за короткий промежуток времени.\n\n"
        "<b>2. Источники и Авторские права</b>\n"
        "• Все новости автоматически агрегируются из открытых RSS-источников.\n"
        "• Бот не выражает личного мнения и не редактирует первоисточники.\n\n"
        "<b>3. Использование данных</b>\n"
        "• Бот хранит только общедоступные данные вашего профиля Telegram (ID, имя) и счетчик запросов для отображения статистики.\n\n"
        "<b>4. Добросовестное использование</b>\n"
        "• Запрещены любые попытки проведения атаки на сервис или использования автокликеров/ботов для создания нагрузки.\n\n"
        "<i>Нарушение правил может привести к ограничению доступа к сервису. Спасибо за понимание!</i> 🤝"
    )
    await message.answer(rules_text, parse_mode=ParseMode.HTML)


@dp.message(Command("about"))
async def process_about(message: Message):
    about_text = (
        "<b>🌍 О проекте GlobalPulse</b>\n\n"
        "<b>GlobalPulse</b> — это интеллектуальный информационный сервис, созданный для оперативного "
        "мониторинга и агрегации мирового новостного потока в режиме реального времени.\n\n"
        "<b>🎯 Наша миссия:</b>\n"
        "Предоставить пользователям быстрый, объективный и удобный доступ к ключевым "
        "событиям планеты без информационного шума и рекламы.\n\n"
        "<b>✨ Ключевые возможности сервиса:</b>\n"
        "• <b>Глобальный охват:</b> непрерывный мониторинг ведущих международных информагентств и СМИ.\n"
        "• <b>Фильтрация дубликатов:</b> умная система отбора уникальных инфоповодов.\n"
        "• <b>Скорость и точность:</b> доставка главных новостей в течение нескольких минут после публикации.\n"
        "• <b>Лаконичный формат:</b> сжатые и структурированные сводки для экономии вашего времени.\n\n"
        "<i>GlobalPulse — ваш надежный ориентир в мире глобальных новостей.</i>"
    )
    await message.answer(about_text, parse_mode=ParseMode.HTML)


async def main():
    await init_db()
    scheduler.add_job(send_daily_news, 'cron', hour=9, minute=0)
    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())