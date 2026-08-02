# main.py
import os
import staypresent

# Запускаем мини-сервер, чтобы Render не "усыпил" бота
staypresent.web.json({"status": "running"})

# Запускаем ваш основной файл с ботом (например, bot.py) на порту, который даст Render
staypresent.run(
    "bot.py",  # Имя вашего основного файла
    port=int(os.getenv("PORT", 8080))
)