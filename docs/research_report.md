# Iddia Arbitraj Arastirmasi (Gov Kaynak Haric)

Bu rapor, kullanici talebine gore devlet kurumlari kaynaklari dislanarak hazirlandi.

## 1) Arbitraj nedir?

- Spor bahis arbitraji, farkli platformlardaki oran farklarini birlestirip tum sonuc olasiliklarini kapsayarak pozitif marj yakalama stratejisidir.
- Temel kosul:
  - `sum(1 / odd_i) < 1` ise teorik surebet vardir.

Referanslar:
- https://help.smarkets.com/hc/en-gb/articles/115001175531-How-to-calculate-arbitrage-betting
- https://sumsub.com/blog/arbitrage-gambling/

## 2) Arbitraj ne degildir?

- Pratikte "tam risksiz para makinesi" degildir.
- Neden:
  - Oranlar saniyeler icinde degisebilir.
  - Bir tarafta bahis kabul edilirken diger tarafta red/oran guncellemesi olabilir.
  - Platform limitleri, max kazanc sinirlari ve hesap kisitlamalari getirilebilir.
  - KYC/AML ve davranis analizi tarafinda pattern bazli inceleme olabilir.

Referanslar:
- https://sumsub.com/blog/arbitrage-gambling/
- https://www.bilyoner.com/yardim/sikca-sorulan-sorular
- https://www.nesine.com/yardim

## 3) Nasil yapilir? (Operasyonel Akis)

1. Ayni event/market icin birden fazla kaynaktan oranlari topla.
2. Sonuc bazinda en yuksek oranlari sec.
3. Implied probability toplamini hesapla.
4. `sum(1/odd_i) < 1` ise stake dagilimi hesapla.
5. Min marj, min bahis tutari, max kazanc, gecikme, kabul/red riskini filtrele.

Referanslar:
- https://help.smarkets.com/hc/en-gb/articles/115001175531-How-to-calculate-arbitrage-betting
- https://sumsub.com/blog/arbitrage-gambling/

## 4) Iddia Tarafi Icın Kritik Gercekler

- Platform bazli kural ve limitler farkli.
- Yardim/SSS dokumanlari surekli guncellenebilir.
- Oran, kabul, iptal, limit ve max kazanc kosullari dinamik oldugundan tarayici sistemlerde risk motoru gerekir.

Referanslar:
- https://www.bilyoner.com/yardim/sikca-sorulan-sorular
- https://www.nesine.com/yardim

## 5) Kodlama Arastirmasi (GitHub)

Incelenen acik kaynak projelerde ortak mimari:

- Veri katmani:
  - Scraper ile event + market + outcome + odds toplanir.
- Normalizasyon:
  - takim/market/outcome adlari standardize edilir.
- Arbitraj cekirdegi:
  - en iyi odds secimi
  - implied probability hesaplama
  - stake dagilimi (bankroll bazli)
- Cikti:
  - konsol/JSON, bazen HTML viewer veya webhook.

Incelenen repolar:
- https://github.com/carterlasalle/SportsArbFinder
- https://github.com/robbiehaynes/arbitrage-finder
- https://github.com/personal-coding/Live-Sports-Arbitrage-Bet-Finder
