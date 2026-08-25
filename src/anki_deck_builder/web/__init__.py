"""本地 Web UI（介面層）。

`server.py` 提供 FastAPI 端點，`service.py` 是唯一的膠水層，`tasks.py` 負責
背景執行的單一飛行。Gradio 介面於 Task 5.2 加入 `ui.py`，掛在同一個 app 上。

匯入一律延後到函式內：`gradio` 與 `fastapi` 很重，CLI 的其他子命令不該
為了它們付出啟動成本。
"""
