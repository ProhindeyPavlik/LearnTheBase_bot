import os
import subprocess
import sys
import time
import staypresent

# Запускаем веб-сервер для keep-alive (чтобы Render не усыпил)
staypresent.web.json({"status": "running"})

# Запускаем бота в бесконечном цикле с перезапуском
while True:
    try:
        # Запускаем bot.py как отдельный процесс
        process = subprocess.Popen(
            [sys.executable, "bot.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True
        )
        # Ждём завершения процесса (или можно читать логи, но оставим простой вариант)
        process.wait()
        print(f"Bot process exited with code {process.returncode}. Restarting...")
    except Exception as e:
        print(f"Exception while running bot: {e}. Restarting...")
    time.sleep(2)  # небольшая пауза перед перезапуском
