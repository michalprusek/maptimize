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
| Retence | 14 dní |
| Doba běhu | ~80 s, **bez výpadku aplikace** |
| Cena místa | ~1,2 GB/den (dump) + 4,7 GB jednou (soubory se hardlinkují) |

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

`LAST_RESULT` se přepisuje na **každé** cestě ven ze skriptu, takže starý
timestamp sám o sobě znamená, že běh umřel tvrdě (OOM, vytržený disk) — ne že se
nic nestalo.

## Obnova databáze

⚠️ **Nejdřív si vždycky zkus obnovu do throwaway kontejneru**, ne rovnou do
produkce. Ověřeno 2026-08-01 — tenhle postup projde:

```bash
DUMP=$(ls -1t /backup/maptimize/db/*.dump | head -1)

docker run -d --name restore-test \
  -e POSTGRES_USER=maptimize -e POSTGRES_PASSWORD=x -e POSTGRES_DB=maptimize \
  -v /backup/maptimize/db:/b:ro pgvector/pgvector:pg16

docker exec restore-test pg_restore -U maptimize -d maptimize --no-owner "/b/$(basename $DUMP)"
docker exec restore-test psql -U maptimize -d maptimize -c \
  "SELECT count(*) FROM cell_crops;"          # porovnej s produkcí
docker rm -f restore-test
```

Do **produkce** (jen když je DB opravdu ztracená — je to destruktivní):

```bash
docker compose -f docker-compose.prod.yml stop maptimize-backend maptimize-mcp
docker cp "$DUMP" maptimize-db:/tmp/restore.dump
docker exec maptimize-db psql -U maptimize -d postgres -c \
  "DROP DATABASE maptimize;" -c "CREATE DATABASE maptimize;"
docker exec maptimize-db psql -U maptimize -d maptimize -c "CREATE EXTENSION vector;"
docker exec maptimize-db pg_restore -U maptimize -d maptimize --no-owner /tmp/restore.dump
docker compose -f docker-compose.prod.yml start maptimize-backend maptimize-mcp
```

## Obnova souborů

```bash
SNAP=/backup/maptimize/files/latest        # nebo files/20260801_074425
rsync -a "$SNAP/data/"    /home/cvat/maptimize/data/
rsync -a "$SNAP/weights/" /home/cvat/maptimize/weights/
cp "$SNAP/env" /home/cvat/maptimize/.env

# backend bezi jako uid 1000; po obnove zkontroluj vlastnictvi
docker run --rm -v /home/cvat/maptimize/data:/dst alpine chown -R 1000:1000 /dst
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
