# Iddia Arbitrage Scanner

Scraper-first arbitraj tarayici. Proje artik hem klasik Python CLI modunda hem de Cloudflare Workers/Pages uyumlu serverless modda calisir.

## Mimariler

### 1) Python CLI (lokal / sunucu)
- Kaynak kod: `src/iddiaarb/*`
- Komutlar: `python -m iddiaarb.cli ...`
- Yerel PHP panel dosyalari hala repoda durur (`site/*.php`), ama Cloudflare icin kullanilmaz.

### 2) Cloudflare Runtime (onerilen)
- Worker entry: `cloudflare/worker.mjs`
- Ortak runtime kodu: `cloudflare/shared/*`
- Pages Functions: `functions/api/scan.js`, `functions/api/health.js`
- Statik panel: `site/index.html`

## Kurulum

### Python
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

### Cloudflare
```bash
npm install
npm run check:worker
```

## Lokal Calistirma

### Python testleri
```bash
pytest -q
```

### Worker local dev
```bash
npm run dev
```
Panel: `http://127.0.0.1:8787/`

## Cloudflare Deploy

### A) Worker + Static Assets (tek deploy)
```bash
npm run deploy:worker
```

Bu modda `wrangler.toml` icindeki `assets.directory = "site"` ile panel de Worker tarafindan servis edilir.

### B) Pages + Functions
```bash
npm run deploy:pages
```

- Static output: `site/`
- Functions: `functions/api/*`
- API ve UI ayni origin uzerinden calisir.

Not: UI'yi Pages'te, API'yi ayri Worker domaininde kullanmak istersen panelde `API base` alanina Worker URL'ini gir.

## API

### `GET /api/health`
Opsiyonel:
- `check_scraper=1` -> Flashscore ile kisa canlı kontrol yapar.

### `GET /api/scan`
Temel parametreler:
- `scraper_source`: `flashscore` | `soccer24` | `livesport` | `betexplorer` | `sofascore` | `multi`
- `bankroll`
- `min_margin`
- `min_data_quality`
- `min_source_quorum`
- `slippage_bps`
- `rejection_rate`
- `max_events`
- `geo_ip_code`, `geo_ip_subdivision`
- `max_bookmakers_per_event`
- `quorum_min_sources` (multi icin)
- `include_sofascore=1` (opsiyonel: multi icinde sofascore'u da ac)
- `limits_url` (opsiyonel, JSON URL)
- `refresh=1` (cache bypass)

Ornek:
```bash
curl "https://<worker-domain>/api/scan?scraper_source=betexplorer&max_events=10&refresh=1"
```

## Limits JSON Formati (Cloudflare)

`limits_url` ile verilen JSON:
```json
{
  "bookmakers": {
    "bet365": {
      "min_stake": 5,
      "max_stake": 750,
      "max_payout": 5000,
      "commission_pct": 0
    }
  }
}
```

## Onemli Notlar

- SofaScore feed'i pratikte cogu zaman tek upstream odds kaynagi verir; bu nedenle gercek multi-book arbitrage coverage dusuk olabilir.
- `multi` modunda kaynaklar event bazinda eslesmezse firsat sayisi `0` olabilir; bu beklenen bir durum olabilir.
- Bu proje teknik arastirma amaclidir. Gercek para ile uygulamada finansal ve hukuki risk vardir.
