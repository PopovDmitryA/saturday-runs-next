from app.s95.errors import S95BanDetected
from app.s95.fetch.coordinator import fetch_json, fetch_page_html
from app.s95.fetch.priority import S95YieldForUserSync

# Две причины свернуть пакетный проход досрочно, а не ошибка на каждой локации:
# s95 нас не пускает (бан или охлаждение) либо очередь ждёт пользовательский синк.
S95_STOP_EXCEPTIONS = (S95BanDetected, S95YieldForUserSync)

__all__ = ["S95_STOP_EXCEPTIONS", "fetch_json", "fetch_page_html"]
