"""專案自訂例外階層。

外部服務的例外一律在 `clients/` 轉為 `ExternalServiceError` 的子類，
`stages/` 與介面層只處理本模組定義的例外，不直接接觸第三方例外型別。
"""


class AnkiBuilderError(Exception):
    """本專案所有自訂例外的根類別。"""


class ConfigurationError(AnkiBuilderError):
    """設定缺漏或格式錯誤。

    訊息必須指出出問題的環境變數名稱，讓使用者不必翻程式碼就知道該補什麼。
    """


class ExternalServiceError(AnkiBuilderError):
    """外部服務（LLM、OCR、ComfyUI、VOXCPM2）呼叫失敗。"""


class StageProcessingError(AnkiBuilderError):
    """單列處理失敗。

    依架構約束 3，`stages/` 捕捉此例外後寫入該列的 `*_error` 欄位並繼續下一列，
    不得讓它中斷整批。
    """
