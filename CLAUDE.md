# Maptimize - Lokální konfigurace (UTIA server)

## ⚠️ KRITICKÉ UPOZORNĚNÍ - PRODUKCE

sudo password: @sudo_password.txt

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
Fallback: `yolov8n.pt` (základní model)

## 🔧 Deploy & Rebuild

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
