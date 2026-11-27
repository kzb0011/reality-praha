# reality-praha – hlídač inzerátů od soukromníků (Bazoš → Telegram)

## Pro koho a jak komunikovat
- Majitel: Bob (Robert Harman), realitní makléř (Dominius reality). **Netechnický uživatel.**
- Odpovídej česky, stručně a prakticky. Změny dělej sám přímo v repu (commit do `main`), ne návodem „stáhni a nahraj".
- Po každé změně ověř, že běh v GitHub Actions doběhl ✅ (log kroku „Run python scraper.py").

## Co to dělá
Každých 15 minut stáhne nové inzeráty (byty, domy, pozemky bez polí a lesů, chaty, garáže; v Praze a Středočeském kraji vše od 5 mil.) z **Bazoš.cz**,
vyřadí realitky a poptávky a **soukromníky pošle do Telegramu**. Cílem je, aby Bob oslovil majitele dřív než ostatní RK.

## Jak to běží
- **GitHub Actions** (`.github/workflows/scrape.yml`): `python scraper.py`, pak uloží `docs/` zpět do repa.
  - `timeout-minutes: 10`, `concurrency: scrape`.
  - Ukládání má smyčku: při odmítnutém push udělá `fetch` + `reset --hard origin/main` + `python scraper.py --merge /tmp/ours.json`
    (sloučí `data.json`, aby se nic neztratilo ani neposlalo dvakrát) a zkusí to znovu (max. 5×).
  - V yml je i `schedule: cron "7,37 * * * *"`, ale GitHub plánované běhy u free účtu vynechává.
- **Hlavní spouštěč je cron-job.org** (účet Boba), každých 15 min:
  `POST https://api.github.com/repos/kzb0011/reality-praha/actions/workflows/scrape.yml/dispatches`
  (účet se 3. 11. 2026 přejmenoval z Bob00712 na **kzb0011** – stará adresa v cron-job.org přestala spouštět), body `{"ref":"main"}`,
  hlavička `Authorization: Bearer <fine-grained PAT>` (jen tohle repo, Actions: read & write).
  **Token vyprší 5. 10. 2027** – pak vygenerovat nový a vyměnit v cron-job.org.
- **Secrets** v repu: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` (může obsahovat víc ID oddělených čárkou → pošle se všem).
  Tokeny nikdy nepiš do souborů ani logů.
- Neběží-li to: Actions → poslední běh → log. „Re-run" starých běhů nepoužívat (berou starou verzi yml) – spouštět přes *Run workflow*.

## Soubory
- `scraper.py` – vše (stahování, filtr RK, duplicity, Telegram, merge). Nastavení je nahoře v souboru.
- `docs/data.json` – stav: `{"updated", "listings": {key: {...}}}`, klíč např. `bazos:225446795`. Drží se 30 dní (`KEEP_DAYS`).
- `docs/probe/` – ladicí výstupy (vzorky stránek/odpovědí). Lze promazat.
- `docs/index.html` – webový přehled nad `data.json` (GitHub Pages **není zapnuté**, Bob ho nepoužívá).
- `requirements.txt` – requests, beautifulsoup4, lxml.

## Zdroje
- **Bazoš (jediný aktivní)**: `reality.bazos.cz/{prodam|pronajmu}/{rubrika}/{offset}/` s `hlokalita=11000&humkreis=70`
  (40 inzerátů/str., offset po 40; nahoře jsou TOPované – prochází se min. `MIN_PAGES`=3 stránky a končí po 2 stránkách bez nového). Jedno velké hledání na rubriku (`BAZOS_SEARCHES`), pak filtr podle PSČ (`near`):
  - rubriky: byt, dum (prodej i pronájem), pozemek, chata, garaz (jen prodej – pronájem garáží Bob nechce).
  - vyřazuje se: sruby, karavany, mobilheimy, mobilní/modulové domy, tiny house (`UNWANTED_RE`), nemovitosti v zahraničí (`ABROAD_RE`),
    pronájmy mimo Prahu pod 11 000 Kč (`PRONAJEM_MIN_MIMO_PRAHU`, Bob 16. 11., bez ceny se nechávají).
  - pozemky: jen od 1 mil. Kč (`POZEMEK_MIN`, bez uvedené ceny se nechávají); **pole, louky, lesy, zemědělská půda se vyřazují** (`FIELD_RE`), pokud nejde o stavební / k bydlení (`BUILD_RE`) – Bob je nechce.
  - posílá se Praha (PSČ `1xx xx`) + `NEAR_PSC` = první 3 číslice PSČ (poštovní obvod ≈ obec + ~10 km): Brandýs, Úvaly,
    Čelákovice, Odolena Voda, Klecany, Zdiby (250), Říčany… (251), Praha-západ obce (252), Hostivice (253), Benešov (256/257),
    Dobříš (263), Beroun (266/267), Kladno, Unhošť (272/273), Slaný (274), Mělník (276), Neratovice (277), Kralupy (278),
    Kolín (280/281), Nymburk, Lysá n. L. (288/289).
  - **drahé**: `/prodam/` přes všechny rubriky, `cenaod=5000000`, okruh 90 km, Praha + Středočeský kraj (PSČ 25x–29x) – `expensive`.
    Typ se u nich odhaduje z titulku (`guess_typ`), jinak „jiná nemovitost".
  - Změna schématu hledání (`SCHEME` v `data.json`) → první běh jen uloží (`first_load`), nic nepošle.
  - Detail inzerátu (`bazos_detail`) dává jméno prodávajícího, celý popis, `seller_id` (idphone).
  - Inzeráty bez načteného jména čekají (`pending`, max. 3 běhy), pak se tiše zahodí – nikdy se neposílají bez posouzení.
  - Poptávky (titulek začíná Hledám/Koupím/Poptávám/Poptávka/Sháním) se přeskakují – Bob je nechce.
- **UlovDomov**: implementováno, ale **vypnuto** (`UD_ENABLED = False`) – Bob: skoro samé realitky.
  API `ud.api.ulovdomov.cz/v1/offer/find` (POST, bounds Prahy), kontakt `www.ulovdomov.cz/fe-api/offer/contact-info/{id}` (`is_private_landlord`).
- **Zamítnuté zdroje (neřešit znovu)**: Sbazar a Bezrealitky (robots.txt zakazuje), Sreality (podmínky), Realingo (zakazuje detaily/API),
  iDNES Reality (100 % realitky), Hyperinzerce (téměř nulový objem), Bazar.cz (nemá nemovitosti), Facebook (zákaz scrapingu – neobcházet).
  - Průzkum 11/2026 (`tools/probe_sources.py`, workflow `probe-sources`, ruční spuštění): **byty.cz** (podmínky výslovně zakazují
    scraping), **Avizo, Annonce, ceskereality.cz, Digireality, RealCity** (ochrana proti botům – neobcházet), **Eurobydlení**
    (robots.txt zakazuje ClaudeBot), **Expats.cz** (soukromých jen pár, podmínky zakazují kopírování), **reality.cz, RealityMix**
    (téměř jen RK), iReality/realitni-inzerce (mrtvé domény), Sousedé.cz (není inzerce).

## Filtr realitek (`classify` → `rk` / `soukromy` / `nejiste`)
Posílá se jen `soukromy` a `nejiste` (`SELLER_FILTER = "rk_ven"`). Vyřadí se jako RK, pokud:
- jméno je na seznamu `RK_SELLERS` (Bobův seznam – M&M, Paschke, HVB, Finpos, Next Reality, ZOOM, Bohemian Estates, Ideální nájemce,
  Dumrealit, Ladislav Burian/REALBrána, Pavelková, Jiří Beneš, Petr Schejbal, Vladimír Kremel, Jaroslav Čermák, Femida, Martin Petlach, Adam Horák, Marek Bijok, Karel Budín, Martin Kršňák, Luboš Rezek, Jana Barčuková, Tomáš Tvoroch (RK Evropa), Bohumír Kocián, Lukáš Kuchař, Oleksandr Dyrma, Martin Lubas, Karel Michel…; text „v nabídce mám 4 byty“, výrobci dřevostaveb = RK; jméno obsahující samostatné „RK“/„Reality“/„infolinka“ = RK; krátká jména jako „TM" v `RK_SELLERS_EXACT` (shoda celého jména); navíc `RK_SELLER_IDS` = Bazoš idphone nahlášených RK, chytí je i pod jiným jménem; když Bob nahlásí další, přidej sem),
- jméno obsahuje firmu (`Jméno - Firma`, ale „Jméno - Praha“ apod. je místo – `PLACE_RE`, čárku, s.r.o./spol., `…Real/Reality/Estate/Invest`, `REAL…`),
- text obsahuje `NO_RK_WORDS` („RK nevolat", „R.K nevolat"… – **vyřadit**; Bob 27. 11. vrátil, 16.–27. 11. se zkoušelo posílat s ⚠️),
- text obsahuje podpis „RK Jméno Příjmení“ (např. „RK Karel Budín“),
- text obsahuje RK výrazy (`RK_WORDS` – **všechny vyřazují**, `RK_TEXT_SOFT` je prázdný; Bob 27. 11.: „jakmile to inzeruje realitka, nechci“;
  14.–27. 11. se zkoušelo měkká slova posílat jako ❔); provize, makléř, zprostředkuje, výhradní zastoupení, ID nabídky, ev. č., …webrealit.cz…),
- stejný prodávající má ≥ 3 **různé** inzeráty (`RK_SURE_LISTINGS` = 3, Bob 27. 11.; 16.–27. 11. bylo 6 a 3–5 jen ❔;
  znovu vložený stejný inzerát se počítá jednou),
  nebo byl dříve vyřazen (paměť `RK_KNOWN`, `RK_PHONES` – **jen ze `STRONG_RK_REASONS`**: seznam, ID, jméno firmy; ne z „dříve vyřazen“
  ani ze slova v textu, aby se chyba neřetězila).
- Znovu vložený stejný inzerát (stejný prodávající + titulek) se neposílá podruhé (`find_repost`).
`PRIVATE_WORDS` (bez provize/provizí, bez zprostředkování, pouze přímému zájemci, nejsem realitka…) ruší RK slova v textu.
Slabé znaky (`RK_WEAK`: „nabízíme k pronájmu", samotné „RK", „exkluzivní") inzerát nevyřadí, jen označí ❔.
Priorita Boba: **raději občas propustit realitku, než přijít o soukromníka.**

## Duplicity
`find_duplicate`: stejný typ+akce, dispozice, plocha ±1 m², cena ±2 %, stejná čtvrť/obec (nebo stejný telefon) z jiného zdroje → neposílat.

## Telegram zpráva
HTML: titulek, cena, dispozice/m²/Kč/m²/lokalita/zdroj, 👤 jméno, 📞 telefon (pokud je),
odkazy „Otevřít inzerát · 📲 WhatsApp" (odkaz na Google Bob nechce) (wa.me s předvyplněnou zprávou `WA_TEXT`,
varianty pronájem/prodej, byt/dům). Max. 15 samostatných zpráv na běh, zbytek souhrnem.
Je-li v textu inzerátu e-mail, zpráva ukáže ✉️ adresu + text e-mailu v `<code>` (klepnutím se zkopíruje; Telegram `mailto:` odkazy nebere).
Automatické odesílání WhatsApp zpráv ani e-mailů **nedělat** (ban čísla, nevyžádaná obchodní sdělení) – jen předvyplněný odkaz, Bob odesílá sám.

## Týdenní přehled „Majitelé, kterým se nedaří“ (`motivated_digest`)
Každé pondělí od 6:00 UTC (první běh po té hodině; `motivated_week` v `data.json` hlídá, aby šel jen 1× týdně) jedna zpráva:
top 7 prodejů + 5 pronájmů. **Jen jistí soukromníci** (`seller_type == "soukromy"` i podle dnešních pravidel, známé jméno,
< 3 různé inzeráty – žádné realitky ani ❔). Signály: snížená cena, ≥ 14 dní v inzerci, opakované obnovení.

## Měsíční „📊 Tržní radar“ (`market_update` + `market_report`)
- `docs/market.json` – denní souhrn (drží se natrvalo, ~2 kB/den): klíč `akce|typ|dispozice|oblast` → [Kč/m² nových inzerátů, počet, soukromníci].
  Dopočítává se z `data.json` (30 dní) každý běh pro chybějící celé dny → ztracený zápis nevadí. Vynechává `first_load` a duplicity.
- 1. den v měsíci od 7:00 UTC jedna zpráva: medián nabídkové ceny Kč/m² (prodej i pronájem bytů) – Praha celkem, dispozice, čtvrti/okresy
  + změna proti předchozímu měsíci; domy jen počty (plocha u domů nespolehlivá). `market_month` v `data.json` = už odesláno.
- „Jak rychle se prodá“ **neměříme** – hlídač nevidí starší stránky Bazoše, zmizení inzerátu ≠ prodej. Bod 1 (kupci z poptávek) Bob nechce.
- Žádné AI/tokeny – čistý Python v GitHub Actions.

## Rozhodnuto (neřešit znovu)
- **Telefon z Bazoše se nezískává** – Bazoš číslo nevydává (ad-phone.php vrací 200 bez čísla). Bob: nezkoušet.
  `bazos_phone` jen vezme `tel:` odkaz, pokud je přímo ve stránce, a `idphone` jako `seller_id` – žádné další požadavky.
- Podpis ve `WA_TEXT` je „Robert Harman" – správně, neměnit. Jiné příjmení nikde nepoužívat.

## Otevřené body
1. `daily_rk_digest` (denní souhrn vyřazených) existuje, ale je vypnutý – Bob nechtěl víc zpráv.
2. `docs/probe/` a staré probe funkce lze uklidit.
