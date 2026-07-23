# Maptimize - Lokální konfigurace (UTIA server)

CRITICAL: před každou novou implementací zkontroluj, jestli už to není naimplementovano a zda by se to nedlao využít. Cti SSOT a DRY principy!

CRITICAL: po každé nové implementaci spust code-simplifier agenty ať projdou celé repo a zjednoduší kód a zkontrolujou DRY a SSOT principy.

CRITICAL: Pro rebuild VŽDY používej PRODUKČNÍ docker-compose (`docker-compose.prod.yml`), NE dev! Příklad:
```bash
docker compose -f docker-compose.prod.yml build maptimize-backend --no-cache
docker compose -f docker-compose.prod.yml up -d maptimize-backend
```
⚠️ NIKDY nepoužívej `docker compose down -v` - smaže to databázi!

Neboj se dělat velké a rozsáhlé změny a mazat kód. Legacy implementace maž kdykoliv na ně narazíš. Codebase udržuje maximálně clean a přehlednou.

## 🤖 AI Chat Agent

**Model:** `gemini-3.6-flash` (nasazeno 2026-07-22) - VŽDY používat tento model pro chat agenta!

⚠️ Model ID **nikdy nehardcoduj** - je v `backend/config.py` jako `settings.gemini_model`
(a `settings.gemini_vision_model` pro extrakci regionů ze stránek). Dřív byl na třech
místech ve dvou souborech a dvě z nich zůstala na `gemini-2.0-flash`, který Google
**vypnul 2026-06-01** - web search i vyřezávání obrázků ze stránek tiše přestaly fungovat.

**Gemini 3.x konfigurace:** `temperature`/`top_p`/`top_k` jsou nahrazeny
`thinking_level` (`minimal`/`low`/`medium`/`high`, default `medium`).

**Soubor:** `backend/services/gemini_agent_service.py`

### Konvergence smyčky (OPRAVENO 2026-07-19)

**Symptom:** místo odpovědi agent vrátil `Completed actions: <30 toolů>. Please
try your query again.`

**Příčina:** soft-cap "po 25 toolech vypni tools ať model odpoví" **nefungoval** —
kód sice nastavil `current_tools = None`, ale `function_call` party z modelu
sbíral a **vykonával dál bez ohledu na to** (žádná kontrola stavu). Model
napodoboval dlouhou historii volání a volal tool každý tah až do
`max_iterations=30`, pak spadl do fallbacku, který vypsal seznam toolů.

**Řešení:** (1) když jsou tools vypnuté a model přesto vrátí `function_call`,
**nevykonávat je** — vyskočit na finální syntézu; (2) fallback dělá **jeden
finální `generate_content` bez toolů** s pokynem "odpověz teď v jazyce uživatele",
místo výpisu toolů.

### query_database MUSÍ nést schéma

Pokud tool description neobsahuje **názvy sloupců**, model je neuhodne, dotaz
selže a model to zkouší znovu → jeden dotaz vyvolá tucet volání DB. Schéma je v
`_SQL_SCHEMA_HINT` (SSOT) a vkládá se do description. Při změně modelů ho
aktualizuj. Dovolené JOINy, zakázané subqueries/CTE/UNION.

⚠️ **ACL predikát se MUSÍ psát přes alias tabulky** (opraveno 2026-07-23).
Postgres zahodí původní jméno tabulky ve chvíli, kdy dostane alias, takže
`FROM user_ratings ur ... WHERE user_ratings.user_id = :user_id` spadne na
`invalid reference to FROM-clause entry`. Aliasy řeší `_table_references()`
(jediný parser FROM klauzule, `_extract_referenced_tables` je nad ním jen
tenká obálka).

Tahle třída chyb je **zákeřná: model se z ní nikdy nevzpamatuje.** Predikát se
injektuje až po validaci jeho SQL, takže ho model nevidí — dostane chybu o
tabulce, kterou v `FROM` má, vyhodnotí ji jako nesmysl a zkouší varianty dál.
Reálně to sežralo 6 volání za 27 s a shodilo celý tah na kvótě. **Cokoli, co
přepisuje model-generované SQL, musí být otestované s aliasy i bez nich.**

⚠️ **Self-join = jeden predikát na KAŽDÝ alias.** `FROM comparisons c1 JOIN
comparisons c2` má dvě nezávislé reference; odfiltrovat jen první nechá druhou
nescopovanou a přes ni protečou cizí řádky. Proto `_table_references()` vrací
**seznam** referencí na tabulku, ne jednu.

### 🔴 Gemini free tier = 5 requestů/min A **20 requestů ZA DEN**

Jeden tah agenta pošle **jeden API request na iteraci** (až `MAX_ITERATIONS=30`),
takže netriviální otázka běžně potřebuje víc než 5 requestů za minutu a na free
tieru narazí na 429 uprostřed smyčky. Dřív to zahodilo celý tah včetně všech už
získaných tool výsledků.

**Denní strop 20 requestů znamená ~2–3 otázky za den.** Na free tieru je agent
prakticky nepoužitelný; jediné skutečné řešení je **zapnout billing** na Google
projektu. Ověřeno živě 2026-07-23 (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`,
`quotaValue: 20`).

⚠️ **Minutová a denní kvóta vypadají v odpovědi STEJNĚ** — obojí 429,
`RESOURCE_EXHAUSTED`, obojí nese `retryDelay` kolem minuty. Liší se **jen
`quotaId`** (`...PerMinute...` vs `...PerDay...`). Rozlišuje je `_quota_kind()`
a je to zásadní: minutové okno se přečkáním posune, **denní se resetuje až zítra**.
Čekat na denní kvótu podle jejího `retryDelay` znamená utratit uživateli pět
minut za selhání, které bylo jisté od první odpovědi. Denní kvóta proto
**nepadá do retry** a hlásí se vlastní hláškou.

**Politika: na limit se ČEKÁ, nepadá se.** Rate limit je problém časování —
počkat stojí sekundy, spadnout zahodí všechny tool výsledky, které tah už zaplatil.
`_call_gemini_with_retry()` čte `RetryInfo.retryDelay` z chyby a počká
(`_QUOTA_RETRY_ATTEMPTS=5`, strop `_QUOTA_RETRY_MAX_WAIT=65 s` ≈ jedno kvótové okno).
**Retry delay od serveru je zaokrouhlený dolů** (reálně 11,577 s hlášeno jako
`'11s'`), proto se přičítá `_QUOTA_RETRY_BUFFER` — retry přesně na uvedené hodnotě
spadne na tutéž kvótu znovu.

⚠️ **Retry MUSÍ obalit všechna tři `generate_content` volání**: smyčku agenta,
finální syntézu i two-phase `google_search`. Táhnou ze **stejné** kvóty, takže
nechat kterékoli nechráněné vrátí přesně to selhání, kvůli kterému retry vznikl.
Proto existuje `_call_gemini_with_retry` jako SSOT — nekopíruj retry logiku.

⚠️ **Backendní čekání musí vejít do polling okna frontendu.** `maxPolls`
v `stores/chatStore.ts` (900 = 15 min) je strop; když ho backend přeteče,
uživatel uvidí chybu, zatímco generování normálně běží dál. **Měň obojí
zároveň** — test `test_quota_budget_fits_frontend_polling_window` to hlídá.

Retry je záchranná brzda, ne řešení. Trvalé zvýšení limitu = **zapnout billing**
na Google projektu; z kódu to udělat nejde.

### 💰 Request budget na tah (od 2026-07-23)

Denní kvóta je tvrdý strop, takže **tah má rozpočet requestů**, ne volný běh:

```
REQUEST_BUDGET_PER_TURN = gemini_daily_request_quota // gemini_target_questions_per_day
                        = 20 // 10 = 2
```

Obojí je v `config.py` (**nehardcoduj to** — po zapnutí billingu se zvedne jen
`gemini_daily_request_quota` a agent automaticky dostane víc kol). Minimum je **2**:
první request zavolá tooly, druhý přečte jejich výsledky. Jeden request by uměl
odpovědět jen z hlavy modelu.

Důsledek: model má **jedno kolo toolů**. To je záměrné — tím je donucený tooly
**dávkovat** (sekce „Calling Tools Efficiently" v `SYSTEM_PROMPT`) místo aby šel
jedno volání na request. Před rozpočtem stál složitý dotaz ~10 requestů, tedy
2 otázky na den.

⚠️ **Do rozpočtu se počítá i finální syntéza.** Je to právě ta větev, kterou jde
tah, když se model chová špatně — nechat ji nezapočítanou znamená, že nejhorší
tahy stojí nejvíc. Proto `requests_used` a `_SynthesisSkipped`.

⚠️ **Data-extraction fallback (`**Analysis Results:**`) NESMÍ být pod rozpočtem** —
nestojí žádný API request a je potřeba přesně tehdy, když už rozpočet došel.
Jednou už se to omylem zaškrtilo celé naráz a čtyři testy to chytily.

⚠️ Testy, které cílí na **jiné** brzdy (`FORCE_ANSWER_AFTER`, syntéza, poslední
fallback), si musí vyžádat fixture `generous_request_budget` — s produkčními
dvěma requesty se k nim nedostanou.

### Jak se šetří requesty

Každá iterace smyčky = **jeden API request**, takže počet requestů se snižuje
snižováním počtu tahů, ne zmenšováním odpovědí:

1. **Paralelní tool cally.** Gemini 3 umí vrátit víc `function_call` partů naráz
   a smyčka je všechny vykoná (`Collect ALL function calls`). Model to ale sám od
   sebe dělá jen občas — sekce „Calling Tools Efficiently" v `SYSTEM_PROMPT` mu to
   explicitně ukládá. Osm sériových tahů vs. dva paralelní je rozdíl mezi rychlou
   odpovědí a čekáním na kvótu.
2. **Širší výsledky místo opakovaného hledání.** `SEARCH_RESULT_DEFAULT = 20`
   (dřív 10) pro `search_documents`, `semantic_search` i `search_fov_images`.
   Je to levné: ve Vision RAG je `extracted_text` NULL, takže výsledek je jen
   metadata + URL stránky — **text stránky se neposílá**.

⚠️ **`SEARCH_RESULT_DEFAULT` se NESMÍ přenést na `get_document_content` /
`show_document_pages`.** Ty posílají reálné base64 obrázky stránek a jejich strop
10 je tam schválně (40stránkové PDF zaplavilo kontext). Hlídá
`test_page_reading_cap_did_not_follow_the_search_default_up`.

⚠️ `semantic_search` si `doc_limit`/`image_limit` bralo **mimo `_clamp_limit`**,
takže halucinovaný `doc_limit=5000` prošel a re-billoval se každou iterací.
Každý model-supplied limit musí projít `_clamp_limit`.

### Filosofie návrhu - AUTONOMNÍ AGENT

**CRITICAL: Agent musí být maximálně autonomní!**

Místo vytváření specifických tools pro každý typ úkolu (např. `create_cell_histogram`, `compare_experiments_bar_chart`) preferujeme **obecné nástroje**, které agentovi umožní:

1. **Přístup k datům** - `query_database`, `list_experiments`, `list_images`, `get_cell_detection_results`
2. **Výpočetní schopnosti** - `execute_python_code` (sandbox pro matplotlib, numpy, pandas, scipy)
3. **Vizualizační nástroje** - `create_visualization` (obecný), ale hlavně Python execution pro custom grafy
4. **Export dat** - `export_data`, `batch_export` pro stažení dat

### Proč autonomie?

- **Flexibilita** - Agent může vytvořit JAKÝKOLIV graf/analýzu, ne jen předdefinované typy
- **Kreativita** - Na základě dat může agent navrhnout vlastní vizualizace
- **Škálovatelnost** - Nemusíme přidávat nový tool pro každý nový typ analýzy
- **Biolog-friendly** - Uživatel popíše co chce přirozeným jazykem, agent to implementuje

### Příklady správného přístupu

**✅ SPRÁVNĚ:**
```
User: "Udělej mi histogram distribuce velikostí buněk"
Agent: Použije execute_python_code s matplotlib, napočítá z dat a vytvoří custom graf
```

**❌ ŠPATNĚ:**
```
Vytvořit specifický tool `create_cell_size_histogram` jen pro tento účel
```

### Kdy přidat nový tool?

Nový tool přidávat pouze když:
1. Operace vyžaduje **přístup k systémovým zdrojům** (DB, filesystem, externí API)
2. Operace je **bezpečnostně citlivá** a potřebuje validaci
3. Operace je **velmi častá** a Python execution by byl neefektivní

Pro výpočty, grafy a analýzy → **vždy preferovat `execute_python_code`**

### 🧪 Testování AI Agenta

**Když uživatel řekne "otestuj chat" nebo "otestuj agenta"**, proveď následující:

**Nejrychlejší cesta — živý konverzační smoke-test** (volá reálné Gemini + DB + GPU,
takže stojí peníze; exit code je nenulový, když nějaký tah selže):
```bash
docker exec maptimize-backend python /app/tests/run_agent_conversations.py
```
Označí `FAIL` (prázdná/fallback odpověď), `NEAR-CAP` (tah použil ≥25 toolů, málem
se zacyklil) a `OK`. Vlastní otázky: `-q "..." -q "..."`.

**Testovací otázky (statická sada):** `backend/tests/test_agent_questions.json`

**Postup testování:**

1. **Spusť sledování logů:**
```bash
docker compose -f docker-compose.dev.yml logs -f backend 2>&1 | grep -i -E "gemini|tool|error|exception|google_search"
```

2. **Pošli testovací otázky do chatu** (vyber z každé kategorie):

| Kategorie | Příklad otázky | Očekávaný tool |
|-----------|----------------|----------------|
| Google Search | "What is the weather in Prague?" | `google_search` |
| Code Execution | "Create a histogram of cell sizes" | `execute_python_code` |
| Experiment Data | "Show me 5 sample images from PRC1" | `get_sample_images` |
| Segmentation | "Show segmentation masks for image 42" | `get_segmentation_masks` |
| Document RAG | "Search my documents for fixation protocols" | `search_documents` |
| External APIs | "Get UniProt info about PRC1 protein" | `call_external_api` |
| Database | "How many cells in all experiments?" | `query_database` |
| Export | "Export PRC1 data to CSV" | `export_data` |

3. **Kontroluj v logu:**
   - `FUNCTION_CALL (tool_name)` - agent správně zavolal tool
   - `Tool X completed successfully` - tool proběhl bez chyb
   - `ERROR` nebo `exception` - problém k vyřešení

4. **Kritéria úspěchu:**
   - ✅ Agent volá správné tools pro daný typ dotazu
   - ✅ Tools vracejí výsledky bez ERROR v logu
   - ✅ Agent generuje smysluplnou odpověď s výsledky
   - ✅ Obrázky/grafy se správně zobrazují v chatu

**Rychlý smoke test:**
```bash
# Otestuj klíčové tools jedním příkazem v logu:
docker compose -f docker-compose.dev.yml logs backend 2>&1 | grep -c "completed successfully"
```

**Google Search (two-phase approach):**
- Agent má `google_search` jako callable tool
- Při volání se dělá separátní API call s `types.Tool(google_search=types.GoogleSearch())`
- Historicky obcházelo limitaci "Tool use with function calling is unsupported"

⚠️ **NEMIGRUJ na nativní search — limitace STÁLE PLATÍ (ověřeno live 2026-07-21).**
Dřívější tvrzení, že Gemini 3.5 umí built-in Google Search + function declarations
v jednom requestu, se v živém testu **nepotvrdilo**: přidání
`types.Tool(google_search=types.GoogleSearch())` vedle function-declaration Tool
(i s `include_server_side_tool_invocations=True`) grounding **nespustilo** — odpověď
měla **0 `grounding_chunks`**, SDK logovalo `AFC is disabled ... do not include
function declaration ... in the tool list`, a model si `[Web: …]` markery **vymyslel**
z tréninku. Two-phase přístup proto **zůstává** (funguje: reálně vrátí web citace).
Jeho skutečný bug byl **timeout** — 30s bylo málo (grounded call trvá i >40s → timeout
→ 0 zdrojů), **opraveno na 60s**. Viz commit „bump two-phase google_search timeout".

## ⚠️ KRITICKÉ UPOZORNĚNÍ - PRODUKCE

**Toto je produkční prostředí!**

- 🔴 **NIKDY nemazat databázi** - obsahuje reálná data uživatelů
- 🔴 **Nepoužívat destruktivní migrace** bez zálohy
- Před jakýmikoliv změnami v DB vždy udělat zálohu
- Při úpravách schématu používat pouze additivní migrace

## Zakázané porty (používá Spheroseg)

### Blue environment (produkce)
| Port | Služba |
|------|--------|
| 80 | nginx-main (externí HTTP) |
| 443 | nginx-main (externí HTTPS) |
| 1026 | mailhog SMTP |
| 4000 | blue-frontend |
| 4001 | blue-backend |
| 4008 | blue-ml |
| 4080 | nginx-blue HTTP |
| 4443 | nginx-blue HTTPS |
| 4432 | postgres-blue |
| 4379 | redis-blue |
| 8026 | mailhog web UI |

### Green environment (staging)
| Port | Služba |
|------|--------|
| 5000 | green-frontend |
| 5001 | green-backend |
| 5008 | green-ml |
| 5080 | nginx-green HTTP |
| 5443 | nginx-green HTTPS |
| 5432 | postgres-green |
| 5379 | redis-green |

## Povolené porty pro Maptimize

Používám rozsah **7xxx**:
| Port | Služba | Popis |
|------|--------|-------|
| 7000 | maptimize-frontend | Next.js frontend |
| 7001 | maptimize-backend | FastAPI backend s GPU |
| 7080 | maptimize-nginx | HTTP reverse proxy |
| 7443 | maptimize-nginx | HTTPS reverse proxy |
| 7432 | maptimize-db | PostgreSQL + pgvector |
| 7379 | maptimize-redis | Redis cache |

## Integrace s nginx-main

Hlavní nginx-main na portech 80/443 routuje traffic podle domény:
- `spherosegapp.utia.cas.cz` → nginx-blue (4080/4443)
- `maptimize.utia.cas.cz` → maptimize-nginx (7080)

## GPU alokace

- Spheroseg (blue-ml): 8GB
- Maptimize: 16GB
- Celkem: 24GB (RTX A5000)

## Model weights

⚠️ **YOLOv8 váhy (`weights/best.pt`)** nejsou součástí repozitáře.
Backend NEPŮJDE spustit bez těchto vah - detekce buněk vyžaduje natrénovaný model.

## 🚀 Development Setup (Docker - podobné produkci)

**Princip:** Celý stack běží v Dockeru s hot-reload. Podobné produkčnímu prostředí.

### Spuštění dev prostředí

```bash
# Spusť celý stack (frontend + backend + DB + Redis)
docker compose -f docker-compose.dev.yml up -d

# Sleduj logy
docker compose -f docker-compose.dev.yml logs -f

# Restart po změnách
docker compose -f docker-compose.dev.yml restart frontend
docker compose -f docker-compose.dev.yml restart backend
```

### Důležité porty pro dev

| Port | Služba |
|------|--------|
| 3000 | Frontend (Next.js dev server) |
| 8000 | Backend (FastAPI s hot-reload) |
| 5433 | PostgreSQL (kvůli konfliktu s lokálním PostgreSQL) |
| 6379 | Redis |

### První spuštění

```bash
# 1. Build images
docker compose -f docker-compose.dev.yml build

# 2. Spusť stack
docker compose -f docker-compose.dev.yml up -d

# 3. Zkontroluj stav
docker compose -f docker-compose.dev.yml ps
```

### Hot-reload

- **Frontend:** Změny v `frontend/` se automaticky projeví (Next.js HMR)
- **Backend:** Změny v `backend/` spustí automatický restart (uvicorn --reload)
- **Poznámka:** Pro změny v `package.json` nebo `pyproject.toml` nutný rebuild

### Rebuild po změně závislostí

```bash
# Frontend (po změně package.json)
docker compose -f docker-compose.dev.yml build frontend --no-cache
docker compose -f docker-compose.dev.yml up -d frontend

# Backend (po změně pyproject.toml)
docker compose -f docker-compose.dev.yml build backend --no-cache
docker compose -f docker-compose.dev.yml up -d backend
```

### Zastavení a cleanup

```bash
# Zastavit
docker compose -f docker-compose.dev.yml down

# Zastavit včetně volumes (⚠️ smaže DB data!)
docker compose -f docker-compose.dev.yml down -v
```

### Troubleshooting dev

**Port conflict:**
```bash
# Zkontroluj co běží na portech
lsof -i:3000
lsof -i:8000

# Uvolni porty
docker compose -f docker-compose.dev.yml down
```

**pgvector extension (první spuštění):**
```bash
docker exec maptimize-dev-db psql -U maptimize -d maptimize -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

**Logy jednotlivých služeb:**
```bash
docker compose -f docker-compose.dev.yml logs -f frontend
docker compose -f docker-compose.dev.yml logs -f backend
```

## 🔧 Deploy & Rebuild (Produkce)

**DŮLEŽITÉ:** Při každé změně kódu v backendu nebo frontendu je nutný rebuild:

```bash
# Backend rebuild
docker compose -f docker-compose.prod.yml build maptimize-backend --no-cache
docker compose -f docker-compose.prod.yml up -d maptimize-backend

# Frontend rebuild
docker compose -f docker-compose.prod.yml build maptimize-frontend --no-cache
docker compose -f docker-compose.prod.yml up -d maptimize-frontend

# Pouze restart (bez změn kódu)
docker compose -f docker-compose.prod.yml restart maptimize-backend
```

**Poznámka:** Kód NENÍ mountován jako volume - je zkopírován do image při buildu.

## ⚠️ Známé problémy a řešení

### 404 chyby na API endpointech (OPRAVENO)

**Symptom:** Frontend dostával 404 chyby při volání API (např. `/auth/login`, `/experiments`).

**Příčina:** Frontend API klient (`frontend/lib/api.ts`) volal endpointy bez `/api` prefixu, ale backend má všechny routery registrované pod `/api/...`.

**Řešení (aplikováno):**
1. Všechny endpoint cesty v `frontend/lib/api.ts` nyní začínají `/api/`
2. Backend URL generátory (`embeddings.py`, `metrics.py`) nyní vracejí cesty s `/api/` prefixem

**Prevence při budoucích změnách:**
- Při přidávání nových endpointů vždy používat `/api/` prefix v frontend klientu
- Při generování URL v backendu vždy zahrnout `/api/` prefix
- Platí pro: `/api/auth/`, `/api/experiments/`, `/api/images/`, `/api/metrics/`, `/api/ranking/`, `/api/proteins/`, `/api/embeddings/`

### Perzistence obrázků v chatu (OPRAVENO 2026-07-19)

**Symptom:** Obrázky (grafy, segmentační overlaye, stránky dokumentů) zmizely
ze starších konverzací - v historii zůstal jen rozbitý `<img>`.

**Dvě nezávislé příčiny:**

1. **24h reaper.** Grafy se ukládaly do `data/uploads/temp/`, které
   `cleanup_old_temp_files(max_age_hours=24)` maže **při každém startu backendu**
   (`main.py`). Markdown odkaz zůstal navždy v `chat_messages.content`.
2. **Chybějící volume.** `docker-compose.*.yml` mountoval jen `./data/uploads`,
   ale `data/rag_documents/` a `data/rag_passages/` jsou **sourozenci** uvnitř
   `/app/data`. Ležely tedy na zapisovatelné vrstvě kontejneru a **každý rebuild
   je smazal**, zatímco DB řádky i embeddingy zůstaly a ukazovaly do prázdna.

**Řešení (aplikováno):**
- Compose mountuje **celé `./data:/app/data`** - ne jednotlivé podadresáře.
- Obrázky generované agentem jdou do `settings.chat_image_dir`
  (`data/chat_images/{user_id}/`), který reaper nemaže a který je **mimo**
  `uploads/` (tedy mimo veřejný StaticFiles mount).

**Pravidlo:** Cokoliv, na co odkazuje uložená zpráva, **nesmí** ležet
v `uploads/temp/` ani mimo mountovaný `./data`.

### Servírování uživatelských souborů (OPRAVENO 2026-07-19)

**`/uploads` je nezabezpečený `StaticFiles` mount** (`main.py`) - cokoliv pod ním
je čitelné bez přihlášení. Exporty tam ležely se jménem
`experiment_{name}_{timestamp}.xlsx`, tedy s **vteřinovým timestampem a bez
náhodné složky** → kdokoliv zvenčí si mohl uhádnout URL a stáhnout cizí měření.

**Řešení:** exporty i obrázky agenta jsou **mimo `upload_dir`** a jdou přes
autentizovaný router `routers/user_files.py`:

| Obsah | Adresář | URL |
|-------|---------|-----|
| Exporty | `data/exports/{user_id}/` | `/api/exports/{user_id}/{file}` |
| Grafy a overlaye | `data/chat_images/{user_id}/` | `/api/chat-images/{user_id}/{file}` |

Jména mají `secrets.token_hex(8)` navíc (`prepare_export_target`), takže nejsou
uhodnutelná ani při znalosti vzoru.

**Pravidlo:** cokoliv, co patří konkrétnímu uživateli, **nikdy nedávej pod
`data/uploads/`**. Token: `processImageUrl` (`lib/utils.ts`) ho doplňuje
`/api/` cestám, které renderuje jako **obrázky**; download odkazy (exporty)
`<a>` ho přidávají ručně v `MessageBubble.tsx` (anchor `processImageUrl`
neprochází).

⚠️ **Práva na `./data`:** backend běží jako `app` (uid 1000). Když přidáš nový
podadresář v `data/`, který si aplikace vytváří sama, musí být `./data`
zapisovatelné pro uid 1000 — jinak backend spadne při importu na
`PermissionError` a crash-loopuje. Bez sudo:
```bash
docker run --rm -v $(pwd)/data:/dst alpine chown -R 1000:1000 /dst/<novy_adresar>
```

### ACL v agentovi

Čtecí dotazy používají `experiment_owner_filter` (SSOT z `utils/groups.py`),
takže agent vidí i experimenty sdílené přes skupinu — stejně jako UI.
`group_id` se resolvuje **jednou za tah** v `generate_response` a předává do
`execute_tool(..., group_id)`; default `None` = jen vlastník (fail-closed).

**Dokumenty mají druhou, paralelní ACL plochu** (od 2026-07-21): `rag_documents`
se sdílí stejným způsobem přes `document_scope` (listing/search) a
`document_read_scope` (fetch-by-id) v `models/rag_document.py`. Sdílejí se **jen
knihovní** dokumenty — group term je **vždy** AND-gated přes `thread_id IS NULL`,
takže přílohy konverzace se nikdy nerozšíří na skupinu. Tentýž invariant je ručně
zopakovaný na dalších dvou místech: raw-SQL `owner_clause` v
`rag_service.search_documents` a `_inject_user_id_filter` v agentově SQL toolu
(ten widenuje `experiments` i `rag_documents`). **Když měníš jedno, zkontroluj
všechny čtyři** — testy v `tests/unit/test_document_acl.py` zamykají *strukturu*
SQL (ne jen substring), takže záměna `and_`→`or_` shodí test.

⚠️ **Zápisy** (`manage_experiment`, `redetect_cells`, a u dokumentů
`delete_document` / `reindex_document`) zůstávají striktně na vlastníkovi -
skupina dává právo číst, ne měnit. Ty dvě dokumentové funkce schválně používají
holý `RAGDocument.user_id == user_id` a **nesmí** se „uklidit" na
`document_read_scope`.

### Komprese obrázků

Naměřeno na reálných datech - PNG je pro tenhle obsah nejhorší volba:

| Obsah | Formát | Úspora |
|-------|--------|--------|
| Grafy z matplotlibu (ploché barvy) | **WebP lossless** | ~78 % (25,9 → 5,8 kB), bez ztráty kvality |
| Stránky dokumentů, MIP, overlaye (fotografické) | **WebP q85** | ~70 % |

U syntetických grafů je lossless WebP **menší než** lossy q85 - nepoužívat q85 na grafy.
Formát stránek řídí `settings.rag_page_format` / `rag_page_quality`.
MIME typ se odvozuje z přípony (`rag_service.image_mime_type`), ne natvrdo.

### Z-index a layout problémy (ČASTÉ!)

**Symptom:** Dropdown menu, modály nebo jiné plovoucí elementy jsou překryté jinými komponentami a nejsou vidět.

**Příčina:** Nedostatečný z-index nebo chybějící `position: relative` na parent elementu.

**Prevence:**
- Dropdown menu vždy používat `z-50` nebo vyšší (ne `z-10`)
- Modály používat `z-[100]` nebo vyšší
- Pokud dropdown nepřekrývá ostatní sekce, zkontrolovat parent element - může potřebovat `relative` a vlastní `z-index`
- Při vytváření nových floating elementů (dropdown, tooltip, popover) VŽDY testovat překrývání s okolními komponentami

**Typické hodnoty z-index:**
- `z-10` - mírně nad ostatními (nestačí pro dropdown přes jiné sekce!)
- `z-50` - dropdown menu, floating elements
- `z-[100]` - modály, dialogy
- `z-[999]` - kritické overlays (loading screens, etc.)

### Mazání cell crops s ranking comparisons

**Chování:** Při mazání cell crop, který má ranking comparisons, API vrátí 409 Conflict s upozorněním.

**Příčina:** Cell crops mají CASCADE DELETE na comparisons tabulku - smazání crop smaže i historii porovnání.

**Řešení:** Pro potvrzení smazání přidat query parametr `?confirm_delete_comparisons=true`:
```
DELETE /api/images/crops/{id}?confirm_delete_comparisons=true
```

## 🔍 Vision RAG System (Chat s dokumenty)

**Architektura:** Systém používá **Vision RAG** - dokumenty se zpracovávají jako obrázky, NE jako extrahovaný text.

### Proč Vision RAG?

1. **Lepší kvalita** - Gemini přímo "čte" stránky jako obrázky, zachovává layout, tabulky, grafy
2. **Univerzálnost** - Funguje pro jakýkoli PDF (skenované, vědecké články, prezentace)
3. **Bez OCR závislosti** - Nepotřebuje Tesseract ani jiné OCR nástroje

### Jak to funguje

1. **Upload PDF** → Stránky se renderují jako PNG obrázky (150 DPI)
2. **Indexování** → Qwen VL encoder vytvoří visual embeddings pro každou stránku
3. **Vyhledávání** → Semantic search pomocí pgvector nad visual embeddings
4. **Čtení** → Agent volá `get_document_content` → stránky se pošlou jako base64 obrázky do Gemini
5. **Gemini Vision** → AI přečte obsah přímo z obrázků stránek

### Klíčové soubory

| Soubor | Účel |
|--------|------|
| `backend/services/document_indexing_service.py` | Upload, rendering PDF→PNG, embedding |
| `backend/services/rag_service.py` | Vyhledávání, `get_document_content` s base64 obrázky |
| `backend/services/gemini_agent_service.py` | Agent s vision - posílá obrázky do Gemini |
| `backend/ml/rag/qwen_vl_encoder.py` | Qwen VL model pro visual embeddings |

### Přístup agenta k dokumentům (nástroje)

Agent má plný přístup ke všem nahraným dokumentům uživatele:
- `list_documents` — všechny dokumenty + metadata (typ, velikost, počet stran, data)
- `search_documents` — sémantické vyhledávání; `limit` = kolik stran (default **10**),
  `document_ids` = omezit na konkrétní dokumenty
- `get_document_content` — agent si **čte** stránky přes vision (default prvních 10, max 10/volání)
- `show_document_pages` — **zobrazí** celé stránky uživateli (markdown obrázky, default 10, max 10)
- `extract_document_region` — vyřízne a zobrazí konkrétní oblast (obrázek/tabulku)

⚠️ `get_document_content` capuje počet stran i když je předán `page_numbers` (dřív ne →
40stránkové PDF zaplavilo kontext).

### ⚠️ HF cache: Qwen VL encoder se nenačte (PermissionError) — OPRAVENO 2026-07-20

**Symptom:** sémantické vyhledávání (`search_documents`, `semantic_search`,
`search_fov_images`) vrací chybu; v logu
`PermissionError ... /app/.cache/huggingface/hub/models--Qwen--Qwen3-VL-Embedding-2B/refs/main`.

**Příčina:** named volume `huggingface_cache` měl Qwen model **vlastněný rootem**,
ale backend běží jako `app` (uid 1000) → nemůže model načíst. Root ownership
vznikl historickým stažením modelu rootem; build-time chown se volume netýká.

**Řešení:**
```bash
docker exec -u 0 maptimize-backend chown -R app:app /app/.cache/huggingface/hub
```
Kontejner běží jako `app`, takže nové soubory zůstávají app-owned — oprava je trvalá,
dokud do volume nezapíše root.

### DŮLEŽITÉ pro implementaci

- **NIKDY neextrahovat text z PDF** - vše se řeší přes vision
- **Stránky jsou PNG obrázky** - uložené v `data/rag_documents/{user_id}/doc_{id}_pages/`
- **Embeddings jsou visual** - 2048-dim vektory z Qwen VL, ne text embeddings
- `extracted_text` sloupec v DB je NULL a to je OK - nepoužívá se

**Důvod:** Prevence nechtěné ztráty dat - uživatel musí explicitně potvrdit, že chce smazat i comparison historii.

### 📚 Discovery: import článků z Europe PMC ("Find sources")

Uživatel v Documents modálu popíše, co hledá (téma / nalepené názvy / DOI), vybere
zaškrtávátky a naimportuje. Backend: `services/paper_discovery_service.py` +
`POST /api/rag/discover` a `/api/rag/discover/import`.

⚠️ **Europe PMC NENÍ v `APPROVED_APIS`** — discovery má vlastního httpx klienta.
`APPROVED_APIS` hlídá agentův generický `call_external_api`, což je jiná cesta; přes ni
se na Europe PMC dostat nedá (a nemá to tak být).

**Importovatelnost NIKDY neurčuj podle `isOpenAccess`.** Ověřeno živě 2026-07-22:
bioRxiv preprinty vracejí `isOpenAccess: "N"` a přitom `availability: "Free"`. Jediné
správné kritérium je záznam ve `fullTextUrlList` s `documentStyle == "pdf"`
**a** `availability ∈ {Open access, Free}` **a** `site == "Europe_PMC"` — záznamy se
`site: "PubMedCentral"` vedou na `ncbi.nlm.nih.gov`, které serverovému klientovi vrátí
bot-check HTML místo PDF. Název časopisu je `journalInfo.journal.title` (ploché
`journalTitle` chodí prázdné).

**Free-text dotaz překládá Gemini** (`rewrite_topic_query`). Bez toho hledání
selhává na nejběžnějším dotazu: „papers from lab of dr. carsten janke" se pošle jako
klíčová slova a vrátí 0 z 6 relevantních (AlphaFold2, sborníky). Po překladu na
`AUTH:"Janke C" AND microtubule` vrací 8 z 8. Platí:

- **jedno Gemini volání na free-text hledání, NULA u DOI a seznamu názvů** (ty jsou už
  strukturované) a nula, když vstup sám obsahuje field syntax. Cena je tvrdý požadavek —
  testy to vynucují přes `assert_not_awaited()`.
- Jakékoli selhání (chybí klíč, timeout, výjimka, prázdný výstup) → **fallback na původní
  text**, hledání nikdy nespadne kvůli překladu.
- Skutečně odeslaný dotaz se vrací jako `effective_query` a UI ho ukáže („Searched as: …"),
  takže je vidět, když překlad dopadne špatně.
- ⚠️ Europe PMC na rozbitý dotaz **nevrací chybu** — vrátí HTTP 200 a nula výsledků. Špatný
  překlad se proto tváří jako „nic nenalezeno"; proto se při prázdném výsledku hledání
  jednou zopakuje s původním textem.

**Konstanty a jejich historie** (`paper_discovery_service.py`):
| Konstanta | Hodnota | Proč |
|-----------|---------|------|
| `EPMC_TIMEOUT` | 45 s | latence kolísá; naměřeno 0,18 s i 12,5 s na tomtéž dotazu, 20 s bylo málo |
| `PDF_READ_TIMEOUT` | 60 s | stahování PDF (i desítky MB) |
| `MAX_PDF_BYTES` | 100 MB | zrcadlí strop upload endpointu |
| `EPMC_MAX_CONCURRENCY` | 4 | politeness vůči EBI — nenech se zablokovat |
| `_QUERY_REWRITE_TIMEOUT` | 20 s | musí zůstat výrazně pod EPMC_TIMEOUT, ať stojící překlad nenafoukne čekání |
| `_MAX_REWRITTEN_QUERY_LEN` | 500 | model má vrátit JEN dotaz, ale stejně přidá prózu/fence — tvrdý strop |

Stahování PDF jde přes `gemini_agent_service._is_safe_url` (SSRF) s **ručním
následováním redirectů a revalidací každého hopu** — reálné EPMC PDF URL redirectují,
takže tahle větev musí být otestovaná (jednou už tu byl bug, který spadl na každém
skutečném stažení, protože žádný test redirect nevracel).

### Deduplikace dokumentů (od 2026-07-22)

Klíč je **`sha256` obsahu** v `rag_documents.content_hash`. Počítá se v
`save_uploaded_document()` — jediném hrdle, kterým jde ruční upload **i** discovery
import, takže obě cesty dedupují automaticky. Vrací `(document, created)`; při
`created=False` se **nesmí** plánovat indexace ani nic zapisovat (dokument může patřit
kolegovi — zápisy zůstávají na vlastníkovi).

Rozsah hledání je `document_dedupe_scope()` v `models/rag_document.py`, **záměrně užší
než `document_scope`**: knihovní upload dedupuje napříč skupinou, příloha chatu jen proti
vlastním přílohám téhož threadu. Kdyby se hranice překročila, knihovní dokument by zmizel
při smazání konverzace.

⚠️ **Dokumenty ve stavu `FAILED` se nededuplikují.** Jinak by uživatel dostal rozbitý
dokument a přišel by o jedinou možnost nápravy — re-upload by se tiše vyhodnotil jako
duplicita.

⚠️ **`PENDING`/`PROCESSING` se naopak dedupují** (jinak by dvojklik během indexace založil
dva dokumenty). Aby to nebyla past, `fail_orphaned_indexing()` v `main.py` lifespanu při
startu překlopí zaseknuté řádky na `FAILED` — indexace běží jako `BackgroundTask`, který
restart kontejneru nepřežije, a CLAUDE.md restart předepisuje po každé změně kódu. Bez
toho by zaseknutý dokument navždy polykal re-uploady a u sdílené knihovny by to nešlo
opravit nikomu kromě vlastníka.

⚠️ **Dedup NENÍ chráněný unique constraintem** — je to check-then-act. Dva současné
uploady téhož nového souboru projdou oba (cena: jeden zbytečný běh indexace, sám se
nezhorší). Vědomé rozhodnutí: správný klíč je `(content_hash, vlastník/skupina, thread_id)`
a špatně napsaný constraint by odmítal legitimní uploady. Discovery import je bezpečný
konstrukcí — `asyncio.gather` paralelizuje jen stahování, ukládací smyčka je sekvenční nad
jednou session. **Kdyby někdo chtěl paralelizovat i ukládání, tahle vlastnost tiše zmizí.**

⚠️ **Testy dedup dotazu asertuj na `stmt.whereclause`, NIKDY na `str(stmt)`.** `str()`
vyrenderuje i seznam sloupců v `SELECT`, takže `assert "content_hash" in str(stmt)` projde
i tehdy, když se filtruje podle úplně jiného sloupce. Reálně se to stalo: přepnutí dedupu
na porovnávání podle názvu souboru nechalo všech 1626 testů zelených.

### PDF fallback při importu

`pdf_urls_from_result()` vrací **seznam** kandidátů (dřív jen první odkaz).
`fetch_paper_pdf()` je zkouší v pořadí: všechny EPMC odkazy → Unpaywall → vzory preprint
serverů (Research Square, bioRxiv/medRxiv, odvozené z DOI bez extra requestu). Resolvery
se volají **až když všechny EPMC odkazy selžou**, takže běžná cesta nestojí nic navíc —
testy to hlídají přes `assert_not_awaited()`.

⚠️ **Když `pdf_urls` je prázdné, import se odmítne i s DOI.** Prázdný seznam znamená, že
picker článek ukázal jako paywallovaný; fallback má zachránit mrtvý odkaz, ne rozšířit,
co se považuje za volně dostupné. (Test `test_import_refuses_paywalled_paper` to hlídá —
při implementaci tuhle hranici jednou zrušil a test to chytil.)

Chyba se hlásí jako **PRVNÍ selhání** (kandidát, kterému věříme nejvíc), ne poslední a ne
„3 kandidáti selhali": rozdíl mezi 403, špatným content-type a překročením 100 MB je to,
co uživateli řekne, jestli zkusit znovu, nebo si PDF stáhnout ručně. Poslední selhání by
bylo skoro vždy vymyšlená 404 z `preprint_pdf_urls`, který u DOI `10.1101/` schválně
zkouší biorxiv i medrxiv s vědomím, že jeden neexistuje.

⚠️ **`fetch_pdf` musí převádět transportní chyby httpx na `PdfFetchError`** a `attempt()`
navíc chytá i `Exception`. Nespadlý connect / read timeout je nejčastější podoba mrtvého
odkazu — když unikne, přeskočí celý zbytek řetězu, tedy přesně tu záchranu, kvůli které
řetěz existuje.

⚠️ **`PaperResult` nemá `pdf_url` (jednotné číslo).** Byla to past: `fetch_pdf(paper.pdf_url)`
se čte přirozeně, přeloží se a tiše obejde celý fallback. Importovatelnost je
`bool(pdf_urls)`, stahování `fetch_paper_pdf(paper)`.

## 🌐 Internacionalizace (i18n)

**CRITICAL: Každý textový řetězec v UI musí používat i18n wrapper!**

### Pravidla pro překlady

1. **Nikdy nepoužívat hardcoded stringy** - vždy použít `useTranslations()` hook
2. **Přidat překlady do obou souborů** - `frontend/messages/en.json` a `frontend/messages/fr.json`
3. **Používat strukturované klíče** - např. `settings.profile.title`, ne jen `profileTitle`

### Příklad použití

```typescript
import { useTranslations } from "next-intl";

export function MyComponent() {
  const t = useTranslations("myNamespace");
  const tCommon = useTranslations("common");

  return (
    <div>
      <h1>{t("title")}</h1>
      <button>{tCommon("save")}</button>
    </div>
  );
}
```

### Struktura překladových souborů

- `common` - Sdílené tlačítka (Save, Cancel, Delete, Loading, etc.)
- `auth` - Přihlášení/registrace
- `navigation` - Položky navigace
- `dashboard` - Dashboard stránka
- `experiments` - Experimenty
- `images` - Upload a zpracování obrázků
- `settings` - Nastavení
- `ranking` - Ranking stránka
- `proteins` - Názvy proteinů

### Při přidávání nových textů

1. Přidej klíč do `frontend/messages/en.json`
2. Přidej překlad do `frontend/messages/fr.json`
3. Použij `t("key")` v komponentě
4. **NIKDY** nepřidávej hardcoded text do JSX!

## 🧪 Backend test coverage

**Měření coverage:** `bash run-coverage.sh` (z rootu repa). Postaví **izolované** prostředí (`docker-compose.test.yml` — vlastní ephemeral pgvector + redis, **nikdy se nedotkne prod DB**) a spojí tři běhy:
- **Run B** – `backend/tests/test_*.py` (httpx integrační testy proti instrumentovanému serveru) → pokrývá těla route handlerů.
- **Run C** – `backend/tests/unit/` (in-process unit testy s mockovaným DB/ML/genai) → pokrývá services + ML/externí cesty, které integrace offline nedosáhne.
- **Run A** – import appky pod coverage → module-level řádky.
Výstup: `backend/coverage.json` + `backend/htmlcov/`. **Cíl: ~99 % line coverage, celá suite musí zůstat zelená** (autoritativní čísla jsou v generovaném `coverage.json`, ne v tomto textu).

### ⚠️ Coverage gotchas (proč ten harness vypadá složitě)
Na stacku torch 2.11 + coverage 7.x + greenlet + asyncpg narazíš na tvrdé pády — řešení je v `backend/tests/_coverage_launcher.py`:
1. `import torch` pod aktivním coverage tracerem → `RuntimeError: _has_torch_function already has a docstring`. **Fix:** importovat celou appku PŘED `coverage.start()` (launcher) a v unit testech mockovat `torch` v `sys.modules`.
2. SQLAlchemy-async (asyncpg) běží v greenletu → coverage C-tracer při přepínání greenlet stacku **segfaultuje**. **Fix:** server importuje appku před coverage; unit testy mockují DB (`mock_db` AsyncMock → žádný greenlet).
3. `concurrency = greenlet` v `.coveragerc` + ctrace core (NE sysmon).
4. Unit testy běží **offline + CPU-only** (`HF_HUB_OFFLINE=1`, `CUDA_VISIBLE_DEVICES=`) — nikdy nestahuj modely ani neber prod GPU.

### Psaní unit testů (`backend/tests/unit/`)
- `tests/unit/conftest.py` dává `mock_db` (AsyncMock AsyncSession) a `make_result(scalar=, scalars_all=, first=, fetchall=, rowcount=)`.
- `pytest.ini` má `asyncio_mode = auto` → async testy jako prosté `async def`.
- Importuj helper přes `from tests.unit.conftest import make_result` (bare `from conftest` nefunguje).
- Routery se testují přímým voláním handler-coroutin s `current_user=SimpleNamespace(...)`, `db=mock_db`; služby mockuj na hranici routeru (`patch("routers.X.<name>", ...)`).

## 🧪 E2E Testování (Playwright)

### Struktura testů

```
frontend/e2e/
├── fixtures/         # Test fixtures a helpers
│   ├── auth.fixture.ts    # Authenticated page fixture
│   ├── global-setup.ts    # Creates auth state before tests
│   └── test-data.ts       # Test data generators
├── pages/            # Page Object Models
│   ├── AuthPage.ts
│   ├── DashboardPage.ts
│   ├── ExperimentPage.ts
│   ├── EditorPage.ts
│   └── RankingPage.ts
├── mocks/            # API mocks pro ML endpointy
│   └── ml-endpoints.ts
├── tests/            # Test soubory
│   ├── auth/login.spec.ts
│   ├── experiments/crud.spec.ts
│   ├── images/upload.spec.ts
│   ├── ranking/comparison.spec.ts
│   ├── editor/navigation.spec.ts
│   └── settings/preferences.spec.ts
└── playwright.config.ts
```

### Spuštění testů

```bash
cd frontend

# Všechny testy
npm run test:e2e

# Pouze kritické testy (před commitem)
npm run test:e2e:critical

# S UI pro debugging
npm run test:e2e:ui

# Debug mode
npm run test:e2e:debug

# Zobrazit HTML report
npm run test:e2e:report
```

### Kdy spouštět testy

| Změna | Příkaz |
|-------|--------|
| Nová stránka/route | `npm run test:e2e -- e2e/tests/[feature]/` |
| Změna formuláře | `npm run test:e2e -- e2e/tests/[feature]/` |
| Změna API endpointu | `npm run test:e2e -- --grep "[endpoint]"` |
| Auth logika | `npm run test:e2e -- e2e/tests/auth/` |
| **Před commitem** | `npm run test:e2e:critical` |
| **Před PR** | `npm run test:e2e` |

### Přeskočit testy když:
- Jen dokumentace změny
- Jen config změny (bez runtime dopadu)
- Backend-only změny (pokryto pytest)

### Priorita testů

| Tag | Popis | Kdy spouštět |
|-----|-------|--------------|
| `@critical` | Login, CRUD, Upload, Ranking | Před každým commitem |
| `@important` | Editor, Settings | Před PR |
| (bez tagu) | Nice-to-have testy | Před release |

### Psaní nových testů

1. **Použij Page Object Model** - všechny selektory v `e2e/pages/`
2. **Mockuj ML endpointy** - `import { mockMLEndpoints } from "../../mocks/ml-endpoints"`
3. **Používej testovací data** - `import { generateTestId } from "../../fixtures/test-data"`
4. **Taguj kritické testy** - `test.describe("Feature @critical", ...)`
5. **Čisti po sobě** - smaž vytvořené experimenty v `afterEach`

### Příklad testu

```typescript
import { test, expect } from "../../fixtures/auth.fixture";
import { ExperimentPage } from "../../pages";
import { generateTestId, deleteTestExperiment } from "../../fixtures/test-data";

test.describe("Feature @critical", () => {
  let experimentPage: ExperimentPage;
  const createdIds: number[] = [];

  test.beforeEach(async ({ authenticatedPage }) => {
    experimentPage = new ExperimentPage(authenticatedPage);
  });

  test.afterEach(async ({ authenticatedPage }) => {
    for (const id of createdIds) {
      await deleteTestExperiment(authenticatedPage, id);
    }
  });

  test("should do something", async ({ authenticatedPage }) => {
    // Test implementation
  });
});
```

### CI/CD

E2E testy běží automaticky v GitHub Actions:
- **Push do main/develop** - plná sada testů
- **Pull Request** - pouze smoke testy (@critical)
- **Artifacts** - HTML report dostupný po každém běhu
