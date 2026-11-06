# Deployment

The trading side runs on an always-on x86_64 Debian host: PostgreSQL 18 in Docker, OpenD and the app under systemd, and a daily backup. Requirements are in the openspec `deployment` spec.

| Service | systemd unit | User | Listens on |
| --- | --- | --- | --- |
| PostgreSQL 18 (Docker) | `tradingagentspro-db` | root | 127.0.0.1:5432 |
| OpenD (CLI) | `tradingagentspro-opend` | `tradingagentspro` | 127.0.0.1:11111 (API), 127.0.0.1:22222 (telnet) |
| App (`tradingagentspro run`) | `tradingagentspro` | `tradingagentspro` | — |
| Backup (daily 06:00 UTC) | `tradingagentspro-backup.timer` | root | — |

| Path | Contents |
| --- | --- |
| `/opt/tradingagentspro/app` | Repo clone with `.venv` |
| `/opt/tradingagentspro/app/config/` | `settings.toml` and `models.toml`, written on first start, service user only, not in git or backups |
| `/opt/tradingagentspro/opend` | OpenD |
| `/etc/tradingagentspro/` | Env files for PostgreSQL and OpenD, root only (templates: `*.env.example`) |
| `/var/backups/tradingagentspro/` | Backups, root only |

Commands are marked `# admin` (a sudo user on the host), `# service` (after `sudo -iu tradingagentspro`) or `# mac`. Secrets go only into `/etc/tradingagentspro/` and `config/`.

## First deployment

1. Prerequisites: Debian 12 or 13, NTP synced (`timedatectl`), [Docker Engine with the compose plugin](https://docs.docker.com/engine/install/debian/).

   ```bash
   # admin
   sudo apt install git telnet curl openssl
   curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
   sudo mkdir -p /var/log/journal && sudo systemctl restart systemd-journald
   ```

2. Service user and code. Add the printed key as a read-only GitHub deploy key.

   ```bash
   # admin
   sudo adduser --system --group --home /opt/tradingagentspro --shell /bin/bash tradingagentspro
   sudo -iu tradingagentspro
   ```

   ```bash
   # service
   ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 && cat ~/.ssh/id_ed25519.pub
   git clone git@github.com:wljf817/TradingAgentsPro.git ~/app
   cd ~/app && uv sync --locked --no-dev
   ```

3. Accounts:
   - Alpaca: reset the paper account to $2,000 and generate a new key pair
   - Discord: a bot with scopes `bot` and `applications.commands`, and View Channels, Send Messages, Embed Links in the alert channel; copy the server, channel and your user IDs
   - Finnhub: a free-tier API key
   - Models: the first start writes `config/models.toml` under the app directory, all stand-ins and readable only by the service user. For real models, add the vendor's interface, base URL and API key under `vendors`, update `agents` and `prices`, and restart. The file is not in git or in backups

4. Env files and settings. Fill each env file from its comments; the database password goes in `postgres.env` and later in the URL in `config/settings.toml`.

   ```bash
   # admin
   sudo install -d -m 700 /etc/tradingagentspro
   for name in postgres opend; do
     sudo install -m 600 /opt/tradingagentspro/app/deploy/$name.env.example /etc/tradingagentspro/$name.env
   done
   openssl rand -hex 24
   ```

   The first run writes `config/settings.toml` and `config/models.toml`, lists the nine required items and exits without connecting to anything. Fill those items in, inside the quotes:

   ```bash
   # service
   cd ~/app && .venv/bin/tradingagentspro run
   nano config/settings.toml
   ```

5. OpenD. Download the Linux CLI build matching `moomoo-api` in `uv.lock` from <https://www.moomoo.com/download/OpenAPI> into `~/opend`. In `~/opend/OpenD.xml`, set `<telnet_port>22222</telnet_port>`. Then log in once interactively, saving the password:

   ```bash
   # service
   cd ~/opend && ./OpenD -api_ip=127.0.0.1 -api_port=11111
   ```

   If asked for an SMS code, enter `input_phone_verify_code -code=<code>`. After login, `exit`.

6. Install units and start everything:

   ```bash
   # admin
   sudo install -m 644 /opt/tradingagentspro/app/deploy/systemd/* /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now tradingagentspro-db tradingagentspro-opend tradingagentspro tradingagentspro-backup.timer
   sudo journalctl -u tradingagentspro -f
   ```

   The log ends with `启动完成`. In Discord, `/plan close symbol:XYZ` replies that there is no XYZ position. Stop OpenD on the Mac for good.

7. Check a backup restores:

   ```bash
   # admin
   sudo systemctl start tradingagentspro-backup
   cd /opt/tradingagentspro/app/deploy
   sudo docker compose exec -T postgres createdb --username=tradingagentspro restore_check
   sudo sh -c 'docker compose exec -T postgres pg_restore --username=tradingagentspro --dbname=restore_check < "$(ls /var/backups/tradingagentspro/*.dump | tail -n 1)"'
   sudo docker compose exec -T postgres psql --username=tradingagentspro --dbname=restore_check -c '\dn'
   sudo docker compose exec -T postgres dropdb --username=tradingagentspro restore_check
   ```

   `\dn` lists `dbos` and `public`. Reboot once and check all three services come back.

## Daily check

```bash
# admin
systemctl --failed --no-pager
sudo ls -lt /var/backups/tradingagentspro | head -n 3
sudo journalctl -u tradingagentspro --since today --no-pager | grep -E ' (WARNING|ERROR) '
```

Program actions (risk, orders, stops, confirmations, reconciliation, degradation, alerts) are in the `programlog_entries` table, in UTC:

```bash
# admin
cd /opt/tradingagentspro/app/deploy && sudo docker compose exec postgres psql --username=tradingagentspro --dbname=tradingagentspro \
  -c 'select id, recorded_at, actor, action, subject, detail from programlog_entries order by id desc limit 50'
```

## Settings

`config/settings.toml` holds the app's parameters and secrets. Items whose value is `""` are required. The rest are comments showing the default: remove the leading `#` to set one. Durations are in seconds. Restart to apply; the log lists the items that differ from their defaults, and a mistake fails startup with the items named. The file is not in backups, so write it again on a new host.

## Move to the settings file

Once, on a host still using `/etc/tradingagentspro/tradingagentspro.env`. The app no longer reads environment variables, so do this instead of a normal update.

1. Back up and stop the app (`# admin`): `sudo systemctl start tradingagentspro-backup`, `sudo systemctl stop tradingagentspro`
2. Update and generate the file (`# service`): `cd ~/app && git pull --ff-only && uv sync --locked --no-dev`, then `.venv/bin/tradingagentspro run`. It lists the nine required items and exits without connecting to anything
3. Copy the nine values from `sudo cat /etc/tradingagentspro/tradingagentspro.env` into `config/settings.toml`, inside the quotes:

   | Old variable | New item |
   | --- | --- |
   | `TRADINGAGENTSPRO_DATABASE_URL` | `[database]` `url` |
   | `APCA_API_KEY_ID` | `[alpaca]` `key_id` |
   | `APCA_API_SECRET_KEY` | `[alpaca]` `secret_key` |
   | `DISCORD_BOT_TOKEN` | `[discord]` `token` |
   | `DISCORD_GUILD_ID` | `[discord]` `guild_id` |
   | `DISCORD_CHANNEL_ID` | `[discord]` `channel_id` |
   | `DISCORD_USER_ID` | `[discord]` `user_id` |
   | `FINNHUB_API_KEY` | `[finnhub]` `api_key` |
   | `SEC_USER_AGENT` | `[sec]` `user_agent` |

4. Install the new unit and start (`# admin`): `sudo install -m 644 /opt/tradingagentspro/app/deploy/systemd/tradingagentspro.service /etc/systemd/system/`, `sudo systemctl daemon-reload`, `sudo systemctl start tradingagentspro`. The log shows `启动完成`
5. Once it runs, delete the old file: `sudo rm /etc/tradingagentspro/tradingagentspro.env`

Rollback before step 5: check out the previous commit, sync, reinstall the old unit, `daemon-reload`, start.

## Update

1. Back up: `sudo systemctl start tradingagentspro-backup`
2. Pull (`# service`): `cd ~/app && git pull --ff-only && git diff --stat 'HEAD@{1}' -- deploy uv.lock`
3. If `moomoo-api` changed in `uv.lock`, upgrade OpenD first (see below)
4. Sync (`# service`): `uv sync --locked --no-dev`, then `exit`
5. Restart (`# admin`): `sudo systemctl restart tradingagentspro`. If `deploy/` changed, first add new variables from `*.env.example`, reinstall the units (step 6 above), `daemon-reload`, and restart any changed service
6. Check the log shows `启动完成`. Migrations run at startup. If it reports missing items, the update added required ones: fill them into `config/settings.toml` and restart

Rollback: `git checkout <previous commit>`, `uv sync --locked --no-dev`, restart. If the update added a migration, restore the step 1 backup first.

## Restore a backup

```bash
# admin
sudo systemctl stop tradingagentspro
cd /opt/tradingagentspro/app/deploy
sudo docker compose exec -T postgres dropdb --username=tradingagentspro --force tradingagentspro
sudo docker compose exec -T postgres createdb --username=tradingagentspro tradingagentspro
sudo sh -c 'docker compose exec -T postgres pg_restore --username=tradingagentspro --dbname=tradingagentspro < /var/backups/tradingagentspro/<file>.dump'
sudo systemctl start tradingagentspro
```

If startup reconciliation then reports discrepancies or stop orders get rejected (reused client order IDs), do a full reset instead.

## Full reset

The database and the Alpaca paper account must be reset together. Run the first four lines of the restore above, reset the paper account to $2,000 with a new key pair, update `key_id` and `secret_key` under `[alpaca]` in `config/settings.toml`, then `sudo systemctl start tradingagentspro`.

## OpenD

- Re-login (expired login or changed password): stop `tradingagentspro-opend`, log in interactively as in step 5, start it again
- SMS code while running, or quote right taken by another device: `telnet 127.0.0.1 22222`, then `req_phone_verify_code` / `input_phone_verify_code -code=<code>`, or `request_highest_quote_right`
- Login succeeded when `sudo ls -t /opt/tradingagentspro/.com.moomoo.OpenD/Log | head` shows a new `GTWLog_<moomoo ID>_…` file
- Upgrade: stop the service, move `~/opend` to `~/opend.old`, install the new build as in step 5, copy `Device.dat` from the old directory, start the service

## Developing on the Mac

- Don't run OpenD on the Mac; tunnel to the host's instead: `ssh -N -L 11111:127.0.0.1:11111 <user@host>`
- Before smoke tests that use Discord or Alpaca, `sudo systemctl stop tradingagentspro` on the host, and start it again afterwards
- `uv run tradingagentspro run` uses `config/settings.toml` in the repo root, written on the first run; smoke tests still read `~/.config/tradingagentspro/dev.env`
