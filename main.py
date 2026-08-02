import os
import subprocess
import sys
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# ----- Веб-сервер для keep-alive (чтобы Render не усыпил) -----
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_webserver(port):
    server = HTTPServer(('', port), Handler)
    server.serve_forever()

port = int(os.getenv("PORT", 8080))
thread = threading.Thread(target=run_webserver, args=(port,), daemon=True)
thread.start()
print(f"Keep-alive web server running on port {port}")

# ----- Запуск бота с перезапуском при падении -----
while True:
    try:
        process = subprocess.Popen(
            [sys.executable, "bot.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True
        )
        # Ждём завершения процесса (если бот упадёт или его убьют)
        process.wait()
        print(f"Bot process exited with code {process.returncode}. Restarting in 2s...")
    except Exception as e:
        print(f"Exception while running bot: {e}. Restarting in 2s...")
    time.sleep(2)
