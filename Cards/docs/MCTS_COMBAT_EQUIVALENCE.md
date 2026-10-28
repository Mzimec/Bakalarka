# Ekvivalence deklarací boje — 2026-10-27

První vrstva použitelná při generování MCTS kandidátů vytváří reprezentanty
aktuálních bojových rolí přímo přes počty a rozdělení. Nepotřebuje nejprve
vyrobit konkrétní podmnožiny a pak je deduplikovat. Je volitelná a výchozím
nastavením vypnutá.

Nejde zatím o obecnou ekvivalenci celých herních stavů. Paměť hráčů, historické
reference, budoucí pravidla a belief mohou konkrétní karty dále rozlišovat.
Proto se fyzické akce trvale nemažou, N/W se neslučují a úplný konkrétní výčet
zůstává pozdější částí proudu. `menu_complete` se stane pravdivým až po jeho
vyčerpání, nikoli po vyčerpání reprezentantů.

## Proč dřívější příklady trvaly dlouho

Starší časy 2,28 s pro útok a 11,54 s pro blokování patřily variantě s úplnými
menu 1 024 a 1 296 možností. Nativní kandidátní varianta ve stejných řízených
příkladech naměřila přibližně 0,61 a 1,14 s při 16 trajektoriích, dvou světech
a hloubce 4. Nejde o zrychlení totožných trajektorií ani o celé hry.

Nativní kandidáti už před tímto krokem nevyžadovali úplné kombinace před první
volbou. Zbývající cenu tvoří skutečné přechody a obnovení světa, další nabídky,
deskriptory, pozorování a řešení souběžných operací. Top-k také neznamená
nejvýše k vytvořených možností: používá lookahead a v kořeni obvykle zásobník
16 návrhů. Samotné menší menu tudíž není záruka kratšího celého search.

## Objekty a rozsah kontraktu

| Objekt | Odpovědnost |
| --- | --- |
| `CardRoleSignature` | Aktuální role s definicí, vlastnictvím/kontrolou, P/T, typy, barvami, keywords, tapnutím, poškozením, countery, revizí, dobou kontroly a vstupu, token/X údaji. |
| `CombatRoles` | Jednorázové seskupení veřejných účastníků; nečte ruce ani knihovny. |
| `CombatActionClass` | Klíč počtů/balíků, kombinatorická četnost a konkrétní nativní binding. |
| `CombatDeclarationEquivalence` | Lokální model deklarací, přípustnosti dvojic a líných celočíselných reprezentantů. |
| `EquivalentCombatStrategy` | Levné návrhy, reprezentanti a následně zbylé konkrétní deklarace. |

Scope `public-combat-declarations-v1` pokrývá pouze tuto deklarativní vrstvu,
ne libovolné callbacky ani budoucí hodnotu celé větve. První podporované
definice jsou pasivní bytosti s nativním castem a konkrétní standardní
Impassioned Orator template. Kontrola není založená jen na názvu karty.
Neznámé triggery, aktivované schopnosti, face-down, attachmenty, nepodporované
modifikátory nebo efekty ponechávají danou kartu v samostatné skupině.
Jiný combat backend, více hráčů či neprázdný stack ponechávají původní cestu.

Pro blokování se do tříd navíc zahrne přípustnost bloků a příslušný defender.
Útočníci stejné podporované role se porovnávají přes reprezentanta; neznámé
role zůstávají singletony. Nativní validační pipeline stále ověřuje každou
skutečně nabízenou kompletní deklaraci, včetně menace a coupled požadavků.

## Generování přes počty

Pro skupinu n útočníků a jednoho soupeře se vytvoří volby 0 až n útočníků.
Při více defenderech se generují rozdělení n mezi jednotlivé defendery a
neútočení. Konkrétní ID se při bindingu zvolí stabilně z dané skupiny.

Pro blokování se ke každému útočníkovi přiřadí vektor počtů blokerů jednotlivých
rolí. V rámci skupiny útočníků jsou tyto vektory seřazené, takže záměna
stejných útočníků nevytváří další reprezentant. Rozdíl mezi `[1,1,1,1,0]`
a `[4,0,0,0,0]` přitom zůstává zachovaný. Nepoužité blokery určuje zbytek.

U plně shodných deseti útočníků je 11 reprezentantů místo 1 024 deklarací.
Původní fixture má dvě skupiny `[9,1]`: první Orator vstoupil v jiné fázi.
Tento rozdíl konzervativně ponecháváme, takže má 20 reprezentantů.
Původní blokovací fixture má skupiny `[5]` a `[4]`, tedy 12 reprezentantů
pro 1 296 konkrétních deklarací.

Četnost třídy je kombinatorická metadata, ne počet dokončených simulací a
zatím se nepoužívá jako prior. Například útok pěti z deseti zastupuje 252
konkrétních podmnožin. Jedna simulace přesto přidá pouze jednu návštěvu.
Četnosti tříd neověřených nativním validátorem zahrnují i nelegální návrhy;
pravidlová omezení se nepřeskakují.

Po reprezentantech pokračuje konkrétní výčet s odstraněním již nabídnutých
přesných deklarací. Při dostatečně velkém widening lze proto opět dosáhnout
původního úplného menu. Tento krok nevydává lokální podobnost za důkaz
zaměnitelnosti ve všech přípustných světech.

## Kořenový zásobník a měření

U nové varianty je zásobník omezený počtem dostupných celočíselných
reprezentantů, maximálně 16. Předpříprava na této hranici neprovádí lookahead
do konkrétních permutací. Původní generátor zachovává dosavadní zásobník.

Benchmark `benchmarks.mcts_combat_equivalence` rozlišuje prefix čtyř možností,
samotnou přípravu kořene a celý referenční search. Navíc měří kontrolu
`native_no_prefetch`: původní proud s vypnutým předgenerováním. To odděluje
vliv zásobníku od vlivu seskupení. Jde o pevné známé světy referenčního
replay simulátoru; není to měření celé partie ani obecné herní síly.

Finální měření přípravy: šest střídajících se vzorků po zahřátí, mediány.
Prefix obsahuje generování a projekci čtyř možností; kořen také staví nový
svět a připravuje případný zásobník.

| Příprava | Nativní | Nativní bez zásobníku | Reprezentanti |
| --- | ---: | ---: | ---: |
| Prefix útoku | 8,54 ms | stejný generátor | 8,84 ms |
| Kořen útoku | 23,24 ms | 17,58 ms | 22,51 ms |
| Prefix blokování | 8,50 ms | stejný generátor | 10,02 ms |
| Kořen blokování | 25,02 ms | 17,43 ms | 21,39 ms |

Celý search používá dva pevné světy, 16 trajektorií, hloubku 4, max. extensions
8 a advances 4 096 bez časového limitu. Čtyři opakování na variantu, pořadí
se obrací v každém druhém opakování:

| Search | Nativní | Bez zásobníku | Reprezentanti |
| --- | ---: | ---: | ---: |
| Útok | 0,599 s | 0,651 s | 0,579 s |
| Blokování | 0,924 s | 0,979 s | 0,985 s |

Všech 24 searchů dokončilo všech 16 trajektorií bez fallbacku. Hodnota byla
+1 pro útok a -1 pro blokování ve všech variantách. Kontrola bez zásobníku
zachovala konkrétní doporučení, hodnoty a návštěvy akcí i logickou práci;
vyžadovala ale více rozšíření kandidátního menu. Úplné vypnutí předpřípravy
tedy není obecné zrychlení, přestože zlevní vytvoření samotného kořene.

Útok měl ve všech variantách 256 logických přechodů a 32 skutečných volání
simulátoru. Blokování mělo v obou původních variantách 89 přechodů/volání,
reprezentanti vedli ke 104: změnilo se nabízené menu i průběh search.
Mírně lepší čas útoku nepovažujeme za průkazné obecné zrychlení; blokování
naopak v tomto testu zpomalilo. Výchozí presety proto experiment nezapínají.

## Ověření

Finální zdroje prošly **671 cílenými testy za 81,48 s**, včetně 31 testů
nové ekvivalence. Kontroly porovnávají množiny a četnosti tříd s úplným
nativním výčtem a také výsledky skutečného damage/SBA řešení po záměně karet.
Zahrnují různé runtime role, skryté ruce/tváře, neznámé triggery, více
defenderů, monotónní widening, úplné konkrétní pokračování, hranici zásobníku,
fork/replay, UCT/PUCT/hierarchický PUCT, konfigurace, paralelní search,
self-play a přesné policy cache. Celá projektová sada se znovu nespouštěla.
Benchmarky konečných zdrojů běžely se zákazem runtime zápisů.

## Konfigurace a reprodukce

Do existující platné konfigurace:

```json
{
  "progressive_candidates": true,
  "candidate_native_ordering": true,
  "candidate_combat_equivalence": true,
  "widening_constant": 4
}
```

Varianta má samostatnou identitu kandidátního modelu. `candidate_attack_risk`
lze zapnout nezávisle; měření ekvivalence jej ponechává vypnutý.

```powershell
$env:PYTHONPATH='Cards'
.venv/Scripts/python.exe -B -m benchmarks.mcts_combat_equivalence --repeats 6
.venv/Scripts/python.exe -B -m benchmarks.mcts_combat_equivalence --search --repeats 4
```

Generátory, bindingy a měření žijí v RAM. Benchmark sám neukládá soubory.
