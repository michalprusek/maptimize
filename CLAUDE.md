# Maptimize - Lokální konfigurace (UTIA server)

CRITICAL: před každou novou implementací zkontroluj, jestli už to není naimplementovano a zda by se to nedlao využít. Cti SSOT a DRY principy!

CRITICAL: po každé nové implementaci spust code-simplifier agenty ať projdou celé repo a zjednoduší kód a zkontrolujou DRY a SSOT principy.

Neboj se dělat velké a rozsáhlé změny a mazat kód. Legacy implementace maž kdykoliv na ně narazíš. Codebase udržuje maximálně clean a přehlednou. 

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

**Důvod:** Prevence nechtěné ztráty dat - uživatel musí explicitně potvrdit, že chce smazat i comparison historii.

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
