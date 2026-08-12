# Zálohy a obnova

Zavedeno 2026-08-01. Do té doby existovaly jen ruční dumpy před rizikovými
migracemi — všechny na tomtéž disku jako produkční databáze, a `data/` (4,7 GB
snímků) nebyla zálohovaná nikdy.

## Co běží

| | |
|---|---|
| Kdy | denně **03:00** (`maptimize-backup.timer`, `Persistent=true`) |
| Kam | `/backup/maptimize/` na **`/dev/sdb1`** — samostatný disk, 503 GB |
| Skript | `scripts/backup.sh` |
| Retence | 14 dní (fakticky 15 — `-mtime +14`), **vždy ale min. 3 nejnovější** bez ohledu na stáří |
| Doba běhu | ~80 s, **bez výpadku aplikace** (~80 % z toho je `pg_dump`, škáluje s DB, ne s `data/`) |
| Cena místa | ~1,2 GB/den (dumpy se **ne**deduplikují) + 4,7 GB jednou na soubory → ustálený stav ~21 GB |

## Co se zálohuje a proč zrovna tohle

`pg_dump` **sám o sobě není záloha téhle aplikace**. Stav je ve třech nezávislých
úložištích a obnova potřebuje všechna:

1. **Postgres** — metadata + pgvector embeddingy (2048-dim).
2. **`data/`** — vlastní pixely. Řádky v DB na ně odkazují **cestou**, takže dump
   obnovený bez `data/` je databáze plná mrtvých odkazů. V malém se to už jednou
   stalo (viz CLAUDE.md, „Perzistence obrázků v chatu" — nenamountovaný
   `rag_documents/` zmizel při rebuildu, DB řádky i embeddingy zůstaly).
3. **`weights/best.pt`** — není v repu a backend bez něj nenastartuje.

Plus `.env`, které je gitignorované, takže záloha je jeho jediná kopie
(mode 600). Zbytek konfigurace i zdrojáky jsou na GitHubu — druhou kopii tady
nepotřebují.

Vynechává se `data/uploads/temp/`: `cleanup_old_temp_files()` ho maže při každém
startu backendu, takže tam z definice nic trvalého neleží.

## Kontrola, že to žije

```bash
cat /backup/maptimize/LAST_RESULT      # OK/FAIL + kdy naposledy
systemctl list-timers maptimize-backup.timer
journalctl -u maptimize-backup.service -n 30
tail -20 /backup/maptimize/logs/backup.log
```

`LAST_RESULT` má tři stavy: `OK`, `FAIL` a `RUNNING`. Skript ho na `RUNNING`
přepne hned po zamknutí a na `OK`/`FAIL` při odchodu — včetně odchodu na SIGTERM
(trapy na TERM/INT/HUP). **Zbylý `RUNNING` znamená SIGKILL** (OOM killer,
`TimeoutStopSec`) — jediný případ, který zachytit nejde.

⚠️ Kdyby timer přestal střílet úplně, `LAST_RESULT` si navždy drží poslední `OK`
a jen zestárne. **Kontroluj datum, ne slovo.** Selhání samotného unitu navíc
hlásí `OnFailure=maptimize-backup-failed.service` do journalu jako `err`.

### Když je tam FAIL

1. `tail -40 /backup/maptimize/logs/backup.log` — skript loguje důvod včetně
   stderru z ověřovacího kontejneru.
2. Rozliš „záloha je špatná" od „ověřovatel nešel spustit" — chybějící image
   nebo zaseknutý docker daemon vypadají jako vadný dump, ale řeší se opačně.
3. Nespoléhej na to, že to zítra vyjde samo: spusť `scripts/backup.sh` ručně a
   sleduj výstup.
4. Staré zálohy jsou v bezpečí — retence drží minimálně 3 nejnovější bez ohledu
   na stáří, takže série selhání archiv nevyprázdní.

## Obnova databáze

⚠️ **Nejdřív si vždycky zkus obnovu do throwaway kontejneru**, ne rovnou do
produkce. Ověřeno 2026-08-01 — tenhle postup projde:

```bash
DUMP=$(ls -1t /backup/maptimize/db/*.dump | head -1)

docker run -d --name restore-test \
  -e POSTGRES_USER=maptimize -e POSTGRES_PASSWORD=x -e POSTGRES_DB=maptimize \
  -v /backup/maptimize/db:/b:ro pgvector/pgvector:pg16

# `docker run -d` se vrátí dřív, než Postgres přijímá spojení — bez tohohle
# čekání další příkaz spadne na "connection refused".
docker exec restore-test bash -c 'until pg_isready -U maptimize -q; do sleep 1; done'

docker exec restore-test pg_restore -U maptimize -d maptimize --no-owner "/b/$(basename $DUMP)"
docker exec restore-test psql -U maptimize -d maptimize -c \
  "SELECT count(*) FROM cell_crops;"          # porovnej s produkcí
docker rm -f restore-test
```

Do **produkce** (jen když je DB opravdu ztracená — je to destruktivní):

```bash
DUMP=$(ls -1t /backup/maptimize/db/*.dump | head -1)   # zopakováno schválně
docker compose -f docker-compose.prod.yml stop maptimize-backend maptimize-mcp
docker cp "$DUMP" maptimize-db:/tmp/restore.dump

# ON_ERROR_STOP=1 je nutné: bez něj psql po chybě POKRAČUJE na další -c.
# Když DROP selže (stačí jedno živé spojení — třeba psql v jiném okně, kterým sis
# před chvílí počítal řádky), CREATE zahlásí "already exists", skript jede dál
# a pg_restore se pustí proti ŽIVÉ produkční databázi. WITH (FORCE) (PG13+)
# spojení odpojí sám.
docker exec maptimize-db psql -U maptimize -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS maptimize WITH (FORCE);" \
  -c "CREATE DATABASE maptimize;"

# CREATE EXTENSION tu NENÍ potřeba — dump nese `CREATE EXTENSION IF NOT EXISTS`
# pro vector i pg_trgm. (Ruční krok by navíc svedl k tomu vypsat jen `vector`
# a na `pg_trgm` zapomenout.)
docker exec maptimize-db pg_restore -U maptimize -d maptimize --no-owner \
  --exit-on-error /tmp/restore.dump
# ⚠️ Nenulový exit = STOP. Backend nespouštěj a zjisti proč.
docker compose -f docker-compose.prod.yml start maptimize-backend maptimize-mcp
```

## Obnova souborů

⚠️ **Obnovuj vždycky z `latest`, nikdy z „nejnovější vypadající" složky.**
`latest` se přesouvá až po úspěchu všech kroků, takže ukazuje na poslední
kompletní snapshot. Neúspěšný běh po sobě může nechat částečnou
`files/<TIMESTAMP>/` — a ta má nejnovější jméno.

⚠️ **DB a soubory ber ze stejného běhu.** `files/<TIMESTAMP>/` a
`db/maptimize_<TIMESTAMP>.dump` sdílejí razítko; míchat je znamená vyrobit si
přesně ty mrtvé odkazy, kvůli kterým se zálohuje obojí.

```bash
docker compose -f docker-compose.prod.yml stop maptimize-backend maptimize-mcp

SNAP=/backup/maptimize/files/latest
# Bez --delete: soubory navíc zůstanou. Je to záměr — konzervativnější při
# částečné obnově. Když potřebuješ přesnou kopii stavu, přidej --delete.
rsync -a "$SNAP/data/"    /home/cvat/maptimize/data/
rsync -a "$SNAP/weights/" /home/cvat/maptimize/weights/
# install, ne cp: `cp` na NEEXISTUJÍCÍ cíl vyrobí soubor podle umask (tady 0664,
# tedy čitelný pro skupinu i ostatní) — a to je přesně případ obnovy na čistý
# stroj. Záloha samotná je 600.
install -m 600 "$SNAP/env" /home/cvat/maptimize/.env

# backend bezi jako uid 1000; po obnove zkontroluj vlastnictvi
docker run --rm -v /home/cvat/maptimize/data:/dst alpine chown -R 1000:1000 /dst
docker compose -f docker-compose.prod.yml start maptimize-backend maptimize-mcp
```

⚠️ Snapshoty jsou pospojované **hardlinky**. Editovat soubor přímo v
`/backup/maptimize/files/*/` by ho změnil ve **všech** snapshotech naráz —
vždycky kopíruj ven, nikdy needituj uvnitř.

## Co tahle záloha NEPOKRÝVÁ

`sda` (produkce) i `sdb` (zálohy) jsou **virtuální disky téhož stroje**. Chrání
tedy před smrtí disku, rozbitou migrací, `rm -rf` a poškozeným filesystemem —
ale **ne** před ztrátou serveru nebo datastoru (požár, krádež, ransomware).
Off-site kopie zatím vědomě není; až bude potřeba, přidá se jako druhý timer,
který sype `/backup/maptimize/` jinam. Struktura na to je připravená.

Rovněž se nezálohují Docker volumes jiných projektů (`spheroseg_*`,
`cell-segmentation-hub_*`) — tenhle skript je jen pro Maptimize.

**Ani všechny Maptimize volumes nejsou v záloze, a je to záměr:**

| Volume | Proč ne |
|--------|---------|
| `maptimize_huggingface_cache` | Qwen VL encoder — stáhne se znovu. Po obnově na čistý stroj ale pozor na práva: `docker exec -u 0 maptimize-backend chown -R app:app /app/.cache/huggingface/hub` (viz CLAUDE.md), jinak encoder spadne na `PermissionError`. |
| `maptimize_redis` | jen cache |

Schema se aplikuje při startu (`create_all` + `ensure_schema_updates()`), takže
obnova staršího dumpu pod novějším kódem je normální případ a funguje —
chybějící sloupce si backend doplní sám.
