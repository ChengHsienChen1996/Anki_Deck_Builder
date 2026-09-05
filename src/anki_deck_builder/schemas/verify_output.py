"""核對階段的 LLM structured output schema（Phase 9）。

由 `agents.yaml` 的 `output_schema` 以 dotted path 引用。

**回傳的是「更正清單」而不是整批卡片**：讓模型重寫整頁等於再抽取一次，
那既昂貴又會把原本正確的欄位一起換掉。只回差異，套用端才有辦法逐筆稽核。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

#: 允許被更正的欄位。**只有兩個，這是實測收斂出來的**（Task 9.0，2026-09-02）。
#:
#: 一開始放了五個（加上 `front`、`back`、`hint`），實測 14 筆變動裡 5 筆是誤更正，
#: 而且**誤更正全部集中在那三個欄位**：模型會先「改判這張卡是哪個詞條」
#: （把正確的 `嫌がる` 判成 `嫌う`），再把讀音與釋義一起改成與它的判斷一致——
#: 一個誤判連帶毀掉三個欄位。
#:
#: 按欄位拆開的真／誤比：`reading` 5:1、`example` 1:0、`front` 1:1、`back` 1:2、`hint` 1:1。
#: 只留前兩個時是 6 真 1 誤（86%），而 OCR 錯誤本來就集中在讀音。
#:
#: `image_scene`、`mnemonic`、`tags` 更不在此列——它們是創作或推導出來的，
#: 影像上根本沒有對應物
CORRECTABLE_FIELDS: frozenset[str] = frozenset({"reading", "example"})


class Correction(BaseModel):
    """單一欄位的更正。"""

    card_id: str = Field(description="要更正的卡片識別碼")
    field: str = Field(description="欄位名稱")
    value: str = Field(description="依影像更正後的值")
    reason: str = Field(default="", description="影像上看到什麼才這樣改")


class VerifyOutput(BaseModel):
    """單次核對呼叫的回傳：這一頁需要的所有更正。"""

    corrections: list[Correction] = Field(
        default_factory=list, description="需要更正的欄位；沒有問題就是空清單"
    )
