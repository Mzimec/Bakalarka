# Omezení opakované práce v MCTS — 2026-10-27

Navazuje na [rozbor náročné hry](MCTS_GAME_DIAGNOSTICS_20261027.md).
Cílem je zlevnit stejný search při zachování pravidel, informací hráče a
rozpočtu. Nepřidává aproximaci světa ani další prořezávání legálních akcí.

## Změny v pěti oblastech

1. **Pozorování hráčů.** Při widening uvnitř stejného nativního callbacku se
   použije už zmrazený pohled. Dosud se při každém rozšíření znovu procházela
   deska obou relevantních hráčů. Přepočítají se akce, vazby, šířka a příslušné
   klíče. Popisy pravidel lze sdílet také mezi pozorovateli jedné synchronní
   projekce; všechny kladné i záporné kontroly viditelnosti se znovu ověřují.
   Sdílený pracovní kontext nesmí přežít změnu světa.
   Projekce vynucených hranic se nově ukládají i pro následné přehrání prefixu
   stejného vzorkovaného světa. Před použitím se ověřuje hlavička, konkrétní
   legální descriptor a úplný předchozí recall. Cache obsahuje pouze neměnné
   fakty. Úsporný záznam jen s pohledem jednajícího hráče se spotřebuje uvnitř
   replaye; nepoužívá se jako náhrada pohledu kořenového hráče pro hodnocení.
2. **Charakteristiky a indexy.** Cache nativních nezměněných charakteristik
   zahrnuje i power/toughness. Po změně statů nebo při modifikátorech,
   counterech či vlastních čtecích metodách zůstává běžná cesta. Strukturální
   ověření neměnné tabulky statů se opakuje až pro novou tabulku; cache drží
   nejvýše 256 tabulek jednoho vlastněného stavu a při kopii světa se resetuje.
   Proměnlivá metadata schopností se stále kontrolují živě. Obecné přepočty
   continuous effects a indexů nebyly odstraněny ani nahrazeny odhadem.
3. **Otisky.** Krátké typované skalární části strukturálních otisků používají
   cache s nejvýše 2 048 položkami. Klíčem je typ i přesná reprezentace;
   `True`, `1`, `1.0`, `"1"` i znaménko nuly zůstávají rozlišené. Výsledné
   digesty jsou totožné s původním algoritmem. Dlouhé řetězce se neukládají.
   Obecné zmrazování proměnlivých struktur zůstává potřebné.
4. **Kandidáti.** Opakovaný požadavek na stejný prefix vrací již vytvořenou
   neměnnou tuple a nečte další položky generátoru. Ukládá se nejvýše osm
   prefixů jednoho frontieru. Na vyhodnocovaném klidném listu se nabídka už
   nerozšiřuje: její širší verze by se při tomto průchodu nepoužila. Postupné
   generování, pořadí kandidátů a úplný legální konec nabídky zůstávají.
5. **Prodloužení za hloubku.** Jiné requesty než priority nemohou být klidným
   listem, takže pro tento test nepotřebují další projekci kořenového hráče.
   U nativních uzlů lze neklidnou priority odmítnout podle již existujícího
   pohledu jednajícího hráče: obsazenost stacku a průběh combatu jsou veřejné.
   Přijatý list se nadále hodnotí z vlastního pohledu kořenového hráče.
   Hloubka, povolená prodloužení, forced kroky i ukončení při výhře se nemění.

Všechny nové cache zůstávají v RAM. Produkční běh nezapisuje nové soubory.
Kopie světa, provádění pravidel, sestavení první nabídky a první projekce
zůstávají skutečnou prací; tato etapa je neodstraňuje.

Zamítnut byl širší experiment s dlouhodobou cache popisů jednoduchých
nativních schopností. Ověřování všech proměnlivých vstupů stálo na syntetické
desce více než původní popis: dvě opakování 40 společných projekcí měla bez
této cache 0,128/0,123 s, s ní 0,171/0,168 s. Experiment nebyl ponechán ve
zdrojích. Méně opakovaných alokací samo o sobě nezaručuje rychlejší search.

## Metoda měření

`benchmarks.mcts_recorded_roots` přehraje zaznamenané živé volby ze stávajícího
JSONL logu. Před každým zachyceným search ověří celý původní information key,
včetně recall. Pořadí popisů schopností obnoví z logu, protože pořadí autorských
množin se může mezi procesy lišit. Jde pouze o benchmarkový adaptér katalogu.
Do MCTS nepředává skutečnou skrytou ruku ani pořadí knihovny.

Časované searchy startují s oddělenými statistikami plánovače; předchozí živé
searchy se nevypočítávají znovu. To zkracuje přípravu měření, není to nová
optimalizace produkčního agenta. Výsledky proto neznamenají čas celé partie.
Pro kontrolu se porovnává kompletní odpověď: volba, hodnota, všechny návštěvy,
průměry a priory akcí, coverage, fallback a důvod ukončení. Počítadla skutečné
realizační práce mohou po odstranění zbytečných projekcí/replayů klesnout.

Z adresáře `Cards`:

```powershell
$env:PYTHONHASHSEED='0'
python -B -m benchmarks.mcts_recorded_roots --roots 1 23 87 104
```

Výstup jde na stdout; zachycené světy, referenční zdroje ani profily se
automaticky neukládají na disk.

Kontrolní běh přes noc byl časově neplatný: u kořene 87 přerušení stroje
prodloužilo wall čas na 35 522,875 s. Jeho odpověď a počítadla souhlasila,
ale žádná část této přerušené série neslouží jako důkaz zrychlení. Referenční
zdroje pro novou dvojici měření byly zachované v RAM před první úpravou;
čerstvé procesy je načítají přes pipe, bez pomocných souborů.

## Výsledky finálního porovnání

Nová souvislá dvojice A → B proběhla 2026-10-28. Každý search měl 128 iterací,
čtyři světy, hloubku 8, nejvýše 16 prodloužení a žádný časový limit.
Jde o jeden vzorek na variantu a pozici, nikoli stabilní mediány nebo celý zápas.

| Kořen | Původní search | Po změně | Pokles času | Počet pohledů před → po |
| --- | ---: | ---: | ---: | ---: |
| 1: časná main priority | 4,774 s | 3,310 s | 30,7 % | 158 → 158 |
| 23: priority před combatem | 56,275 s | 51,261 s | 8,9 % | 1 378 → 1 022 |
| 87: pozdní priority | 108,716 s | 96,063 s | 11,6 % | 2 218 → 1 699 |
| 104: priority s aktivací na stacku | 112,921 s | 120,024 s | **−6,3 %: zpomalení** | 3 048 → 2 316 |

U všech čtyř kořenů souhlasí celý výsledek také s původním herním logem:
akce, hodnota, návštěvy, průměry, priory, coverage i fallback/stop reason.
Souhlasí 128 dokončených trajektorií, počty logických přechodů, skutečných
posunů, obnovení světů a rozšíření kandidátů. Například kořen 104 má nadále
2 165 logických přechodů a 1 298 skutečných posunů; žádné simulace nebyly
odebrány kvůli lepšímu času.

V pomalých pozicích odpadlo 23,4–25,8 % sestavení pohledu. Čas **celého** search
je smíšený. U kořene 104 klesla vlastní cena pozorování z 36,202 na 34,180 s,
ale candidate frontier zdražil z 9,576 na 12,765 s a kopie z 13,952 na
15,514 s. Proto se ani menší počet projekcí nedá označit za univerzální
zrychlení. Kolísají i části bez přímé úpravy, včetně kopií; z rozdílu jednoho
páru nelze určit příčinu celé změny latency.

První čtyři průzkumné série měřily meziverzi bez cache forced prefixů a
ukázaly smíšené časy. Jsou ponechané spolu s neplatnou přerušenou sérií a
finálním párem v [JSONL výsledcích](../runs/mcts-work-reuse-20261028.jsonl).
V tomto prostředí byly časy před přerušením výrazně nižší i u reference;
jednotlivé série přes tyto rozdílné podmínky nesrovnáváme.

Tato etapa tedy dokládá méně opakované práce při stejných výsledcích na
ověřených pozicích. **Zrychlení celých partií ani odstranění všech úzkých míst
neprokazuje.**

## Ověření finálních zdrojů

Kompletní sada: **3 039 passed, 725,87 s**, `python -B -m pytest -p no:cacheprovider
Cards/tests -q`. Nové regresní testy mají auditní zákaz filesystem zápisů.
Ověřují soukromé reference v obou pořadích pozorovatelů, čerstvost po mutaci,
countery a rollback statů, vlastní čtecí metody, typované digesty, spotřebu
generátoru, widening stejného callbacku, horizont a forced prefix cache.

Finální zdroje `game`/`helper` po měření i testech mají stejný normalizovaný
SHA-256 `830eb38479084da68f597b915bb50a0b0a472c8ebcda1061409b7e3d021828ab`;
formát výpočtu a počet souborů jsou v JSONL. Nová celá partie se v této etapě
neměřila. Výsledek testů a shoda čtyř kořenů nejsou důkazem zachování herní
síly při libovolném časovém limitu.

## Co lze převzít z MageZero

Ověřeno přímo ve zdrojích projektu WillWroble/mage, ne odvozeno z předpokládané
rychlosti Javy:

| Princip v MageZero | Vztah k naší implementaci |
| --- | --- |
| `MCTSPlayer.priority` přehraje skript dříve, než vytvoří nový vektor stavu; jednoakční pass může pokračovat bez zastavení. | Opakovaný ověřený prefix nemá znovu stavět pohled. Proto přibyla cache i pro projekce forced hranic. Úplný hráčský recall zachováváme. |
| Útok a blokování vybírá `MCTSPlayer` postupně. | Inspirace pro samostatné nativní combat continuations. Samotný beam nad deklaracemi není totéž; tato etapa ho nepřevádí na mikrorequesty. |

Zdroj: [MCTSPlayer.java](https://github.com/WillWroble/mage/blob/master/Mage.Server.Plugins/Mage.Player.AIMCTS/src/mage/player/ai/MCTSPlayer.java).

`MCTSNode.validateState` obnoví kopii stabilního stavu a přehraje dílčí volby.
Mikrouzel může držet stav rodiče; strom sdílí jeden objekt `Game`. U nás už
podobný základ tvoří nativní priority checkpointy a souvislé trajektorie.
Přenos tohoto principu neznamená sdílení proměnlivého světa mezi workery.
[MCTSNode.java](https://github.com/WillWroble/mage/blob/master/Mage.Server.Plugins/Mage.Player.AIMCTS/src/mage/player/ai/MCTSNode.java)

`GameState` kopíruje vyjmenované části přes jejich vlastní `copy()`; restore
jednorázového snapshotu některé komponenty přímo převezme. To je konkrétní
vzor pro budoucí explicitní kopii našeho dynamického stavu. Neprokazuje cenu
kopie v milisekundách a není to podklad pro slepé shallow-copy našeho grafu.
[GameState.java](https://github.com/WillWroble/mage/blob/master/Mage/src/main/java/mage/game/GameState.java)

Náš závěr: nejbližší přínos má přesouvat práci z opakovaného replaye na první
ověřenou hranici. Přestavba kopírovacího kontraktu a postupné combat requesty
jsou další samostatné změny, které zde neoznačujeme jako hotové.
