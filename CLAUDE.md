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

## 🤖 MCP — ovládací plocha agenta (Claude connector)

Agent (Claude) ovládá Maptimize **výhradně přes MCP server** `mcp-server/`
(balík `maptalk_mcp`, HTTP transport, OAuth 2.0 PKCE + per-user PAT). Žádný in-app
LLM tu není — dřívější Gemini chat agent byl smazán (commit `2ba9181`) a agentní
vrstva se přesunula do Claude přes hostovaný per-user konektor.

**CRITICAL — SSOT pravidlo: každý nový/změněný aplikační endpoint přidej i do MCP.**
MCP je to, čím agent appku „vidí" a „ovládá"; když endpoint v MCP chybí, pro agenta
neexistuje. Přidání toolu je většinou **jen YAML záznam** v
`mcp-server/maptalk_mcp/tools.yaml`:
- jednoduchý REST → generický handler `http_json` (GET/DELETE/PATCH bez těla) nebo
  `http_post_json` (POST/PATCH s JSON tělem); `method`/`path`/`params` jsou v YAML.
- vlastní handler v `handlers.py` **jen** pro: multipart upload (`upload_image`,
  `index_document`), binární/obrázkové odpovědi (`read_page_region`), nebo tělo
  s explicitním `null` (`move_document`). Nový handler registruj v `HANDLERS`.
- pole se deklaruje `type: array` + `items: <typ>` (registry to umí od 2026-07-24).
- anotace `readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint` řídí
  consent UX v klientovi — **nejsou** bezpečnostní brána (autorizace je server-side).

`tools.yaml` je mountovaný read-only a **hot-reloaduje se** (změna textu toolu
nevyžaduje rebuild); změna `handlers.py`/`registry.py`/`server.py` vyžaduje **rebuild
image** `maptimize-mcp`. Verzi serveru (`SERVER_VERSION` v `server.py`) zvyš při
změně tool kontraktu.

⚠️ **Connector token nesmí narazit na `require_interactive_user`** (`utils/security.py`).
Ten blokuje OAuth/PAT tokeny u account-sensitive akcí (heslo/e-mail/admin). Feature
routery (`experiments`/`images`/`proteins`/`query`) používají `get_current_user`, takže
connector projde. Endpoint chráněný `require_interactive_user` do MCP nepatří (vrátí 403).

**ACL se propisuje samo.** MCP je čistý HTTP klient; token protéká do backendu, kde
platí stejná pravidla jako pro člověka: **čtení skupinově sdílené**
(`experiment_owner_filter`, SSOT `utils/groups.py`), **zápisy do experimentů/obrázků
owner-only** (re-check `obj.user_id == current_user.id` → 403). ⚠️ **Proteiny jsou
sdílená referenční data** — `MapProtein` nemá `user_id`, takže je smí měnit/mazat
kdokoliv přihlášený (není to bug). Když přidáš write endpoint, zkontroluj, že re-check
vlastníka SKUTEČNĚ je v handleru — `update_experiment_protein` ho omylem neměl a šel
přes něj group-write (opraveno v PR #43). Maže se kaskádově (experiment → obrázky →
cropy) a nevratně — proto destruktivní tooly nesou `destructiveHint`.

### query_database — read-only SQL (SSOT `services/sql_query_service.py`)

Agent má read-only okno do DB: `POST /api/query` → `sql_query_service.run_query()`.
Jen **SELECT** nad whitelistem tabulek; per-user ACL predikát se injektuje **až po
validaci** (model ho nevidí a neobejde). Pořadí: **validace → whitelist → injekce
predikátu → exekuce.** Chyby se hlásí jako HTTP 400 s opravitelnou hláškou.

Zákeřné třídy chyb (zamčené v `tests/unit/test_sql_query_service.py`):
- ⚠️ **Predikát se MUSÍ psát přes ALIAS tabulky**, ne přes jméno. Postgres zahodí
  jméno tabulky, jakmile má alias, takže `FROM experiments e ... WHERE
  experiments.user_id=…` spadne na `invalid reference to FROM-clause entry`.
  `_table_references()` vrací `(jméno, reference)` a predikát kvalifikuje `reference`.
- ⚠️ **Self-join = jeden predikát na KAŽDÝ alias** — proto `_table_references()` vrací
  **seznam**, ne set; kolaps na set nechá druhý alias nescopovaný a protečou přes něj
  cizí řádky.
- ⚠️ **Nekorelované joiny jsou zakázané** (comma / CROSS / NATURAL) a `LIMIT`/`OFFSET`
  v dotazu taky (LIMIT přidává `run_query` sám naclampovaný, aby `LIMIT 100000` neobešel
  strop). Vyžaduje se explicitní `JOIN ... ON`.
- ⚠️ **Nepřímé tabulky NEVĚŘÍ modelovu `ON`.** `images`/`cell_crops`/`rag_document_pages`
  nemají `user_id`; scopují se přes rodiče, ale `ON c.map_protein_id = e.map_protein_id`
  (nebo `ON true`) by protáhl cizí řádky. Proto `_scoping_plan` **sám injektuje FK
  korelaci** (`child.fk = parent.pk`) nezávisle na modelově ON, a vyžaduje **celý řetěz**
  (`cell_crops → images → experiments`; chybějící/dvojznačný rodič = odmítnuto). Toto byla
  reálná díra nalezená v PR #43 review.
- ⚠️ **Schema hint** (`SQL_SCHEMA_HINT`) je zrcadlený v popisu toolu `query_database`
  v `tools.yaml` — **při změně sloupců aktualizuj obojí** (model bez názvů sloupců SQL
  neuhodne).

### Testování MCP / agenta
- MCP: `mcp-server/.venv/bin/python -m pytest` (mockovaný backend přes
  `httpx.MockTransport`, žádný live backend/GPU).
- Backend: viz „Backend test coverage" (`run-coverage.sh`); rychlý běh unit testů viz
  memory `reference_fast_unit_test_runner`.
- Živě přes připojený konektor: `create_experiment → upload_image → process_images →
  list_cell_crops → query_database → delete_experiment` a ověř, že ACL drží.

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

### ACL (sdílená pro UI i MCP)

Čtecí dotazy používají `experiment_owner_filter` (SSOT z `utils/groups.py`),
takže je vidět i experimenty sdílené přes skupinu — stejně pro UI i MCP konektor.
`group_id` resolvuje endpoint přes `get_user_group_id(current_user.id, db)`;
default `None` = jen vlastník (fail-closed).

**Dokumenty mají druhou, paralelní ACL plochu** (od 2026-07-21): `rag_documents`
se sdílí stejným způsobem přes `document_scope` (listing/search) a
`document_read_scope` (fetch-by-id) v `models/rag_document.py`. Sdílejí se **jen
knihovní** dokumenty — group term je **vždy** AND-gated přes `thread_id IS NULL`,
takže přílohy konverzace se nikdy nerozšíří na skupinu. Tentýž invariant je ručně
zopakovaný na dalších dvou místech: raw-SQL `owner_clause` v
`rag_service.search_documents` a `_inject_user_id_filter` v
`services/sql_query_service.py` (ten widenuje `experiments` i `rag_documents`).
**Když měníš jedno, zkontroluj všechny čtyři** — testy v
`tests/unit/test_document_acl.py` zamykají *strukturu* SQL (ne jen substring),
takže záměna `and_`→`or_` shodí test.

⚠️ **Zápisy** zůstávají striktně na vlastníkovi (re-check `obj.user_id ==
current_user.id` → 403) — skupina dává právo číst, ne měnit. U dokumentů
`delete_document` / `reindex_document` schválně používají holý
`RAGDocument.user_id == user_id` a **nesmí** se „uklidit" na `document_read_scope`.

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

1. **Lepší kvalita** - agent (Claude přes MCP) přímo "čte" stránky jako obrázky, zachovává layout, tabulky, grafy
2. **Univerzálnost** - Funguje pro jakýkoli PDF (skenované, vědecké články, prezentace)
3. **Bez OCR závislosti** - Nepotřebuje Tesseract ani jiné OCR nástroje

### Jak to funguje

1. **Upload PDF** → Stránky se renderují jako PNG obrázky (150 DPI)
2. **Indexování** → Qwen VL encoder vytvoří visual embeddings pro každou stránku
3. **Vyhledávání** → Semantic search pomocí pgvector nad visual embeddings
4. **Čtení** → agent (Claude) volá MCP `read_document_pages` → stránky přijdou jako base64 obrázky
5. **Vision** → agent přečte obsah přímo z obrázků stránek (Gemini se používá už jen pro pomocnou extrakci pasáží v `rag_service`)

### Klíčové soubory

| Soubor | Účel |
|--------|------|
| `backend/services/document_indexing_service.py` | Upload, rendering PDF→PNG, embedding |
| `backend/services/rag_service.py` | Vyhledávání, `get_document_content` s base64 obrázky |
| `mcp-server/maptalk_mcp/` | MCP konektor — agent (Claude) čte stránky přes vision |
| `backend/ml/rag/qwen_vl_encoder.py` | Qwen VL model pro visual embeddings |

### Přístup agenta k dokumentům (nástroje)

Agent (Claude přes MCP) má plný přístup ke všem dokumentům uživatele (názvy = MCP tooly
v `tools.yaml`):
- `find_documents` / `list_folders` — procházení knihovny podle metadat / složek
- `search_documents` — sémantické vyhledávání (vrací page images; `return=refs` = jen odkazy)
- `read_document_pages` — **čte** stránky přes vision (default 5, **max 10/volání**)
- `read_page_region` — **zoom** na oblast stránky (vysoké DPI, čitelné malé figury/tabulky)
- zápisy: `index_document` / `index_text`, `reindex_document`, `delete_document`,
  `move_document`, `create_folder`

⚠️ `read_document_pages` capuje počet stran na 10 i při explicitním výběru (dřív ne →
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

Stahování PDF jde přes `paper_discovery_service._is_safe_url` (SSRF) s **ručním
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
