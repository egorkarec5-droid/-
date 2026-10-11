"""
Точка запуска бота на Timeweb Cloud Apps.

Делает две вещи:
1. поднимает маленький HTTP-сервер «я жив» на порту PORT (по умолчанию 8080) —
   хостинг проверяет по нему, что приложение запущено;
2. запускает самого бота (start.main), как и раньше.

Код бота при этом не меняется: локально и где угодно ещё можно по-прежнему
запускать python start.py.
"""

import asyncio
import logging
import os

from aiohttp import web

import start


async def _health_server():
    async def ok(_request):
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", ok)
    app.router.add_get("/health", ok)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logging.info("Health-check сервер слушает порт %s", port)


async def main():
    logging.basicConfig(level=logging.INFO)
    try:
        await _health_server()
    except Exception:
        logging.exception("Health-check сервер не запустился — бот работает без него")
    await start.main()


if __name__ == "__main__":
    asyncio.run(main())
