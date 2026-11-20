---
name: codex-handoff
description: Zapisuje zamrożony, odchudzony wyciąg sesji Codex i wczytuje go do nowej sesji numerem z listy; używaj przy przekazywaniu pracy między sesjami, zapisywaniu stanu i kontynuowaniu zadania.
---

`UserPromptSubmit` przechwytuje `/handoff -save [polecenie]` oraz samo `/handoff` bez wywołania modelu. Przy zapisie skrypt natychmiast czyta transcript, pomija wyniki narzędzi i zapisuje zamrożony snapshot. Późniejsze zmiany źródłowej sesji nie zmieniają snapshotu.

Przy `/handoff N` hook przekazuje snapshot jako dodatkowy kontekst bieżącego promptu. Nieprawidłowy numer pokazuje listę ponownie. Jeśli snapshot zawiera polecenie na kontynuację, wykonaj je od razu. Bez takiego polecenia streść stan sesji po polsku w 2–4 zdaniach i czekaj na dalsze polecenie.

`/resume` pozostaje osobną funkcją do wznowienia pełnej sesji. Handoff służy do kontrolowanego przekazania odchudzonego kontekstu.
