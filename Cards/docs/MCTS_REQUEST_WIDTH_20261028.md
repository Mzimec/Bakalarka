# Cena requestů při podobné šířce nabídky — 2026-10-28

**Další body plánu:** [navazující audit](MCTS_MAINTENANCE_AUDIT_20261028.md)
odděluje experimentální úsporu přepočtů od skutečného času celého search
a uvádí, které optimalizace zůstávají otevřené.

**Navazující oprava:** [zachování indexových projekcí při restore](MCTS_INDEX_RESTORE_20261028.md)
omezilo opakované přepočty po kopii světa. Dva kořeny se stejnou prací mají
průměrně o 5,0 % a 7,6 % kratší search; kompletní jednorázová příprava má
smíšené výsledky. Následující profil zachovává měření před touto změnou.

Aktuální kód byl porovnán na čtyřech pozdních pozicích z diagnostické partie:
29–30 permanentů a 3–4 zpřístupněné kořenové akce. Samotná příprava nabídky
trvala přibližně 8–11 ms; search nad těmito nabídkami 2,9–7,2 s. Typ requestu
a počet kořenových možností tedy samy neurčují cenu vyhodnocení.

## Podrobné měření části A: kde se opravdu tráví čas

Nový režim `--single-step --execution-breakdown` zapíná exkluzivní časovače
jen mezi odevzdáním kořenové akce a vstupem do následujícího `_Replay.decide`.
Část B (menu následníka), výběr PUCT a předchozí obnovení světa jsou mimo tyto
čítače. Při vnoření se čas účtuje právě nejhlubšímu měřenému volání. Například
`settle → synchronise_registers → refresh_continuous_effects → synchronise`
neznamená čtyřnásobné započtení stejného času. GC callback účtuje pauzy
samostatně, nikoli funkci, kterou přerušily.

Finální vzorek: stejné čtyři zaznamenané kořeny, dvě opakování, seedy světů
123 a 124, každá ze 3–4 nabídnutých akcí. **60 profilovaných posunů a 60
odpovídajících kontrol bez těchto hooků.** Pořadí profilovaný/kontrolní běh se
mezi opakováními obrací. Ve všech 60 párech se shoduje historie, počet fázových
kroků, terminal/cutoff, uložené pohledy, recall a hráčská informace včetně
nabízených akcí. Každý průchod provede právě jednu volbu, bez cutoffu, a
zastaví na priority requestu bez provedení jeho akce.

### Exkluzivní průměry na jeden posun A, v ms

| Složka A | Priorita 87 | Útok 102 | Blokování 90 | Pořadí damage 92 |
| --- | ---: | ---: | ---: | ---: |
| Aktualizace indexů `IndexedRegister.synchronise` | **20,944** | **11,742** | 0,029 | **20,925** |
| Trvalé efekty `State.refresh_continuous_effects`, bez vnořených indexů | **8,060** | **4,264** | 0,034 | **6,206** |
| Transakce cen | 2,704 | 0 | 0 | 0 |
| Vazba volby, deskriptory, recall a uzavření fronty | 1,184 | 0,257 | 0,206 | 0,856 |
| Vlastní `operation.execute`, bez okolního batch zpracování | 0,102 | 0 | 0 | 0,028 |
| Vlastní deklarace combatu, bez vnořených měřených složek | 0 | 0,130 | 0,111 | 0 |
| Stavové akce (SBA), bez vnořených indexů/operací | 0,348 | 0,207 | 0,291 | 0,326 |
| Zachycení/matching a zpracování triggerů, bez vnořených indexů/efektů | 0,358 | 0,586 | 0,441 | 0,311 |
| Zachycení/publikace událostí, hráčské eventy a LKI | 0,831 | 0,247 | 0,172 | 0,425 |
| Garbage collection během A | 10,109 | 0,586 | 0 | 18,248 |
| Ostatní: plánování resolution, validace, batch, tok priority, adaptéry a nezachycený zbytek | 1,456 | 0,137 | 0,125 | 0,348 |
| **Celé A, včetně GC** | **46,096** | **18,155** | **1,408** | **47,675** |

Součty se mohou lišit o zaokrouhlení. Nulový `operation.execute` u útoku nebo
blokování neznamená, že se stav nemění: deklarace zapisují combat nativními
metodami a jejich čas je v odpovídajícím řádku. Transakce cen se u priority
provádí jen pro dvě aktivace, nikoli pro pass; tabulka průměruje všechny tři
kořenové volby.

Po odečtení přímo zachycených GC pauz činí podíl indexů + trvalých efektů na A:

- priorita: **80,6 %**;
- útok: **91,1 %**;
- pořadí damage: **92,2 %**;
- blokování: 4,5 %, ale jeho celé A trvá jen přibližně 1,4 ms.

To je podíl ve wall čase tohoto profilovaného experimentu po odečtení GC,
nikoli údaj o čistém CPU nebo univerzální rozdělení všech requestů. GC pauzy
jsou viditelné zejména v některých vysokých vzorcích priority/damage; rozsáhlejší
instrumentace sama může měnit alokace a okamžik spuštění GC.

### Co za tímto nákladem je

`State.synchronise_registers()` není jen aktualizace několika položek v mapě.
Volá přepočet trvalých efektů a synchronizaci registrů karet a hráčů. Také
`IndexedRegister.synchronise()` před samotnou aktualizací ověřuje trvalé efekty.
Při dirty položkách získává nové hodnoty indexovaných vlastností, porovnává je
s předchozími snapshoty a mění indexová členství. To zahrnuje čtení aktuálních
charakteristik, kontrolu modifierů a práci s cache jejich snapshotů.

Tyto operace se mohou spustit i nepřímo. Zachycení trigger rosteru nebo dotaz
na hráče nejprve potřebují konzistentní index. V předběžném hrubším profilu se
proto velká část útoku jevila jako čas `capture_trigger_roster`. Po doplnění
vnitřních časovačů se ukázalo, že většinu spotřebovaly jeho indexy a efekty,
nikoli samotný matching triggerů. Přisuzovat původních přibližně 14 ms přímo
trigger logice by bylo zavádějící.

Další důležitý příklad je **pass z kořene 87**. Ve finálním měření jeho A
průměrně trvá 29,849 ms, z toho indexy 20,549 ms a efekty 6,225 ms. Neplatí
žádnou cenu ani neprovádí mutační operation. Samotné předání priority tedy
nevysvětluje tento čas: engine cestou potřebuje aktuální odvozený stav.

Doplňkový cProfile omezený na A jednoho passu ukázal cestu
`is_game_over → query_players → IndexedRegister.query → synchronise / refresh`.
Zachytil 6 volání `is_game_over`, 359 volání `changed_index_values`, 357 volání
`partial_index_values`, 121 volání `_characteristic_index_values` a 1 533 volání
`Card.get_stat`. Jsou to počty volání, nikoli počty různých karet. CProfile měl
výraznou režii; jeho čas 70 ms není srovnatelný s běžnou latencí a neslouží jako
další rychlostní výsledek. Tento profil ukazuje místo a rozsah práce.

Čerstvé obnovení světa je mimo A, ale odložená údržba jeho odvozených dat se
může zaplatit až při prvním pozdějším dotazu uvnitř A. Tento experiment sám
nerozlišuje, jaká část dirty stavu pochází z obnovy a jaká ze zvolené akce.
Nelze proto čísla nazývat čistou cenou samotné změny priority či damage.

Ve finálním profilu bylo v A například 732 vstupů do `synchronise` a 4 576 do
`refresh_continuous_effects` v 12 prioritních vzorcích; u damage 448 a 4 752
v 16 vzorcích. Počty obsahují levné/reentrantní kontroly a **neznamenají tolik
úplných přepočtů grafu**. Přesto ukazují, že konzistenci kontroluje mnoho
vnořených čtení, ne pouze jeden koncový update.

### Režie profilování a další postup

| A | Kontrola bez hooků, medián | S hooky, medián |
| --- | ---: | ---: |
| Priorita | 36,557 ms | 36,066 ms |
| Útok | 21,505 ms | 22,144 ms |
| Blokování | 1,179 ms | 1,481 ms |
| Pořadí damage | 28,784 ms | 29,796 ms |

Rozdíly malého vzorku, GC a režie se nemají interpretovat jako optimalizační
gain; profilování zejména u krátkého blokování není zanedbatelné. Smyslem je
určit hlavní náklad. Průměr kontrolního A u damage byl 31,799 ms proti
profilovaným 47,675 ms, které obsahují průměrně 18,248 ms přímo zachyceného GC.
Nejde o tvrzení, že samotné pravidlové zpracování damage nově zpomalilo.

Z tohoto rozdělení plyne priorita další optimalizace:

1. Rozlišit odloženou údržbu po restore od invalidace způsobené konkrétní akcí.
2. Zmenšit dirty množiny a indexová přepočítání na prokazatelně změněné hodnoty.
3. Prověřit opakované refresh/query kontroly uvnitř jednoho pravidlového úseku
   a uchování platných odvozených hodnot. SBA a pravidlové kontroly se nesmí
   prostě vypnout ani přesunout přes hranici, na níž mají pozorovat změnu.
4. Samostatně řešit alokace a dlouhé GC pauzy; menší option space samo tento
   typ nákladu neopraví.

Nejde zatím o změnu enginu ani důkaz zrychlení celé partie. Do benchmarků byly
doplněny pouze dočasné hooky, oddělené časové kategorie a párová kontrola
výsledků. Šest cílených testů prošlo: exkluzivní účtování přes začátek/konec
měření, zachování classmethod, obnovení hooků/GC callbacků při výjimce a
shoda nativního následníka u běžné akce i ukončení hry a dosavadní diagnostické
kontroly. Všechny herní sondy běžely pod `deny_writes`;
výstupy zůstaly v RAM/stdout, bez profilových nebo dočasných souborů.

Reprodukce z `Cards`:

```powershell
$env:PYTHONHASHSEED='0'
python -B -m benchmarks.mcts_matched_requests --single-step --execution-breakdown --repeats 2
```

Výstup zachovává jednotlivé `samples[*].execution_breakdown.functions`
(exkluzivní ms a počty volání), `categories_ms`, kontrolní časy a souhrnný
`execution_breakdown` pro každý request. Kategorie se sčítají na celé A.

## Rozdělení menu, lokálního výběru a provedení jedné akce

Aktuální `--single-step` měří samostatně čtyři navazující úseky:

1. Příprava kořenového menu: nativní kandidáti, deskriptory, hráčské pohledy,
   kontext, recall a adresování rozhodnutí. Začíná vstupem do `_Replay.decide`
   v již obnoveném světě, končí vystavením kořene callbacku.
2. Jeden lokální `PUCT tree_action` s prázdnými statistikami, včetně výpočtu
   priorů a počátečního pořadí kandidátů. Není to výběr výsledné akce po celém
   search. Měří se zvlášť jako sonda; pro provedení postupně použijeme každou
   nabídnutou akci, nikoli pouze tu, kterou preferuje tato sonda.
3. Provedení akce od vrácení volby simulátoru po vstup do následujícího
   `_Replay.decide`: vazba na nativní option, pravidla, eventy a postup enginu.
   Neobsahuje přípravu nabídky uvnitř následujícího requestu. Případná práce
   nativního checkpoint adaptéru před vstupem do `_Replay.decide` v něm zůstává.
4. Příprava následujícího menu do jeho vystavení callbacku. Zde okamžitě
   zastavíme: žádnou následující akci nevybereme ani neprovedeme.

Dva seedy světů (123, 124), dvě opakování, všechny nabízené kořenové akce,
opačné pořadí pozic v druhém opakování. Následující tabulka uvádí **mediány**:

| Kořen / request | Kořenové akce | Kořenové menu | Lokální PUCT výběr | Provedení k dalšímu requestu | Následující menu |
| --- | ---: | ---: | ---: | ---: | ---: |
| 87 / Priorita | 3 | 15,68 ms | 0,43 ms | 32,96 ms | 8,88 ms |
| 102 / Útok | 4 | 11,51 ms | 0,80 ms | 21,07 ms | 16,73 ms |
| 90 / Blokování | 4 | 11,60 ms | 0,52 ms | 1,21 ms | 8,37 ms |
| 92 / Pořadí damage operací | 4 | 12,30 ms | 0,50 ms | 30,63 ms | 8,51 ms |

Průměry stejných úseků (citlivé na vysoké jednotlivé vzorky):

| Request | Kořenové menu | Lokální PUCT výběr | Provedení | Následující menu | Celý posun: provedení + následující menu |
| --- | ---: | ---: | ---: | ---: | ---: |
| Priorita | 34,24 ms | 0,48 ms | 32,17 ms | 8,83 ms | 41,00 ms |
| Útok | 12,46 ms | 0,97 ms | 28,73 ms | 17,84 ms | 46,57 ms |
| Blokování | 11,70 ms | 0,58 ms | 1,22 ms | 8,69 ms | 9,92 ms |
| Pořadí damage | 13,43 ms | 0,59 ms | 43,75 ms | 9,17 ms | 52,92 ms |

Kořenové menu se dá dále rozdělit. Nativní `candidate_generation` a `generation`
jsou oddělené od deskriptorů, `views`, `player_projection` a uložení adresy.
Mediány těchto dvou skupin:

| Request | Samotné generování kandidátů | Pohledy/deskriptory/projekce/adresa |
| --- | ---: | ---: |
| Priorita | 3,24 ms | 12,08 ms |
| Útok | 0,72 ms | 10,70 ms |
| Blokování | 1,42 ms | 10,07 ms |
| Pořadí damage | 0,08 ms | 11,83 ms |

Mediány jednotlivých složek se nemusí sečíst na medián celku. Průměry se sčítají
v rámci měřených navazujících úseků; tabulka nezahrnuje předchozí vytvoření
simulátoru, vzorkování, kopírování a přehrání kořene. Výrazně vyšší průměr
kořenového menu priority a jednotlivé celé posuny (útok až 213 ms, damage až
142 ms) ukazují variabilitu tohoto malého vzorku. Příčinu odchylek jsme v tomto
měření samostatně neprofilovali. Časovače samy mají malou režii.

### Počty posunů

| Request | Akce / svět | Vzorky: akce × 2 světy × 2 opakování | Vykonané posuny | Vykonané akce následujícího requestu |
| --- | ---: | ---: | ---: | ---: |
| Priorita | 3 | 12 | 12 | 0 |
| Útok | 4 | 16 | 16 | 0 |
| Blokování | 4 | 16 | 16 | 0 |
| Pořadí damage | 4 | 16 | 16 | 0 |
| Celkem | 15 | 60 | 60 | 0 |

Každý vzorek kontroluje přírůstek historie přesně o jednu volbu a žádný cutoff.
Zde vždy vznikl jeden další `PriorityDecisionRequest`: během měřeného průchodu
tedy vznikla dvě menu (kořen a následník), ale provedla se pouze jedna akce.
Obnovení kořene před měřením je oddělená příprava a tato tabulka nepočítá její
případné historické replay volby. Nejde o počet posunů potřebný k uzavření
celého combatu ani ke strategicky spolehlivému ohodnocení akce.

### Co přesně obsahuje čas posunu simulace

V tomto benchmarku označuje `step_ms` interval **od odevzdání zvolené akce
simulátoru do vystavení následujícího hotového rozhodnutí**. Svět už existuje
a kořenové menu už je připravené. Čas neznamená pouze přepsání hodnot karet.

```text
Před měřeným posunem:
  vzorkování / obnova světa → kořenové menu → lokální výběr akce

t0: callback odevzdává zvolenou akci
  A. Navázání akce, historie, návrat do enginu a pravidlové pokračování
t1: engine vstupuje do _Replay.decide následujícího requestu
  B. Generování další nabídky, pohledy, historie a nový SimulationNode
t2: callback dostává hotové následující rozhodnutí; vrací None

step_ms = (t1 - t0) + (t2 - t1)
          execution_to_request_ms + successor_menu.total_ms
```

Časovače jsou v `RequestStepMeasurement.choose/execute` v
`benchmarks/mcts_matched_requests.py`. Kontrola pro každý vzorek ověřuje, že
rozdělené intervaly dávají původní `step_ms`. Ukončovací unwinding po vrácení
`None` už v `step_ms` není; širší `restore_and_step_ms` jej obsahuje společně
s předchozí obnovou/přehráním a kořenovým menu i sondou výběru.

#### A. Provedení k následujícímu requestu

Interval `execution_to_request_ms` může obsahovat následující práci. Konkrétní
větev ji nemusí vykonat všechnu; například obyčejný pass neplatí cenu ability.

| Část | Co se v kódu děje |
| --- | --- |
| Navázání volby | `_Replay._select` převede přenosnou `PlayerAction` na místní `ChoiceDescriptor` a nativní option z aktuálního menu. U kandidátní fronty sestaví odpovídající lokální deskriptory. |
| Záznam volby | Přidá `RecordedChoice`, posune kurzor replay a připojí vlastní akci do recall aktéra. Jde o data v RAM. |
| Návrat z rozhodování | Uzavře aktivní generátor/frontier, vrátí `DecisionResult` a projde kontrolou typu výsledku. Herní kód pokračuje na svém původním zásobníku volání. |
| Provedení priority | `PriorityWindow` při pass aktualizuje předání priority. U zahrání/aktivace spustí `ActionProcessor`: validaci akce a cílů, cenu a okamžitá vyhodnocení nebo vložení schopnosti/kouzla na stack. Po společném pass může také vyhodnotit vršek stacku. |
| Transakce cen | Pokud akce má cenu, může vzniknout `CostTransaction` s RAM checkpointem/journalem pro případ částečného selhání. To je náklad uvnitř provedení akce, odlišný od předchozí kopie celého simulačního světa. |
| Provedení combatu | V combat větvi engine zapíše deklarované útočníky/bloky, zpracuje jejich nativní pravidla a události. Deklarace sama ještě neznamená odehrání celého combatu a damage. |
| Vyhodnocení operací | Pokud se skutečně řeší efekt, `ResolutionEngine` připraví operace a replacement efekty; `OperationExecutor` provede dávku. Například změní životy, přesune kartu, zapíše damage nebo counter. |
| Historické informace a triggery | Podle operací zachytí potřebné poslední známé informace karet, trigger roster před/po změně a vazby událostí na triggery. Tyto snapshoty nejsou plným hráčským pohledem. |
| Události a znalosti | Publikuje události, případně je odloží do dokončení transakce; vytváří hráčsky filtrované eventy, aktualizuje `PlayerKnowledge` a připojuje eventy do recall. To je práce s pozorováním uvnitř provedení, i když se úplný pohled pro další menu vytváří až v B. |
| Ustálení pravidel | Tam, kde to cesta vyžaduje, `settle` synchronizuje registry/indexy, zpracuje odložené události, stavové akce (např. smrt bytosti či prohra hráče) a triggery. Jedna akce může vyvolat více operací a kontrol. |
| Další postup enginu | Aktualizuje prioritu, případně dokončí fázi a pokračuje k další potřebné volbě. Jakmile tato volba dorazí do `_Replay.decide`, končí A. |
| Adaptéry před hranicí | Vznik requestu, vstup do kontroleru a případná práce `_CheckpointReplay.decide` před voláním jeho základní implementace jsou ještě v A. Proto A není čistým časem mutací stavu nebo jediné funkce `operation.execute`. |

V těchto 60 vzorcích následovalo vždy jedno priority rozhodnutí. Neprobíhaly
žádné další vybrané akce ani dodatečné vynucené kroky: i jednoakční následník
byl vystaven a benchmark zastavil. V jiné pozici může engine potřebovat volbu
uprostřed vyhodnocení efektu; právě tam by posun skončil a zbytek efektu by
čekal na další akci.

#### B. Příprava následujícího menu

Interval `successor_menu.total_ms` běží od vstupu do `_Replay.decide` po
vystavení hotového `SimulationNode`. Nativní stage čítače v něm rozlišují:

| Čítač | Obsah |
| --- | --- |
| `candidate_generation` | Odběr potřebného prefixu z `CandidateFrontier`: nativní návrhy/top-k, jejich pravidlová kontrola v generátoru a případný lookahead. Vytváří se pouze omezená nabídka, nikoli automaticky celé kombinatorické menu. |
| `generation` | Získání nativních options, pokud nebyly předány kandidátní frontou, nebo převzetí již vygenerovaných options; kontrola prázdné nabídky a limitu `max_options`. |
| `descriptors` | Převod lokálních options na stabilní deskriptory použitelných voleb. |
| `views` | Identifikace requestu a aktéra, vyhledání případné projekční cache, fingerprint nabídky a sestavení podporovaných hráčských pohledů. Podle konfigurace jde o pohled aktéra a potřebný pohled kořenového hráče, ne automaticky oba pohledy pokaždé. |
| `player_projection` | `DecisionProjector`: kontext rozhodnutí, přenosné `PlayerAction`, vazby na nativní options, normalizace pohledu a záznam aktuálního pozorování do recall. |
| `views_and_address_storage` | Zmrazení/sdílení strukturovaných hodnot v RAM a vytvoření `DecisionAddress` včetně identifikátorů pohledu a menu. Název storage neznamená zápis na disk. |
| `cached_projection` | Alternativní větev při shodném přehrávaném prefixu: ověření shody a použití dřívějšího pohledu/bindings místo nové kompletní projekce. Nejde o totéž co cache celého přechodu. |
| Zbytek `other_ms` | Založení kandidátní fronty a pomocných struktur, sestavení uzlu, vstup do callbacku a drobná režie měřicího kódu. Uzavření kořenové fronty po odevzdání akce patří do A; unwinding po zastavení na následníkovi je až za koncem posunu. |

Sestavení pohledu zahrnuje výběr viditelných karet a zón, aktuální vlastnosti
karet (např. p/t, keywordy, schopnosti), veřejné informace hráčů, stack/combat
a jejich filtrované vztahy. Načtení vlastností může využít efekty, indexy a
cache. Není to jen kopie seznamu ID a není to numerické value hodnocení pozice.

Přesné vnitřní stage časy jsou v jednotlivých `samples[*].root_menu.stages_ms`
a `samples[*].successor_menu.stages_ms` stdout výstupu benchmarku. Původní
rozpad v této sekci neměřil zvlášť čas jednotlivých validací, transakcí,
operací, SBA a triggerů v A. Nové exkluzivní měření je na začátku reportu.
Ze samotného původního součtu nelze tvrdit, že například triggery zabraly
konkrétních 10 ms nebo že dominantní část byla vlastní změna karet.

#### Číselný příklad a hranice závěrů

Pro průměr jednoho blokovacího přechodu v kořeni 90 platí:

```text
1,224 ms   A: navázání a provedení bloků až k dalšímu requestu
8,694 ms   B: příprava následující priority nabídky a pohledu
---------
9,918 ms   celý měřený posun
```

Pro průměr útoku v kořeni 102 je rozdělení 28,727 ms + 17,843 ms = 46,570 ms.
To neříká, kolik času uvnitř A zabraly deklarace, eventy nebo SBA samostatně.
Vyšší jednotlivé vzorky také ovlivňují průměr. Časy patří právě těmto pozicím
a nabízeným akcím; nejsou konstantním nákladem všech útoků či blokování.

Do `step_ms` tohoto experimentu nepatří vzorkování skrytých informací,
počáteční tvorba/kopie/obnova světa a replay před kořenem, původní kořenové
menu, PUCT sonda, výpočet hodnoty listu ani backpropagation statistik. Tyto
náklady mohou být součástí širších časů celého search; stejný název advance
v širším search profileru tedy není automaticky totožný interval.

Při 32 skutečně jednokrokových trajektoriích bez extensions/forced pokračování
by bylo 32 logických posunů; přechodová cache může snížit počet nových provedení.
Zde se cache přechodů nepoužívá a každý vzorek začíná v čerstvém simulátoru.
Tento experiment nesmí být zaměněn za dřívější 32trajektoriový depth-4 search.

Všechny přechody proběhly pod `deny_writes`; jednotlivé vzorky a souhrny šly
pouze do RAM/stdout. Engine a produkční presety se neměnily. Rozšířený výstup
se získá stejným příkazem `--single-step --repeats 2` uvedeným níže. Každý JSON
řádek obsahuje `samples`, `menus`, `mean_pipeline_ms`, `median_pipeline_ms`
a `counts`. Starší tabulka přípravy níže navíc používala prázdný měřicí recall
a samostatnou projekci jednoho aktéra; její interval není totožný s nynější
úplnou nativní přípravou requestu v simulátoru s původní historií.

## Oprava interpretace a měření přesně jednoho přechodu

Následující čísla jsou předchozí měření celého jednoho přechodu bez nového
rozdělení na provedení a následné menu; aktuální rozpad je výše.

Sekundové časy níže nejsou cenou samotného requestu. Původní search měl hloubku
4 a extensions 16, takže zahrnoval také následnou prioritu, combat a další
rozhodnutí. Pro izolování provedení kořenové akce je nutné skončit na úplně
nejbližším rozhodnutí. Ani samotné nastavení depth 1 nestačí, pokud zůstávají
aktivní extensions nebo průchod vynucenými volbami.

Nový režim `--single-step` žádný MCTS search nespouští. Každou ze 3–4 nabízených
kořenových akcí provede v čerstvém simulátoru, dvakrát pro každý ze dvou seedů
světa (123 a 124). Pořadí pozic se mezi opakováními obrací. Celkem bylo ověřeno
60 přechodů: každý přidal právě jednu volbu do historie, bez cutoffu. Na
následujícím requestu callback vrátí `None`; nevybírá ani neprovádí jeho akci.
V těchto vzorcích byl následujícím requestem vždy `PriorityDecisionRequest`.

Časovač začíná až v obnoveném kořeni těsně před odevzdáním akce enginu. Končí
při vystavení následného rozhodnutí. Zahrnuje pravidlové provedení a sestavení
následné nabídky/pohledu, ale nezahrnuje vzorkování světa, obnovu/přehrání prefixu,
přípravu původní kořenové nabídky, hodnocení listu ani rollout.

| Výchozí request | Akce | Vzorky | Průměr přechodu | Medián přechodu | Rozsah |
| --- | ---: | ---: | ---: | ---: | ---: |
| Priorita, kořen 87 | 3 | 12 | 39,11 ms | 40,93 ms | 33,03–44,03 ms |
| Útok, kořen 102 | 4 | 16 | 39,73 ms | 36,30 ms | 13,54–145,01 ms |
| Blokování, kořen 90 | 4 | 16 | 9,21 ms | 8,79 ms | 7,87–11,95 ms |
| Pořadí damage operací, kořen 92 | 4 | 16 | 42,12 ms | 34,62 ms | 32,45–136,32 ms |

Jde o dvě opakování na dvou světech konkrétních pozic, ne obecné pořadí rychlosti
typů requestů. Průměry útoku a damage mají vysoké jednotlivé vzorky; příčina
těchto odchylek nebyla profilována. V prioritě stojí dvě aktivace ability
průměrně 41,47 a 41,57 ms, pass 34,31 ms. Čtyři útoky mají průměry
70,28 / 36,96 / 36,30 / 15,39 ms, což ukazuje rozdílnou cenu konkrétních akcí
i uvnitř jediného requestu; první průměr navíc ovlivňuje vysoký vzorek.

Samostatně měřený celý `follow_actions` včetně obnovy/přehrání kořene měl
průměry 86,42 ms (priorita), 108,04 ms (útok), 58,64 ms (blokování) a 110,41 ms
(damage). Tyto časy již obsahují čas samotného přechodu. Nezahrnují předchozí
`factory(seed)` a `start()` a nejsou kompletní cenou inicializace světa.
Časy z obou samostatných experimentů (příprava nabídky a jeden přechod) nelze
vydávat za přímo změřený společný end-to-end interval.

Měření proběhlo pod `deny_writes`, výstup zůstal v RAM/stdout. Herní engine ani
produkční konfigurace se nezměnily. Reprodukce z `Cards`:

```powershell
$env:PYTHONHASHSEED='0'
python -B -m benchmarks.mcts_matched_requests --single-step --repeats 2
```

## Příprava nabídky bez search

20 měření po pěti zahřívacích kolech, střídavé pořadí. Existující vzorkovaný
svět už byl připraven; měřeno generování prefixu do čtyř akcí, projekce pohledu
a akcí, připojení prázdného měřicího recall a heuristický výběr. Snapshot,
vzorkování světů, provedení vybrané akce a MCTS zde zahrnuté nejsou. Jde o
zahřátou lokální přípravu, ne kompletní živé rozhodnutí agenta.

| Request | Akce v nabídce | Generování — průměr | Projekce — průměr | Včetně heuristiky — průměr |
| --- | ---: | ---: | ---: | ---: |
| PriorityDecisionRequest, kořen 87 | 3 | 3,21 ms | 7,26 ms | 10,61 ms |
| DeclareAttackersRequest, kořen 102 | 4 | 0,87 ms | 7,23 ms | 8,49 ms |
| DeclareBlockersRequest, kořen 90 | 4 | 1,65 ms | 7,71 ms | 9,53 ms |
| ReplacementOrderRequest, kořen 92 | 4 | 0,11 ms | 7,47 ms | 7,73 ms |
| AbilityResolutionRequest, syntetický výběr 1 ze 4 karet | 4 | 0,11 ms | 7,29 ms | 7,52 ms |

Poslední řádek je pouze příprava nativního výběrového requestu nad čtyřmi
vlastními veřejnými bytostmi z boardu kořene 87. Neměří skutečné provedení
sacrifice efektu ani MCTS při jeho resolution. Pomalý libovolný efekt nelze
charakterizovat cenou tohoto jednoduchého výběru.

Nativní top-k se v combatu používá. Časy generování neobsahují sestavení všech
podmnožin útoku; `CandidateFrontier` odebere jen malý prefix s lookahead.
Příprava priority zahrnuje konstrukci legálních akcí a jejich případných plateb.
Přesné rozdělení jejích 3,21 ms na jednotlivé podfunkce tato ablace neměří.

## Stejný rozpočet search, nejvýše čtyři kandidáti

PUCT s heuristikou, 32 trajektorií, dva světy, depth 4, extensions 16,
32 768 advances, žádný časový limit. Postupný combat je vypnutý. Pro toto
diagnostické srovnání je widening omezen na nejvýše čtyři zpřístupněné akce
**i ve vnitřních progresivních uzlech**. Nedokončené nabídky zůstávají označené
jako neúplné; jde o změněné měřicí nastavení, nikoli produkční preset.
Generátor může připravit i malý zásobník/lookahead nad zpřístupněnou šířkou.

Každá pozice měřena dvakrát, pořadí 87, 90, 92, 102 a obráceně. Strom je
studený, původní vlastní recall zachovaný, stejné seedy mezi opakováními.
Přehrání původní partie kontroluje celé information keys. Podobně velký board
není totožný stav: liší se fáze, zdraví, stack a rozpracovaný combat.

| Rozhodnutí | Kořen / permanenty / akce | Průměr search | Rozsah dvou měření | Nové posuny simulace |
| --- | --- | ---: | ---: | ---: |
| Priorita: pass nebo first strike jednoho ze dvou Inquisitorů | 87 / 29 / 3 | **2,92 s** | 2,54–3,30 s | 61 |
| Útok: výběr skupiny útočníků | 102 / 30 / 4 | **7,16 s** | 7,08–7,23 s | 210 |
| Blokování dvou Ogre a Dragona | 90 / 29 / 4 | **4,22 s** | 4,09–4,34 s | 72 |
| Pořadí tří damage operací 3, 3 a 4 | 92 / 29 / 4 | **3,46 s** | 3,28–3,63 s | 55 |

Všech osm searchů dokončilo všech 32 trajektorií bez fallbacku. Celé odpovědi,
hodnoty, návštěvy a logická práce se mezi opakováními každé pozice shodují.
Tyto časy zahrnují přípravu vzorkovaných světů a průchod simulací uvnitř
search, ale **nezahrnují předchozí živý snapshot**, kterým se root připravuje
pro MCTS agenta. Nejde o celou partii ani srovnání herní síly.

## Proč se časy liší

V tomto útoku je 306 logických kroků, z nichž 210 vyžaduje skutečný simulator
advance. Priorita má 173 logických kroků, ale pouze 61 nových advances. Ostatní
přechody obslouží cache. Počet možností v kořeni tento rozdíl nevystihuje.

Průměrné navzájem vyloučené hooked časy celého search:

| Request | Kopie světů/checkpointů | Sestavení pohledů | Kandidátní fronty |
| --- | ---: | ---: | ---: |
| Priorita | 0,998 s | 0,611 s | 0,119 s |
| Útok | 1,340 s | 2,109 s | 0,422 s |
| Blokování | 1,403 s | 0,767 s | 0,265 s |
| Pořadí damage | 0,980 s | 0,809 s | 0,099 s |

Nejde o úplné rozdělení času: neobalené funkce tvoří zbytek. Tabulku nelze
sčítat s nadřazenými metrikami jako `advance_ms`. Levná příprava čtyř možností
pořadí damage tedy neznamená levné vyhodnocení jejich budoucích důsledků.

Resolve bez hráčské volby, například běžné líznutí karty, sám žádné menu
options nemá. Jeho provedení patří do simulovaného přechodu. Samostatný čas
provedení takového efektu tímto benchmarkem změřený není; řádek pořadí damage
měří rozhodnutí během zpracování efektů.

## Reprodukce

[Podrobný výstup](../runs/mcts-matched-requests-20261028.jsonl) obsahuje metriky,
odpovědi a vzorky přípravy. [Benchmark](../benchmarks/mcts_matched_requests.py)
zapisuje pouze stdout. Z `Cards`:

```powershell
$env:PYTHONHASHSEED='0'
python -B -m benchmarks.mcts_matched_requests --repeats 2 --iterations 32
python -B -m benchmarks.mcts_matched_requests --preparation
```

Osm měřených searchů a lokální příprava proběhly pod `deny_writes`; až jejich
výsledky se explicitně zapsaly do výstupního JSONL logu. Engine se neměnil.
