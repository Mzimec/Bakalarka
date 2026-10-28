# Další body optimalizačního plánu — 2026-10-28

Navazuje na [přenos platných indexových projekcí](MCTS_INDEX_RESTORE_20261028.md).
Další tři postupy jsou implementované jako **izolované benchmarkové experimenty**.
Nejsou zapnuté v běžném enginu: menší počet přepočtů v této etapě neprokázal
stabilní zrychlení celého search. Předchozí ověřená oprava restore zůstává aktivní.

## Co bylo prověřeno z původního plánu

| Směr | Stav a závěr |
| --- | --- |
| Inkrementální posun a checkpointy | Současný backend už používá souvislé trajektorie, připravené světy a nativní priority checkpointy. Není správné celý běh popisovat jako replay od začátku partie. Ve srovnaných searchích jsou stále potřebné samostatné obnovy vlastněných světů; univerzální continuation pro všechny nested operace tento krok nezavádí. |
| Sdílení neměnných dat | `immutable_world_values` a copy recipes už sdílejí prokázané neměnné hodnoty. Celou `CardDefinition`/`AbilityDefinition` nelze plošně prohlásit za neměnnou: obsahují také měnitelné definice, modifikátory a případné runtime reference. Další sdílení vyžaduje explicitní kontrakt těchto dat. |
| Cache charakteristik | Přenos ověřených indexových projekcí při restore je hotový v předchozí etapě. Nově byl vyzkoušen i přenos právě vypočtené registrační projekce; výrazně snižuje počet výpočtů při inicializaci, ale časový přínos celé práce není přesvědčivý. |
| Fast path priorit a fází | Komprese forced rozhodnutí a odlehčená forced projekce už existují. Bypass `is_game_over` není bezpečná náhrada: dotaz nejprve synchronizuje efekty a může dokončit čekající práci. Nový regresní test kontroluje i vytvoření dirty práce uvnitř refresh při původně čistém player registru. Další přeskočení priority oken nebylo zapnuto. |
| Invalidace podle změn | Runtime používá dirty množiny, částečné indexové aktualizace a opaque membership tokens. Nově se vyzkoušelo zúžení player aktualizací a zachování nezměněných indexových řádků. V tomto vzorku nemají spolehlivý časový přínos. Globální invalidace efektů se při změně života nezrušila — život může ovlivnit jiné karty. |
| Reuse MCTS mezi rozhodnutími | Současná paměť odděluje strukturu od statistik a kontroluje informační stav, horizont a svět. V této etapě nebylo povoleno slučování návštěv odlišných světů ani ztrátově podobných pozic. Nové měření hodnotí cenu stejného search, nikoli novou strategii reuse nebo herní sílu. |

## Tři provedené experimenty

1. **Registrační projekce.** Před zápisem karty do registru ji běžná cache
   správně odmítá jako ještě neregistrovanou. Experiment po registraci převezme
   charakteristiky z právě vypočteného indexového řádku. Kontroluje native typy,
   neměnnost stat tabulky, prázdné modifikační zdroje, živá ability metadata
   a nepřepsané gettery. Opakovaná registrace dirty karty starý řádek nepovýší.
2. **Player write sets.** Health mění health/alive; loss mění loss/alive;
   failed draw a poison mají vlastní indexy. Neznámý hráč/registr nebo plná
   notifikace dál používá úplnou projekci. Před čtením zůstává refresh efektů.
3. **Nezměněné indexové řádky.** Pokud diff neobsahuje žádnou změnu členství,
   ponechá se původní immutable řádek místo nového slovníku/unionu. Skutečné
   změny, dirty fronta a membership tokens zachovávají původní chování.

Všechny varianty jsou v `benchmarks.mcts_incremental_maintenance`, včetně
kontextu, který po dokončení i výjimce obnoví původní metody a property.
Globální benchmarkové monkeypatches nejsou určeny pro souběžné ani živé hry.
Běžné spouštění nic z těchto experimentů neaktivuje.

## Metodika

Čtyři kořeny z uložené diagnostické partie: 87 priorita, 90 blokování,
92 pořadí damage a 102 útok. Jednokrokové srovnání používá všechny 3–4 nabídnuté
akce, světy se seedy 123/124 a dvě opakování: **60 párů / 120 přechodů**.
Kontroluje celou detached historii, fáze, terminal/cutoff, pohledy, recall
a hráčskou informaci včetně menu. Zastaví na další žádosti, nevykoná její akci.

Celý search má 32 iterací, dva světy, depth 4, extensions 16, 32 768 advances,
šířku nejvýše čtyři a žádný časový limit. Čtyři opakování na variantu,
pořadí AB/BA/AB/BA. Porovnává se také úplná odpověď a hlavní work counters.
Jde o diagnostický rozpočet, nikoli produkční 128iteraciový preset nebo celé hry.

Samostatná ablace rozděluje varianty na registraci, player/řádky a jejich
kombinaci. Před každou dvojicí searchů provede GC mimo časování, aby omezila
vliv odpadu předchozího vzorku. **GC během search zůstává zapnutý a zahrnutý
do wall času.** Jeho callback sleduje počet kolekcí a celkovou dobu pauz.
Výsledky této série se nesmějí směšovat s řadou bez předchozího explicitního GC.

## Výsledky experimentů

Všech 60 jednokrokových párů má shodné detached výsledky. Počty níže jsou
volání `_characteristic_index_values`, nikoli počty unikátních karet:

| Kořen | Inicializace: před → experiment | A: před → experiment | Celý interval před | Celý interval experiment |
| --- | ---: | ---: | ---: | ---: |
| 87 priorita | 241 → 122 | 2 → 2 | 281,02 ms | 244,45 ms |
| 90 blokování | 241 → 122 | 0 → 0 | 259,24 ms | 246,36 ms |
| 92 damage order | 241 → 122 | 2 → 2 | 255,64 ms | 254,33 ms |
| 102 útok | 240 → 120 | 0 → 0 | 256,11 ms | 259,34 ms |

Celý interval zahrnuje `factory(seed) + start() + restore + kořenové menu +
lokální výběr + jednu akci + menu následníka`, tedy více než A. Počty getterů
v restore/kořenovém menu a v menu následníka jsou nulové v obou variantách.
Úspora prvotních přepočtů tedy není pouhým přesunutím stejných getterů do menu.
Jednorázový interval přesto nezrychlil univerzálně.

**Celý search**, čtyři vzorky na variantu, bez předchozího explicitního GC:

| Kořen | Reference: vzorky v s | Experiment: vzorky v s | Průměr reference → experiment |
| --- | --- | --- | --- |
| 87 priorita | 2,085 / 2,260 / 2,169 / 2,446 | 2,162 / 2,212 / 2,257 / 2,576 | **2,240 → 2,302 s (+2,8 %)** |
| 102 útok | 5,412 / 5,404 / 5,461 / 5,630 | 5,355 / 5,503 / 6,029 / 7,305 | **5,477 → 6,048 s (+10,4 %)** |

V obou kořenech se shodují celé odpovědi, doporučení, hodnoty a návštěvy,
32 dokončených trajektorií a dva světy. Kořen 87 má 173 logických posunů,
61 nových posunů a 112 cache hits; kořen 102 má 306 / 210 / 96.
Všech 16 searchů dokončilo zadaný rozpočet bez fallbacku. Poslední attack
vzorek výrazně kolísá; nezahazuji ho ani bez dalších důkazů nepřipisuji
konkrétní externí příčině. Tato série **neprokazuje další zrychlení**.

Samostatná ablace s předchozím GC, dva vzorky na variantu:

| Kořen / průměr v s | Reference | Jen registrace | Jen player/řádky | Vše |
| --- | ---: | ---: | ---: | ---: |
| 87 priorita | 1,964 | 1,961 | 2,098 | 2,192 |
| 102 útok | 5,526 | 5,610 | 5,623 | 6,000 |

Také zde se všechny úplné odpovědi a kontrolované work counters shodují.
GC během dvojice searchů zabral 1,324–1,693 s při 1 588–1 606 kolekcích.
Jde o celkový čas za oba kořeny, nikoli za jedno rozhodnutí. Méně přepočtů
charakteristik tedy automaticky neodstranilo alokační a ostatní náklady.

Rozptyl obou řad a malé vzorky neumožňují přisoudit celé zpomalení jednomu
mechanismu. Z hlediska nasazení ale stačí závěr, že přínos není doložený:
**nové varianty zůstávají jen v benchmarku**, nejsou nové výchozí chování MCTS.

## Co zůstává otevřené

Přenést více odvozených dat po restore může dávat smysl, ale prostá kopie
podpisu grafu efektů nestačí: podpisy obsahují i Python identity efektů a karet.
Převod by musel zachovat kontrolu cílových inkarnací, vrstev, pořadí, expirace
a mutable metadat a ověřit přínos včetně ceny nových kontrol.

Další větší práce je omezit alokace a opakované konstrukce **hráčských
projekcí a kopírovaného runtime grafu**. Profil celého search po předchozí
opravě stále ukazuje významný podíl obou. Nejde jen o cenu vlastního výběru
PUCT. Globální vypnutí nebo přenastavování GC uvnitř search nebylo přidáno;
zasáhlo by i jiné agenty a vlákna stejného procesu a změnilo paměťové nároky.

Univerzální undo log, ploché runtime jádro karet a přepis všech continuations
zůstávají otevřené architektonické kroky. Tato kontrola je neoznačuje za hotové.

## Ověření a stav zdrojů

Cílené testy experimentů a jejich izolace: **15 passed**. Kontrolují
platnost registrační projekce, odmítnutí custom readeru, opakovanou
registraci dirty karty, player indexy, slučování notifikací, rollback,
life-dependent cíle efektů a zachování refresh bariéry v `is_game_over`.

Kompletní sada zachytila i problém izolace benchmarku: starší profiler
ponechal na třídě explicitní alias zděděné metody. Hook pouze na základní
třídě pak neaktivoval experiment pro tuto konkrétní cestu. Benchmark nyní
hookuje také nativní `CardRegister` a `PlayerRegister` a má na alias regresní
test. Po měření se změnilo toto pokrytí hooků, nikoli samotné experimentální
transformace. Původní metody a property se obnoví i po výjimce.

**Finální kompletní sada: 3 079 passed, 266,88 s (4:26).** Spuštěna s
`PYTHONHASHSEED=0`, `PYTHONDONTWRITEBYTECODE=1`, Python `-B` a pytest
`-p no:cacheprovider`, bez JUnit/profilových výstupních souborů.

Fingerprint finálních lokálních zdrojů: 303 Python souborů z `game`, `helper`
a `benchmarks`, SHA-256
`4133fac60f3bb93a93a317262eeeba7c51a77cc212e8616de0f34da847030ff9`.
Výpočet: SHA-256 UTF-8 řetězce lexikograficky seřazených řádků
`relativní/cesta.py:sha256(obsah)`, spojených `\n`, bez koncového newline.
Nejde o nový commit nebo ověřený vzdálený push.

## Reprodukce bez pracovních souborů

Z `Cards`:

```powershell
$env:PYTHONHASHSEED='0'
python -B -m benchmarks.mcts_incremental_maintenance
python -B -m benchmarks.mcts_incremental_maintenance --search --repeats 4
python -B -m benchmarks.mcts_incremental_maintenance --variants
```

Výstup je pouze stdout; žádné nové runs adresáře, checkpoint soubory ani
profilové soubory se nezakládají. Naměřené série byly navíc spuštěny pod
`runtime_write_guard.deny_writes`, mezivýsledky zůstaly v RAM.
