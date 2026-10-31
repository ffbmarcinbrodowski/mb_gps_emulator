Rozbuduj emulator o dane OBD-II urządzenia Teltonika FMB003, wysyłane w Codec 8 Extended.

## Po co
Mój drugi program (MB Garage, zarządzanie flotą i warsztatem) czyta dane z Traccar przez REST API
(`/api/positions`) i chce z nich brać: prawdziwy stan licznika auta, km do następnego przeglądu, kody błędów,
VIN, napięcie akumulatora. Ten emulator jest ich jedynym źródłem (Traccar to serwer testowy),
więc dane muszą być realistyczne i spójne z jazdą – tak jak reszta emulatora.

## Najpierw
1. Przeczytaj CLAUDE.md i trzymaj się jego zasad (angielski w kodzie i UI, migracje jako pliki SQL,
   reguły w `core/`, oba interfejsy web + desktop zachowują się tak samo, publiczne repo).
2. Zrób kopię zapasową projektu (`backup/<timestamp>/`, baza przez SQLite backup API).
3. Zaproponuj plan kroków i poczekaj na moją akceptację, dopiero potem pisz kod.

## Wymagania

### 1. Codec 8 Extended (0x8E) w `core/teltonika.py`
- Encoder i decoder (decoder też dla `tools/fake_server.py`). Codec 8 zostaje bez zmian.
- Różnice względem Codec 8 w rekordzie: event IO ID **2 bajty**, liczba IO **2 bajty**, liczniki N1/N2/N4/N8
  **2 bajty**, każde IO ID **2 bajty**, dodatkowa sekcja **NX** (elementy o zmiennej długości:
  liczba 2 B, potem dla każdego: ID 2 B + długość 2 B + wartość).
- Test zgodny z przykładem „Codec 8 Extended” z wiki Teltoniki (tak jak istniejący test Codec 8 – bajt w bajt, z CRC).
- Urządzenie ma ustawienie, którym kodekiem wysyła (domyślnie: Codec 8 Extended, gdy OBD jest włączone).

### 2. Symulacja OBD w nowym module `core/obd.py` (bez UI, używana przez live engine i historię)
Stan pojazdu zmienia się razem z jazdą (RecordBuilder w `core/recorder.py` dokłada IO):

| AVL ID | Dane (wiki Teltonika FMB003) | Bajty | Jednostka / mnożnik | W Traccar | Zachowanie |
|---|---|---|---|---|---|
| 389 | OBD OEM Total Mileage | 4 | km | `obdOdometer` (Traccar ×1000 = m) | start = wartość z ustawień urządzenia, rośnie z przejechanym dystansem |
| 402 | OEM Distance Until Service | 4 | km (signed – może być ujemne) | `io402` | maleje z dystansem; < 0 = przegląd po terminie |
| 30 | Number of DTC | 1 | – | `faultCount` | zwykle 0 |
| 281 | Fault Codes (DTC) | NX, ASCII | np. `P0420,P0171` | `io281` (Traccar zapisuje jako hex) | wysyłane tylko gdy 30 > 0 |
| 256 | VIN | NX, 17 B ASCII | – | `vin` | z ustawień urządzenia |
| 48 | Fuel Level | 1 | % | `io48` | spada z dystansem, czasem tankowanie (skok w górę na postoju) |
| 32 | Coolant Temperature | 1 | °C (signed) | `coolantTemp` | rośnie do ~90 po starcie, stygnie na postoju |
| 36 | Engine RPM | 2 | rpm | `rpm` | 0 bez zapłonu, bieg jałowy ~800, w jeździe zależnie od prędkości |
| 31 | Engine Load | 1 | % | `engineLoad` | zależnie od przyspieszania |
| 43 | Distance Traveled MIL On | 2 | km | `milDistance` | rośnie tylko, gdy są kody błędów |
| 60 | Fuel Rate | 2 | ×0,01 l/h | `io60` | zależnie od obciążenia |

Spójność z istniejącymi IO: 239 Ignition, 240 Movement, 66 External Voltage (14 V przy pracującym silniku,
~12,5 V bez), 16 Total Odometer. Wartości przetrwają restart (zapisane przy urządzeniu, jak `odometer_m`).

### 3. Ustawienia urządzenia (migracja + oba UI)
- OBD włączone tak/nie, VIN, początkowy stan licznika (km), km do przeglądu, poziom paliwa.
- Dla testów MB Garage: możliwość wywołania (jak istniejące zdarzenia w `core/events.py`):
  **„Fehlercode setzen / löschen”** (30 + 281 + MIL), **„schwache Batterie”** (66 < 12,0 V przy wyłączonym
  zapłonie), **„Service fällig”** (402 ustawione blisko 0 albo ujemne).
- `seed.py`: demo-urządzenia dostają realistyczne VIN-y, liczniki 20 000–250 000 km i różne km do przeglądu.

### 4. Sprawdzenie
- Testy jednostkowe (bez sieci) dla encodera/decodera 8E i dla `core/obd.py`.
- `tools/fake_server.py` pokazuje zdekodowane IO z 8E.
- Prawdziwy Traccar: po wysłaniu `GET /api/positions` musi pokazać w `attributes` m.in. `obdOdometer`
  (w metrach), `faultCount`, `vin`, `io402`, `io48`, `coolantTemp`, `rpm`.
  Uwaga: Traccar przelicza 389 → `obdOdometer` tylko gdy **model urządzenia w Traccar = FMB003**
  (model ustawia się w Traccar, nie w protokole); inaczej przyjdzie surowe `io389`.
- README i CLAUDE.md zaktualizowane. Commit dopiero na moją prośbę.

Źródła: Teltonika wiki „FMB003 Teltonika Data Sending Parameters ID” (OBD i OBD OEM elements),
„Codec” (Codec 8 Extended), Traccar `TeltonikaProtocolDecoder.java` (mapowanie IO → atrybuty).
