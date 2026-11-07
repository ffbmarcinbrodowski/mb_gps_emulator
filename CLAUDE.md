# CLAUDE.md – MB GPS Emulator

Emulator nadajników GPS Teltonika: wirtualne urządzenia jeżdżą realistycznie po trasach GraphHoppera i wysyłają
dane AVL Codec 8 po TCP do Traccara. Wspólny rdzeń `core/`, interfejsy: `web/` (Flask, Docker) i `desktop/` (PySide6).
Dokumentacja dla użytkowników: `README.md`.

## Zanim zaczniesz – folder `referenz/` (tylko lokalnie)

`referenz/` to lokalne notatki projektu (po polsku), celowo **poza Gitem** (`.gitignore`) – w publicznym repo
i w świeżym klonie go nie ma. Jeśli folder nie istnieje, pomiń ten punkt i pracuj na kodzie, README i tym pliku.
Nigdy nie dodawaj go do repozytorium. Jeśli istnieje, czytaj w tej kolejności:

1. **`referenz/STAN.md`** – co jest zrobione, decyzje i ich powody, otwarte tematy, środowisko, pliki poza Gitem.
   Przeczytaj na początku każdej nowej sesji.
2. **`referenz/WZORCE.md`** – katalog rozwiązań (A–H) z odwołaniami do kodu i **pułapkami, które już raz wystąpiły**
   (tabela G). Przeczytaj odpowiedni punkt, zanim zmienisz dany obszar.
3. `referenz/CLAUDE.md` – zasady pracy z folderem (także jako wzorca dla innych projektów).
4. `referenz/LIESMICH.md` – opis folderu dla użytkownika.

`referenz/kod/` to tylko **migawka** kodu – pracuj zawsze na aktualnych plikach w katalogu projektu.
Po większych zmianach zaproponuj odświeżenie `STAN.md`, `WZORCE.md` i `kod/`.

## Polecenia

```bash
python -m unittest discover -s tests -t .   # testy (offline, szybkie) – uruchom na start sesji
python -m web                               # web na :5057
python -m desktop                           # desktop (requirements-desktop.txt)
python tools/fake_server.py                 # serwer testowy Teltoniki na :5055
python seed.py [--reset]                    # dane demo
docker compose up -d --build                # web w Dockerze (potrzebny .env)
```

Eksperymenty na osobnej bazie: `EMULATOR_DB=/tmp/x.db python -m web`. Bazy użytkownika (`emulator.db`) nie dotykaj;
migracje testuj na kopii zrobionej przez SQLite `backup()`.

## Zasady, które obowiązują zawsze

- Odpowiadaj użytkownikowi **po polsku**; kod, komentarze, UI i README **po angielsku**.
- Logika tylko w `core/` – web i desktop mają działać tak samo. Schemat bazy tylko przez nową migrację.
- **Realizm danych jest celem** (dane idą do analiz): bez przyspieszania czasu na żywo; czasy w bazie/protokole UTC,
  w UI strefa aplikacji (`config.TIMEZONE`).
- Przed większą zmianą **kopia zapasowa** do `backup/<data_godzina>/` (baza przez SQLite `backup()`).
- Zmiany weryfikuj naprawdę (testy, fake server, zrzuty Playwright, Qt offscreen) i mów wprost, czego nie sprawdziłeś.
- **Git: commit/push tylko na prośbę.** Przed commitem `git status`, pliki dodawane **jawnie** (nie `git add -A`),
  skan pod kątem klucza GraphHoppera. Nigdy do Gita: `.env`, `graphhopper_api.txt`, `elevenlabs_api.txt`,
  `Traccar_login.txt`, `emulator.db`, `backup/`, `presentation/`, `referenz/`. Repozytorium jest **publiczne**.
- Prezentacja (`presentation/`) jest tylko lokalna – nie publikować w żadnej formie.
