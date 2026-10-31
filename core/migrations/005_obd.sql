-- OBD-II data of a Teltonika FMB003 (see core/obd.py) and the codec a device sends with.

ALTER TABLE devices ADD COLUMN codec TEXT NOT NULL DEFAULT '8';               -- '8' or '8E' (Codec 8 Extended)
ALTER TABLE devices ADD COLUMN obd_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE devices ADD COLUMN vin TEXT;
ALTER TABLE devices ADD COLUMN obd_odometer_km REAL NOT NULL DEFAULT 50000;  -- OEM total mileage (IO 389)
ALTER TABLE devices ADD COLUMN obd_service_km REAL NOT NULL DEFAULT 15000;   -- distance until service (IO 402)
ALTER TABLE devices ADD COLUMN obd_fuel_pct REAL NOT NULL DEFAULT 70;        -- fuel level (IO 48)
ALTER TABLE devices ADD COLUMN obd_tank_l REAL NOT NULL DEFAULT 55;          -- tank size, for the fuel level
ALTER TABLE devices ADD COLUMN obd_dtcs TEXT NOT NULL DEFAULT '';            -- fault codes "P0420,P0171" (IO 281)
ALTER TABLE devices ADD COLUMN obd_mil_km REAL NOT NULL DEFAULT 0;           -- distance with MIL on (IO 43)
ALTER TABLE devices ADD COLUMN obd_coolant_c REAL NOT NULL DEFAULT 15;       -- coolant temperature (IO 32)
ALTER TABLE devices ADD COLUMN obd_weak_battery INTEGER NOT NULL DEFAULT 0;  -- weak battery while parked
