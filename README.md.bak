# Bakalářka

## Téma

Optimalizace distribuce cen karet v balíku pro zjednodušený model karetní hry Magic: the Gathering. A analýza užitečnosti karet v daném stavu hry.

## Model

### Minimalistický
- Velikost balíku: *N*
- Počet kol do konce: *T*
- Počet karet na zažátku hry: *S*
- Počet líznutých karet v kole: *D*
- Užitková (globální) funkce, která mapuje cenu karty na očekávaný užitek: *u(c)*
- Cena karty (pro každou kartu): *c*
- Každé kolo se mužeme využít právě *t* zdrojů k zahrání karet, kde *t* odpovídá aktuálnímu kolu.
- Balík je náhodná permutace *N* karet

#### Možná rozšíření
- *T*,*D* jsou náhodné veličny
- *u(c)* je závislá i na *t*, současném kole, tedy je to funkce: *u(c,t)*
- Každá karta má specifické *u(c,t)* - umožňuje rozlišovat karty na ruzné druhy (engine, finisher)...
- Dva balíky soutěží proti sobě
- Zdroje nedostaváme jistě, musíme hrát karty které generují zdroj za kolo.

### S definovanými kartami
Je třeba definovat jednotlivé karty. Snažíme se najít *u(c,state)*

Dva hráči hrají se svými balíky proti sobě. Každý má *HP* životů. Cílem každého hráče je dostat protivníkův *HP* na nulu.

Mějme tři typy karet:
1. Unit - Setrvává na hracím poli. Každá má svůj *power* a *toughness* a popřípadě nějaké specifické schopnosti. 
Souboj uvažujme stejný jako v MtG. V každém kole můžeme zaútočit libovolným množstvím jednotek na hracím poli a vyčerpat je. 
Naopak bránící hráč může branit naše jednotky libovolným množstvím svých nevyčerpaných jednotek. Jednotky se opět dobijí na začátku našeho tahu.
Uvažujme, že všechny jednotky mají schopnost *trample* z MtG.
2. Spell - Karta, která má okamžitý jednorázový efekt.
3. Land - Setrvává na hracím poli. Každý vyložený Land generuje v každém našem tahu jednu manu. Můžeme yahrát maximálně jeden tah ve svém kole.

#### Návrhy karet
Jednotky s různými hodnotami *power* a *toughness*.

Nějaké jednotky, které generují vetší hodnotu, čím déle jsou na hracím poli (engine).
1. Na začátku tahu získá +1/+1
2. Pokaždé když zahraješ *Spell* dá dvě poškození protivníkovi.
3. Lízni si kartu, pokaždé když zahraješ kartu s cenou X a vyš....
4. Po smrti se vrátí do ruky.

Jednotky, se schopnostmi, které mohou razantně posílit naši hrací plochu (finisher):
1. Přidej všem svým jednotkám +X/+X

Spelly:
1. Lízni si X karet.
2. Znič libovolnou *Unit*.
3. Znič všechny jednotky.
4. Přidej si X many.
5. Podívej se na nepřítelovu ruku a zruš z ní jednu kartu...
6. Jednotky v tomto kole nemohou blokovat....


## Optimalizace užitku v průběhu hry
Snažíme se najít balík karet, který dosáhne pro dané parametry modelu nejvyššího očekávaného užitku ze zahraných karet v průběhu celé hry.
Uvažujme zatím v minimalistickém modelu.

### Greedy algoritmus
V každém kole maximalizujeme problém batohu pro karty v naší ruce.

Neoptimální mějme *u(c)=c^1.5, T=2, t=5*, v ruce máme karty s následujícími cenami: 1,2,3,4 (každou jen jednou). 
V příštím tahu si lízneme kartu s cenou 5. V tomto případě by greedy metoda vybrala v prvním kole karty s cenami 4, 1 a v druhém 5.
Zatímco optimální výběr by byl 2,3 a 1, 5.

### Belmann algoritmus
Problém Belmannova algoritmu je, velikost potenciálního stavového prostoru a tedy celková časová složitost algoritmu. 
Nicméně pro náš minimalistický model, kde platí mezi kartami se stejnou cenou ekvivalence, by mohlo dojít k dostatečné redukci stavů a 
pro nižší *T* by mohl být Belmann použitelný. 

### Monte-Carlo
Provádíme *n* simulací hry do hloubky *h* (nebo do konce hry).
V každé simulaci:

1. Ze současného stavu vybereme akci (náhodně nebo podle jednoduché heuristiky).
2. Simulujeme náhodné líznutí a další tahy až do hloubky *h*.
3. Spočítáme celkový užitek dosažený v simulaci. 

### Monte-Carlo Tree Search
MCTS kombinuje Monte-Carlo simulace s postupným budováním rozhodovacího stromu.

Každá iterace má 4 fáze:

1. Selection – postupujeme stromem podle kritéria (např. UCB), které vyvažuje exploataci a exploraci.
2. Expansion – přidáme nový uzel (nový stav).
3. Simulation (rollout) – náhodně nebo heuristicky dohraje hru.
4. Backpropagation – aktualizujeme hodnoty uzlů podle výsledku simulace.

## Určování užitku karet ve specifikovaném modelu

### Lokální
Hledání zahraní optimální karty v daném stavu *s*, můžeme provést přes hledaní pravděpodobnosti pro výhru ze stavu *s* a ze stavu *t*, 
který nastane po zahrání karty. Čím blíže k nule bude honota funkce *V(t) - V(s)*, tím optimálnější je karta. 
*V(s)* určuje pravděpodobnost výhry ze stavu *s*.

### Globální
Pokud chceme najít kvalitativní ohodnocení karet pro danou kombinaci dvou soupeřících balíků. 
Můžeme kvalitu karty definovat jako rozdíl mezi winrate balíku s kartou (*D1*) proti winratu balíku bez ní (*D2*).
Což by vedlo dle mého názoru na rovnici typu:
$
U(c) =  \sum_s (\pi_{D1}(s) - \pi_{D2}(s))V(s)
$

 

