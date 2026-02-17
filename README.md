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
Je třeba definovat jednotlivé karty. Snažíme se najít *u(c,t)*

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
