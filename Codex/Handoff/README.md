# Codex Handoff

Globalny skill `codex-handoff` przenosi odchudzony snapshot między lokalnymi sesjami Codex. Snapshot jest budowany w chwili `/handoff -save` i pozostaje niezmienny. Przy wczytaniu do nowej sesji trafiają tylko wybrane wiadomości i metadane; wyniki narzędzi nie są kopiowane.

## Polecenia

| Polecenie | Efekt | Model |
|---|---|---|
| `/handoff -save` | zapisuje snapshot bieżącej sesji | nie jest wywoływany |
| `/handoff -save polecenie` | zapisuje snapshot i notę na kontynuację | nie jest wywoływany |
| `/handoff` | pokazuje listę zamrożonych snapshotów | nie jest wywoływany |
| `/handoff N` | wczytuje snapshot wybrany numerem z listy do bieżącej sesji | wywoływany z odchudzonym kontekstem |

Snapshot zachowuje ostatnie wymiany użytkownika i Codex, ścieżkę sesji, katalog, gałąź, ścieżki wskazane w patchach oraz liczbę wywołań `exec`. Wyniki narzędzi i długie bloki kodu są pomijane albo skracane.

Każda sesja ma jeden snapshot. Kolejny zapis go zastępuje. Snapshot jest widoczny przez 14 dni i po wczytaniu zostaje oznaczony jako zużyty. Pliki robocze są przechowywane w `$CODEX_HOME/handoffs/`.

## Instalacja

```bash
/config/workspace/Skills/Codex/Handoff/install.sh
```

Instalator kopiuje skill do `$HOME/.agents/skills/codex-handoff/` i dopisuje hook `UserPromptSubmit` do `$CODEX_HOME/hooks.json`. Po instalacji otwórz `/hooks` w Codex i zaufaj hookowi. Nowe sesje zobaczą konfigurację po ponownym uruchomieniu.

Odinstalowanie:

```bash
/config/workspace/Skills/Codex/Handoff/install.sh --uninstall
```

## Zakres

Skill działa w lokalnym Codex CLI, IDE i desktopie, gdy lokalne hooki są włączone. `/resume` nadal służy do wznowienia pełnej sesji; handoff przekazuje kontrolowany wyciąg. Nieprawidłowy numer handoffu pokazuje listę ponownie.
