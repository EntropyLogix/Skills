# Handoff — przenoszenie kontekstu między sesjami Claude Code

Globalny skill `/handoff` wczytuje do nowej sesji zamrożony, zwięzły skrót
wcześniejszej sesji, zamiast wznawiać jej pełny kontekst. Zapis w starej sesji
(`/handoff -save …`) buduje paczkę od razu; lista zapisanych handoffów
(`/handoff`) i zapis są obsługiwane przez hook, więc nie wywołują modelu. Model
uruchamia się dopiero przy wczytaniu wybranej pozycji.

## Jak to działa

Claude Code na bieżąco zapisuje każdą sesję do
`~/.claude/projects/<katalog>/<id-sesji>.jsonl`. Ten transkrypt jest już
automatycznym zrzutem sesji, więc nic nie trzeba zapisywać przy jej
zamknięciu. `handoff.py` buduje z niego snapshot w chwili zapisu i później
wczytuje dokładnie ten snapshot, nawet jeśli sesja źródłowa nadal działa.

| Gdzie | Polecenie | Efekt | Koszt modelu |
|---|---|---|---|
| sesja, którą chcesz potem kontynuować | `/handoff -save` albo `/handoff -save polecenie` | hook `UserPromptSubmit` blokuje prompt, zanim Claude Code uruchomi skill, buduje handoff z poleceniem na kontynuację i zapisuje snapshot do `~/.claude/handoffs/<id>.md` | brak |
| nowa sesja | `/handoff` | hook blokuje prompt i pokazuje listę zapisanych handoffów do wyboru, także gdy jest jeden, z rozmiarem snapshotu (KB i szacunek tokenów); nic nie wczytuje; przy braku mówi, jak zapisać | brak |
| nowa sesja | `/handoff N` | wczytuje pozycję N z ostatnio pokazanej listy | rozmiar snapshotu |

Nieprawidłowy, nieaktualny albo nieobecny numer pokazuje listę ponownie.

Szacunek tokenów to liczba znaków podzielona przez 2. Taki stosunek wyszedł
z pomiaru na transkryptach (przyrost `usage` po dużych wynikach narzędzi): polski
markdown ma medianę 2,0 znaku na token (p10–p90: 1,9–2,2), kod i angielski 2,3.
Dokładne liczenie wymaga endpointu API `count_tokens`, więc offline podawany
jest tylko szacunek.

Zapis i wczytanie to jedno polecenie `/handoff`; pierwszy argument `-save`
oznacza zapis. Myślnik sprawia, że zapisu nie da się pomylić z wyszukiwaniem
po słowie. Hook dostaje surowy tekst promptu przed rozwinięciem skilla i
przechwytuje dwa polecenia: `/handoff -save …` oraz samo `/handoff` bez
argumentu. Blokuje je, więc skill w ogóle się nie uruchamia. Nieprawidłowe
wybory również kończą się listą bez modelu. Prawidłowe polecenie z numerem
wyboru (np. `/handoff 1`) przepuszcza do skilla, bo
wczytany skrót ma przeczytać model. Gdy hook nie jest zainstalowany albo jest
wyłączony (`disableAllHooks`), oba przechwytywane polecenia trafiają do
skilla, który daje ten sam wynik przez model; zapis wyraźnie to zgłasza.
Wynik jest poprawny, a jedyną stratą są tokeny.

`/handoff` wczytuje wyłącznie zapisane handoffy. Aby kontynuować starszą
sesję, w której nie zrobiono zapisu, otwórz ją z historii sesji (VSCode albo
`claude --resume`) i wpisz w niej `/handoff -save …`. Otwarcie sesji i zapis
nie wywołują modelu.

### Zapis i polecenie na kontynuację

- Każda sesja ma najwyżej jeden zapisany handoff. Kolejne `/handoff -save …`
  w tej samej sesji nadpisuje poprzednie polecenie i zapisuje handoff ponownie,
  także gdy poprzedni był już wczytany.
- Tekst po `-save` jest poleceniem dla modelu w nowej sesji, np.
  `/handoff -save dokończ testy hooka i uruchom pełną suitę`. Po wczytaniu
  model pisze jedno lub dwa zdania o stanie sesji i od razu wykonuje
  polecenie. Bez polecenia (samo `/handoff -save`) model streszcza sesję i
  czeka.
- To samo zdanie jest widoczne na liście zapisanych handoffów.
- Zapisany handoff jest ważny 14 dni. Po wczytaniu jest oznaczany jako zużyty
  i znika z `/handoff`, więc druga nowa sesja nie weźmie go ponownie. Aby
  wczytać go jeszcze raz, zrób ponownie `/handoff -save` w tamtej sesji.
- `/handoff` wczytuje snapshot z chwili `/handoff -save …`. Praca wykonana
  później w sesji źródłowej nie zmienia już tej paczki.

### Kilka równoległych sesji

- Każda sesja ma własny zapisany handoff. `/handoff` zawsze pokazuje listę
  (także z jedną pozycją), od najświeższego zapisu, żeby wczytanie było
  świadomym wyborem.
- Pozycja listy zawiera tytuł sesji, czas zapisu, początek id, gałąź, katalog
  roboczy (gdy jest inny niż bieżący), status sesji `● aktywna (nazwa)` albo
  `zamknięta` oraz polecenie na kontynuację. Status pochodzi z rejestru
  `~/.claude/sessions` ze sprawdzeniem, czy proces żyje.
- Przy wczytaniu snapshotu z sesji, która nadal działa, pojawia się ostrzeżenie,
  że paczka pochodzi z chwili zapisu.
- Transkrypty są szukane we wszystkich `~/.claude/projects/*/`, bo sesja mogła
  być uruchomiona w innym katalogu niż bieżący. Widoczne są tylko sesje z tej
  maszyny i tego katalogu konfiguracji.

### Zawartość skrótu

- Zakres liczony od ostatniego `/clear` w transkrypcie.
- Z ostatniego kompaktowania tylko sekcje Primary Request, Pending Tasks,
  Current Work i Next Step, z datą i zastrzeżeniem, że późniejsze wymiany mają
  pierwszeństwo.
- Chronologia wcześniejszych kompaktowań: z każdego sekcja Current Work (do
  500 znaków), od najstarszego. Łączny limit to 12 000 znaków; gdy się
  wyczerpie, najstarsze kompaktowania są pomijane, a skrót podaje ich liczbę.
- Dokumenty zapisane w sesji (`.md`, `.txt`, `.tsv`, do 40) jako osobna lista,
  żeby raporty i noty nie ginęły wśród plików kodu.
- Pozostałe pliki zmieniane narzędziami Edit/Write/MultiEdit/NotebookEdit (do
  40, ostatnio zmieniane na końcu) oraz liczba wywołań Bash.
- Powiązane sesje (do 8): inne transkrypty, których tytuł, ostatni prompt
  albo polecenie zapisanego handoffu zawiera identyfikator z tytułu lub
  polecenia bieżącego handoffu. Identyfikator to litery z co najmniej dwiema
  cyframi, np. `B421` albo `JIRA-123`. Skrót podaje ścieżki transkryptów, a
  nie ich treść.
- Ostatnie 4 wymiany: prompt użytkownika i ostatnia odpowiedź Claude'a w danej
  turze. Ostatnia odpowiedź ma limit 6000 znaków, wcześniejsze 1200. Bloki kodu
  dłuższe niż 6 linii są zastępowane znacznikiem.
- Ścieżka do pełnego transkryptu na wypadek, gdyby potrzebne były szczegóły.

Przykładowo skrót sesji z transkryptem 479 MB i 39 kompaktowaniami ma około
10 tys. tokenów (bez chronologii około 4,5 tys.) i powstaje w około 2,3 s. Hook przy każdym zwykłym prompcie kończy się w
kilkadziesiąt milisekund.

## Wymagania

- Claude Code z obsługą skilli (`SKILL.md`) i hooków `UserPromptSubmit`.
- `python3` (3.8 lub nowszy) w `PATH`. Bez zewnętrznych pakietów.
- Linux albo macOS: skrypt używa `fcntl` do blokady pliku stanu.

## Instalacja automatyczna

```bash
/config/workspace/Skills/Claude/Handoff/install.sh
```

Skrypt:

1. kopiuje `handoff.py` do `~/.claude/skills/handoff/`;
2. tworzy `SKILL.md` z `SKILL.md.template`, wstawiając pełną ścieżkę skilla;
3. robi kopię `~/.claude/settings.json` jako `settings.json.bak-handoff` i
   dopisuje hook `UserPromptSubmit`. Istniejące hooki zostają, a ponowne
   uruchomienie nie dubluje wpisu.

Jeśli ustawiony jest `CLAUDE_CONFIG_DIR`, instalator i skrypt używają tego
katalogu zamiast `~/.claude`.

Odinstalowanie:

```bash
/config/workspace/Skills/Claude/Handoff/install.sh --uninstall
```

Usuwa skill i wpis hooka. Zapisane handoffy zostają w `~/.claude/handoffs/`.

## Instalacja ręczna

1. Skopiuj skrypt:

   ```bash
   mkdir -p ~/.claude/skills/handoff
   cp handoff.py ~/.claude/skills/handoff/
   ```

2. Utwórz `~/.claude/skills/handoff/SKILL.md` z szablonu, zamieniając
   `__SKILL_DIR__` na pełną ścieżkę katalogu skilla (bez `~`):

   ```bash
   sed "s#__SKILL_DIR__#$HOME/.claude/skills/handoff#g" SKILL.md.template \
     > ~/.claude/skills/handoff/SKILL.md
   ```

   Ścieżka musi być literalna: Claude Code odrzuca w poleceniach skilla
   rozwinięcia powłoki typu `$HOME`, a `allowed-tools` dopasowuje dokładny
   tekst polecenia.

3. Dopisz hook do `~/.claude/settings.json` (pełna ścieżka zamiast
   `/HOME`). Jeśli klucz `hooks` już istnieje, dołóż tylko element tablicy
   `UserPromptSubmit`:

   ```json
   {
     "hooks": {
       "UserPromptSubmit": [
         {
           "hooks": [
             {
               "type": "command",
               "command": "python3 /HOME/.claude/skills/handoff/handoff.py hook-prompt",
               "timeout": 60
             }
           ]
         }
       ]
     }
   }
   ```

4. Uruchom nową sesję. Skill i hook są wczytywane przy starcie sesji, więc
   sesje działające w chwili instalacji ich nie widzą.

## Sprawdzenie po instalacji

1. W dowolnej sesji z kilkoma wymianami wpisz `/handoff -save`. Zamiast
   odpowiedzi modelu powinien pojawić się komunikat „Bez wywołania modelu.
   Handoff zapisany…” ze ścieżką pliku `.md`. Komunikat zaczynający się od
   „HANDOFF ZAPISANY PRZEZ SKILL” oznacza, że hook nie działa.
2. W nowej sesji wpisz `/handoff`. Zamiast odpowiedzi modelu powinien
   pojawić się komunikat „Bez wywołania modelu.” z listą, w której ta sesja
   jest pozycją 1. Wpisz `/handoff 1`: model powinien w 2–4 zdaniach streścić
   tamtą sesję i czekać na polecenie.
3. Ponowne `/handoff` powie, że nie ma zapisanych handoffów, bo ten został już wczytany.

Bez modelu, z terminala (`load` bez argumentu tylko pokazuje listę; wczytanie
pozycji, np. `load --self brak -- 1`, oznacza handoff jako zużyty):

```bash
python3 ~/.claude/skills/handoff/handoff.py load --self brak --
python3 ~/.claude/skills/handoff/handoff.py show <pełne-id-sesji>
python3 ~/.claude/skills/handoff/handoff.py save <pełne-id-sesji> polecenie na kontynuację
```

## Pliki

| Plik | Rola |
|---|---|
| `handoff.py` | parser transkryptów, zapis i lista przez hook, wczytanie |
| `SKILL.md.template` | definicja skilla `/handoff` z miejscem na ścieżkę |
| `install.sh` | instalacja i `--uninstall` |
| `~/.claude/handoffs/state.json` | zapisane handoffy, ich zużycie i ostatnio pokazane listy (tworzony w trakcie działania) |
| `~/.claude/handoffs/<id>.md` | skrót zapisany przez `/handoff -save` (tworzony w trakcie działania) |

## Ograniczenia

- Claude Code domyślnie usuwa transkrypty starsze niż 30 dni
  (`cleanupPeriodDays`). Plik `.md` zapisany przez `/handoff -save` zostaje.
- Hook `SessionEnd` nie jest używany celowo: transkrypt i tak jest kompletny,
  a automatyczne przypinanie każdej sesji zapełniłoby listę `/handoff`
  sesjami, których nikt nie zamierzał kontynuować.
- Wczytanie przyjmuje wyłącznie numer pozycji z ostatnio pokazanej listy.
  Nieprawidłowy numer wraca do listy.
- Format transkryptu nie jest publicznym kontraktem Claude Code. Po
  aktualizacji, która go zmieni, parser może wymagać poprawki.
