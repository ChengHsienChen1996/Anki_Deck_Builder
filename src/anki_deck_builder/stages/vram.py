"""階段開工前的 VRAM 讓渡（流程層）。

本機只有一張卡，而本專案會在上面跑數種模型：Ollama 的抽取／OCR 模型、
ComfyUI 的 SD、VOXCPM2 的語音權重，以及（開啟分塊時）版面偵測器。
誰先佔住不放，下一個階段就會排隊或載不滿。

**CLI 與 Web UI 都要做這件事**，所以邏輯放在這裡而不是任一個介面層——
兩邊各寫一份的話，同一台機器上 UI 跑出來的失敗率會跟 CLI 不一樣（約束 4）。

讓渡一律是**最佳化而非流程的一部分**：取不到 endpoint、服務沒開、設定關閉時
都只是略過，絕不讓它擋下真正要做的事。
"""

from __future__ import annotations

from collections.abc import Callable, Collection

from ..config import Settings
from ..exceptions import AnkiBuilderError

#: 讓渡過程的說明訊息去處。CLI 傳 `print`，Web UI 傳 logger，測試傳 list.append
Notify = Callable[[str], None]

#: `release_all_gpu(keep=...)` 認得的值。用具名常數而非裸字串，免得呼叫端打錯字
#: 卻安靜地什麼都沒 keep 住（那正是 OOM 的樣子）。
#:
#: **沒有 VOXCPM 這一項是刻意的**：那一側唯一的動作是 `release_gpu_cache()`，
#: 它只把 torch 的快取配置器還給驅動，**不會卸載行程內已載入的模型**，
#: 所以連 audio 自己都不需要 keep 它。
COMFYUI = "comfyui"
OLLAMA = "ollama"


def extract_agent_name(settings: Settings) -> str:
    """抽取階段實際會用到的 agent（兩條輸入路徑用不同 agent，但模型相同）。"""
    from .extract import TEXT_AGENT, VISION_AGENT

    return VISION_AGENT if settings.ingest.mode == "vision_direct" else TEXT_AGENT


def ollama_base_url(settings: Settings) -> str:
    """Ollama 的 endpoint。

    唯一真實來源是 `agents.yaml`（見 `clients/agent_endpoint.py`），所以即使
    image 階段自己不呼叫任何 agent，也從 agent 物件反查而不在 `.env` 另立一份。
    """
    from ..clients.llm_client import LLMClient

    client = LLMClient(settings.agent_factory.yaml_settings_file)
    base_url, _ = client.model_endpoint(extract_agent_name(settings))
    return base_url


async def free_vram_for(
    settings: Settings,
    endpoint: tuple[str, str],
    notify: Notify | None = None,
) -> None:
    """階段開始前卸載其他常駐模型。

    Ollama 預設 `keep_alive` 5 分鐘不會主動讓位，前一階段的模型還在時，
    這一階段的請求會卡在排隊、`still_waiting` 一路累積到逾時。
    """
    if not settings.model_unload.before_stage:
        return

    from ..clients.model_unload import ensure_room

    base_url, model = endpoint
    freed = await ensure_room(base_url, model, wait_timeout=settings.model_unload.timeout)
    if freed and notify is not None:
        notify(f"（已卸載 {'、'.join(freed)} 以騰出 VRAM）")


async def free_vram_for_local_gpu(
    settings: Settings,
    notify: Notify | None = None,
    on_skip: Notify | None = None,
) -> None:
    """image／audio 開工前卸載**全部** Ollama 模型。

    這兩個階段都在本機 GPU 上跑自己的模型（ComfyUI 的 SD、VOXCPM2 的權重），
    都不需要任何 Ollama 模型，所以 `keep=""`——一個都不留。切回 31B 抽取模型時
    這件事是必要條件：19.87 GB 加上 VOXCPM2 的 6.3 GB 就已經超出 24 GB。

    **也順手把 PyTorch 的快取還回去**：VOXCPM2 是行程內的模型，跑完 audio 之後
    那約 7.5 GB 會被 caching allocator 抓著不放。CLI 每個指令是獨立行程所以感覺不到，
    但 **Web UI 是長駐行程**——在那裡先跑 audio 再跑 image，ComfyUI 就少了那麼多
    可用顯存。這一步不受 `MODEL_UNLOAD_BEFORE_STAGE` 管：那個開關管的是 Ollama。

    endpoint 取不到時只略過，不中斷本階段：只做圖不做抽取的人可能根本沒有
    `agents.yaml`，不該因此連圖都生不出來。`on_skip` 與 `notify` 分開，
    是因為 CLI 把略過訊息寫到 stderr、把卸載結果寫到 stdout。
    """
    from ..clients.tts_client import release_gpu_cache

    # 同一個呼叫也把版面偵測器的快取還回去——它與 VOXCPM2 一樣是行程內的
    # torch 模型，`empty_cache()` 對兩者一視同仁（開啟分塊時才會有東西可還）
    if release_gpu_cache() and notify is not None:
        notify("（已釋放行程內 torch 模型佔用的 VRAM）")

    if not settings.model_unload.before_stage:
        return
    try:
        base_url = ollama_base_url(settings)
    except AnkiBuilderError as error:
        if on_skip is not None:
            on_skip(f"（略過 VRAM 讓渡：{error}）")
        return
    await free_vram_for(settings, (base_url, ""), notify)


async def release_comfyui(settings: Settings, notify: Notify | None = None) -> None:
    """請 ComfyUI 釋放 VRAM，讓給接下來要吃 GPU 的階段。

    `ensure_room()` 看不到 ComfyUI——它只認 Ollama 的 `/api/ps`，
    `free_vram_for_local_gpu()` 也只卸載 Ollama 的模型，兩者都不碰 ComfyUI。
    這個函式是唯一會請 ComfyUI 讓位的路徑。

    **在 extract 與 audio 之前呼叫**，兩處的理由不同：

    - extract：ComfyUI 常駐會壓縮抽取模型的載入比例。31B q4（19.87 GB）時實測
      只載入 88%、速度掉到 1/6；換成 E4B（11.64 GB）後兩者可共存，這個呼叫
      不再必要（開與不開差 0.9%），切回 31B 時它會重新變得必要
    - audio：VOXCPM2 峰值約 7.5 GB，加上 ComfyUI 常駐要放得下。SDXL 的 7.2 GB
      合計 14.7 GB 有餘裕，所以 Phase 4～6 一直沒事；FLUX.2-klein-9B 的
      17.4 GB 合計 24.9 GB 會超出 24 GB 卡——**換模型逼出來的必要呼叫**

    不在 image 前呼叫：那一步正要用 ComfyUI，清空它只是白費工。
    也不在 OCR 前呼叫：OCR 模型只佔 2.2 GB，沒有同樣的壓力。

    > ⚠️ 上一句只在「ComfyUI 此刻是空的」時成立。**開跑前 GPU 上已經有東西**時
    > 完全不同——ComfyUI 只要被用過就抓著 17.6 GB 不放，再載 2.2 GB 的 OCR 模型
    > 就在 24 GB 卡的邊緣上（2026-09-02 使用者實測 OOM，2026-09-10 再次實測
    > 28 列全滅）。那個缺口由 `release_all_gpu()` 補。
    >
    > **2026-09-10 起單獨子命令的開跑前讓渡改由 `_dispatch`／`_prepare` 統一做**
    > （`cli.py` 的 `GPU_COMMAND_KEEP`、`web/service.py` 的 `_STAGE_KEEP`），
    > 因此本函式**只留在 `run-all` 的階段之間**。單獨跑一個階段時它是多餘的
    > ——那條路徑上的 ComfyUI 讓渡已由 `release_all_gpu(respect_switches=True)`
    > 以同一個開關做過了。

    預設關閉（`COMFYUI_FREE_BEFORE_LLM`）：它是最佳化，且開啟後 ComfyUI 下次生成
    要重載模型。變數名留著「LLM」是為了不動使用者既有的 `.env`——語意以本段為準。
    """
    if not settings.comfyui.free_before_llm:
        return

    from ..clients.comfyui_client import free_memory

    if await free_memory(settings.comfyui.base_url) and notify is not None:
        notify("（已請 ComfyUI 釋放 VRAM）")


async def release_all_gpu(
    settings: Settings,
    notify: Notify | None = None,
    on_skip: Notify | None = None,
    keep: Collection[str] = (),
    respect_switches: bool = False,
) -> None:
    """開跑前把 GPU 上**這一趟用不到**的東西請出去。

    ## 為什麼需要它

    階段之間的讓渡（`release_comfyui()`、`free_vram_for*()`）假設**這個 pipeline
    是 GPU 上唯一的東西**——每個階段只需要把「前一個階段」留下的模型請走。
    這個假設在「開跑前 GPU 上就已經有東西」時不成立：ComfyUI 只要被用過就會
    抓著 17.4 GB 不放，`run-all` 的第一階段一頭撞上去就是 CUDA OOM
    （2026-09-02 使用者實測遇到）。

    `release_comfyui()` 的 docstring 原本寫「不在 OCR 前呼叫：OCR 模型只佔 2.2 GB，
    沒有同樣的壓力」——那個判斷只看了本專案自己的階段順序，沒有考慮
    **開跑前 GPU 的既有狀態**。本函式補的就是那個缺口。

    ## `keep`：單獨跑一個階段時，別清掉它正要用的東西

    2026-09-10 起本函式也接在**單獨子命令**上（原本只有 `run-all`）。
    起因是使用者跑完 `image` 之後單獨跑 `ocr`，ComfyUI 抓著 17.97 GB／24 GB 不放，
    28 列全部 CUDA OOM——`ocr` 是唯一一個連 `release_comfyui()` 都沒有的子命令。

    但單獨跑一個階段與 `run-all` 有個差別：**那個階段正要用的東西不該被清掉**。
    `keep` 就是用來說明這件事的，值取自本模組的 `COMFYUI`／`OLLAMA`：

    | 這一趟要跑的 | `keep` | 理由 |
    |---|---|---|
    | ocr／extract／scene／prompt／verify | `OLLAMA` | 見下方 |
    | `image` | `COMFYUI` | 正要用它，清掉只是白付一次冷啟（約 100 秒） |
    | `audio` | （空） | 它要用的 VOXCPM2 不受本函式影響，見 `COMFYUI`／`OLLAMA` 常數的說明 |
    | `run-all` | （空） | 兩者遲早都要用，開跑前一個都不留 |

    LLM 階段 `keep` 住 Ollama，是因為 Ollama 那側由該階段自己的 `free_vram_for()`
    處理得更精確——它 `keep` 住這一階段要用的**那一個模型**而只趕走別的。
    這裡若一併清掉，等於每次呼叫都白付一次模型重載。

    ## `respect_switches`：那兩個開關要不要算數

    `run-all` 傳 `False`（預設）。理由是原本那條：
    `COMFYUI_FREE_BEFORE_LLM` 與 `MODEL_UNLOAD_BEFORE_STAGE` 管的是
    「階段之間要不要付重載成本來換讓渡」，而**整趟開跑前沒有這個取捨**——
    `keep` 以外的東西沒有一樣是這一趟需要的，唯一的代價是稍後重載，
    那嚴格小於 OOM。

    **單獨子命令傳 `True`**（2026-09-10 使用者裁示）。關鍵差別是次數：
    接到單獨子命令之後，這個函式**每次呼叫都會跑**，於是「開跑前」實質上
    等同「每個階段前」——那正好是那兩個開關管的事，無視它們就等於讓
    `COMFYUI_FREE_BEFORE_LLM=false` 與 `MODEL_UNLOAD_BEFORE_STAGE=false`
    悄悄失效。使用者設定的語意優先於「最不容易 OOM」。

    代價寫明：把開關關掉又用常駐 17.4 GB 的 kyoani workflow 時，
    單獨跑 `ocr` 仍會 OOM。CLAUDE.md 已記該組態下 `COMFYUI_FREE_BEFORE_LLM`
    **必開**，所以這個代價落在一個本來就不該存在的組態上。

    `release_gpu_cache()` 不受 `respect_switches` 影響：它只是把 torch 快取還給
    驅動，沒有重載成本，也沒有哪個開關在管它。

    各項一律**失敗即略過**（同本模組其他函式）：服務沒開、端點不支援、
    torch 沒載入，都不該擋下真正要做的事。
    """
    from ..clients.comfyui_client import free_memory
    from ..clients.model_unload import ensure_room
    from ..clients.tts_client import release_gpu_cache

    unknown = set(keep) - {COMFYUI, OLLAMA}
    if unknown:  # pragma: no cover - 僅在呼叫端打錯字時觸發
        raise ValueError(f"未知的 keep 值：{sorted(unknown)}")

    freed: list[str] = []

    free_comfyui = COMFYUI not in keep and (
        not respect_switches or settings.comfyui.free_before_llm
    )
    unload_ollama = OLLAMA not in keep and (
        not respect_switches or settings.model_unload.before_stage
    )

    if free_comfyui and await free_memory(settings.comfyui.base_url):
        freed.append("ComfyUI")

    if unload_ollama:
        try:
            base_url = ollama_base_url(settings)
        except AnkiBuilderError as error:
            if on_skip is not None:
                on_skip(f"（略過 Ollama 讓渡：{error}）")
        else:
            # keep="" ＝ 一個都不留。此刻 GPU 上沒有一樣東西是這趟流程需要的
            unloaded = await ensure_room(
                base_url, "", wait_timeout=settings.model_unload.timeout
            )
            if unloaded:
                freed.append(f"Ollama（{'、'.join(unloaded)}）")

    if release_gpu_cache():
        freed.append("行程內 torch 模型")

    if notify is not None:
        notify(
            f"（開跑前已釋放 {'、'.join(freed)} 的 VRAM）" if freed
            else "（開跑前檢查：GPU 上沒有需要釋放的東西）"
        )
