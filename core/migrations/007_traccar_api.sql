-- Optional access to the Traccar REST API of a server (see core/traccar_api.py), used to create
-- an emulator device in Traccar too. NULL = no API access configured.

ALTER TABLE servers ADD COLUMN api_url TEXT;        -- e.g. http://10.40.1.114:8082
ALTER TABLE servers ADD COLUMN api_user TEXT;       -- Traccar login (e-mail)
ALTER TABLE servers ADD COLUMN api_password TEXT;   -- never sent to the UI

-- Id of the device in Traccar once it was created (or found) there; reset when the server changes.
ALTER TABLE devices ADD COLUMN traccar_id INTEGER;
