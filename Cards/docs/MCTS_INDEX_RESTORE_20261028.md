# Zachování platných indexových projekcí při obnově světa — 2026-10-28

**Další etapa:** [audit zbývajících směrů](MCTS_MAINTENANCE_AUDIT_20261028.md)
zkouší registrační projekce a jemnější invalidaci. Tyto další experimenty
neprokázaly stabilní zrychlení celého search a nejsou zapnuté v runtime.
Níže uvedené výsledky patří předchozí opravě přenosu projekcí při restore.

Obnova vzorkovaného světa už nemusí zahazovat všechny prokazatelně platné
indexové projekce karet. Na dvou zaznamenaných kořenech zkrátila tato změna
search se stejnou prací v průměru o 5,0 % a 7,6 %. Jednokrokové A zlevnilo
výrazněji, ale kompletní inicializace + jeden krok má smíšené výsledky.
Nejde o měření zrychlení celé partie nebo herní síly.

## Příčina a změna

`copy_world()` dosud nahrazovalo `CardSnapshotCache` prázdnou instancí a
zakládalo novou klasifikaci layer candidates. Následující kontrola efektů
potom nedokázala doložit invariantnost nemodifikovaných karet a při prvním
přechodu přepočítala i jejich indexové charakteristiky. Náklad se projevil
uvnitř A, i když souvisel se zahozenou informací při předchozím restore.

Nová `CardSnapshotCache.restore_index_projections()` přenáší jen stále platné
nativní **indexové** projekce. Před přenosem ověřuje registraci, aktuální stat
tabulku, definici, zónu, revizi inkarnace, face-down příznak, prázdné modifikační
zdroje/countery a živý podpis relevantních ability metadat. Instanční přepisy
charakteristických getterů se nepřenášejí.

Záznam kopíruje stejný ownership copier a memo jako zbytek světa. Card,
controller, definice, stat tabulka i případné runtime hodnoty snapshotu tedy
odkazují do kopie. Klíče závislé na Python `id()` se sestaví z nových objektů.
Zdrojová cache se nemění. LKI záznamy, modifikované/dynamické projekce a jiné
odvozené cache zůstávají na dosavadní cestě. Dirty množiny, legální akce,
pořadí eventů, SBA a pravidlové kontroly se nevypínají.

Vlastnictví zůstává oddělené i po následné změně hodnot, inkarnace nebo
counterů v jedné kopii. Změna není abstrakcí hráčské informace, sdílením
skrytého skutečného stavu ani přenosem MCTS návštěv mezi různými světy.

Výchozí `copy_world(..., retain_index_projections=True)` používá přenos.
`False` ponechává dosavadní zahazování těchto projekcí jako ablační referenci.
Nemění se produkční rozpočty ani top-k/PUCT nastavení.

## Oddělení přípravy od samotné akce

Čtyři stejné kořeny jako v [rozboru requestů](MCTS_REQUEST_WIDTH_20261028.md):
87 priorita, 90 blokování, 92 pořadí damage a 102 útok. Dva seedy světů,
dvě opakování a všechny 3–4 kořenové akce: **60 párů / 120 přechodů**.
Každý přechod vykoná právě jednu akci a zastaví na následujícím requestu.
Pořadí referenční/nové varianty a pořadí kořenů se obrací.

Kontrola shody zahrnuje historii, fáze, terminal/cutoff, pohledy, recall a
hráčskou informaci s nabízenými akcemi. Všech 60 párů se shoduje.

Počítadlo sleduje volání `_characteristic_index_values`, nikoli unikátní karty:

| Kořen | Průměr čtení při první inicializaci, před / po | Průměr čtení v A, před / po | A před, průměr | A po, průměr |
| --- | ---: | ---: | ---: | ---: |
| Priorita 87 | 241 / 241 | **121 / 2** | 31,36 ms | **8,29 ms** |
| Útok 102 | 240 / 240 | **90 / 0** | 15,37 ms | **3,13 ms** |
| Blokování 90 | 241 / 241 | 0 / 0 | 1,12 ms | 1,14 ms |
| Pořadí damage 92 | 241 / 241 | **121 / 2** | 25,47 ms | **4,45 ms** |

V těchto vzorcích jsou počty uvedeného čtení v obnově/přípravě kořenového menu
a v menu následníka nulové u obou variant. Tedy nejde pouze o přesunutí
stejných getter volání z A do menu. Přenos projekcí ale přidává vlastní práci
při kopírování a prvotní rekonstrukci neurychluje.

To je vidět na celém diagnostickém intervalu `factory(seed) + start() +
restore + menu + lokální výběr + jeden posun`. Obsahuje i to, co je mimo A:

| Kořen | Celý interval před, průměr | Po, průměr | Změna |
| --- | ---: | ---: | ---: |
| Priorita | 312,48 ms | 286,15 ms | −8,4 % |
| Útok | 284,27 ms | 291,83 ms | +2,7 % |
| Blokování | 274,23 ms | 291,98 ms | +6,5 % |
| Pořadí damage | 307,43 ms | 281,28 ms | −8,5 % |

Krátké jednorázové obnovení tedy nemá univerzální zisk. Rozptyl, GC a přidané
alokace při přenosu cache ovlivňují tento malý vzorek; příspěvek každé z těchto
příčin zde není samostatně změřený. Přínos samotného A nelze vydávat za stejný
přínos celého MCTS.

## Celý search se stejnou prací

PUCT s heuristikou, 32 iterací, dva světy, depth 4, extensions 16,
max. 32 768 logických advances, žádný časový limit. Diagnostická šířka je
omezena na čtyři kandidáty i ve vnitřních progressive uzlech. Každá varianta
byla měřena dvakrát v pořadí AB/BA; celkem osm searchů.

| Kořen | Před: dva vzorky | Po: dva vzorky | Průměr před → po | Pokles |
| --- | --- | --- | --- | ---: |
| Priorita 87 | 2,399 / 2,819 s | 2,403 / 2,556 s | 2,609 → 2,480 s | **5,0 %** |
| Útok 102 | 6,374 / 6,320 s | 5,791 / 5,942 s | 6,347 → 5,867 s | **7,6 %** |

V prioritě je první nový vzorek mírně pomalejší; dvě opakování nejsou garance
stabilního 5% zisku. U útoku jsou obě nová měření rychlejší. Neměříme zde
128iteraciový produkční preset ani celou partii.

Obě varianty mají shodné úplné odpovědi (volba, hodnoty, odhady a návštěvy)
a kontrolované work counters:

| Kořen | Dokončené iterace | Logické posuny | Nové posuny | Cache hits | Obnovy světa |
| --- | ---: | ---: | ---: | ---: | ---: |
| Priorita | 32 | 173 | 61 | 112 | 13 |
| Útok | 32 | 306 | 210 | 96 | 27 |

Zisk tedy nevzniká zmenšením rozpočtu, menu nebo počtu simulovaných kroků.

## Ověření a reprodukce

Regresní testy kontrolují přesměrování controller referencí do nové kopie,
nezávislost následných mutací, odmítnutí změněné inkarnace/ability metadat,
countery, aktivní modifikátory, instanční přepisy getterů a referenční cold
variantu. Platnost původního snapshot/replay a checkpoint chování ověřují také
dosavadní testy. **Kompletní sada: 3 064 passed, 302,64 s.**

Dvě živé MCTS kontroly použily produkční native konfiguraci s explicitními
diagnostickými overrides: 16 iterací, dva světy, depth 2 a žádný časový limit.
Zadaný `max_turns=8` ukončil oba běhy jako `turn_limit` (exportovaný počítač
byl 9), nikoli výhrou nebo prohrou. Oba hráči měli deklarované decklisty,
začínal soupeř. Nevznikla engine chyba ani herní fallback:

| Balíčky / soupeř / seed | Wall kontrolního běhu | Použitelné search / pokusy | Dokončené trajektorie | Setup policy |
| --- | ---: | ---: | ---: | ---: |
| white/red vs Modular, 42 | 13,144 s | 13 / 13 | 208 | 3 |
| blue/black vs Simple, 43 | 12,707 s | 8 / 8 | 128 | 1 |

Jde o funkční kontroly, ne párové měření rychlosti ani celé partie. Všechny
jejich runtime operace byly pod `deny_writes`. Součet je 21/21 použitelných
searchů a 336 dokončených trajektorií.

Fingerprint konečných zdrojů: SHA-256
`877f410b6b0dfbc13243019b16c3574dd770e1123e5d624566a34b0444b48457`,
302 souborů v `game/**/*.py`, `helper/**/*.py`, `benchmarks/**/*.py`.
Zdrojové změny jsou lokální pracovní kopie; nejsou novým commitem/pushem.

Benchmarky zapisují pouze stdout. Použitá měření navíc běžela pod
`runtime_write_guard.deny_writes`; jednotlivé výsledky zůstaly v RAM/stdout.
Nevytváří se profilové, dočasné ani jiné pracovní soubory. Z `Cards`:

```powershell
$env:PYTHONHASHSEED='0'
python -B -m benchmarks.mcts_restore_maintenance
python -B -m benchmarks.mcts_restore_maintenance --search
```

Další práce je na ceně první materializace a na dalších stále dynamických
projekcích. Tento krok opravuje konkrétní opakování práce po restore;
nezavádí univerzální undo log ani jinou sémantiku pravidel.
