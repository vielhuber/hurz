[![GitHub Tag](https://img.shields.io/github/v/tag/vielhuber/hurz)](https://github.com/vielhuber/hurz/tags)
[![Code Style](https://img.shields.io/badge/code_style-psr--12-ff69b4.svg)](https://www.php-fig.org/psr/psr-12/)
[![License](https://img.shields.io/github/license/vielhuber/hurz)](https://github.com/vielhuber/hurz/blob/main/LICENSE.md)
[![Last Commit](https://img.shields.io/github/last-commit/vielhuber/hurz)](https://github.com/vielhuber/hurz/commits)

## usage

-   `python3 hurz.py`

#### remote-controlling a running instance

Pass a flag to trigger the matching main-menu entry on the running instance:

```sh
python3 hurz.py --load        # Load historical data (current asset)
python3 hurz.py --verify      # Verify
python3 hurz.py --compute     # Compute features
python3 hurz.py --train       # Train model
python3 hurz.py --test        # Determine confidence / run fulltest
python3 hurz.py --trade       # Trade optimally
python3 hurz.py --refresh     # Refresh view (re-reads data/settings.json)
python3 hurz.py --exit        # Exit
```

`--auto-*` variants run the same step across **all** assets (Auto-Trade Mode):

```sh
python3 hurz.py --auto-load           # Load all historical data
python3 hurz.py --auto-verify         # Verify all
python3 hurz.py --auto-compute        # Compute features for all
python3 hurz.py --auto-train          # Train all models
python3 hurz.py --auto-test           # Fulltest all
python3 hurz.py --auto-trade          # Trade all optimally
python3 hurz.py --auto-all-no-trade   # Load → verify → compute → train → test (all)
python3 hurz.py --auto-all-trade      # Same as above + trade
```

To change the active asset from outside: edit `data/settings.json`, then
`--refresh`. Exit codes: 2 = unknown/multiple flags, 3 = no running instance.

## installation

#### install requirements

-   `sudo apt install -y git unzip python3 python3-pip python3-venv`
-   install nvidia cuda according to https://developer.nvidia.com/cuda-downloads
-   `git clone https://github.com/vielhuber/hurz.git .`
-   `python3 -m venv venv`
-   `pip3 install -r requirements.txt`
-   `cp .env.example .env`

#### setup local database

-   `mysql -u root -p`
-   `CREATE DATABASE IF NOT EXISTS hurz;`
-   `exit;`
-   modify `.env` and fill in credentials for `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USERNAME`, `DB_PASSWORD`

#### get trading platform variables

-   log into https://pocketoption.com
-   go to https://pocketoption.com/cabinet/demo-quick-high-low
-   run in console and copy those values in `.env`

```js
console.log(`IP_ADDRESS="${
    decodeURIComponent(
        document.cookie
            .split('; ')
            .find((c) => c.startsWith('ci_session='))
            ?.split('=')[1]
    ).match(/s:10:"ip_address";s:\d+:"([^"]+)"/)?.[1]
}"
USER_ID="${
    decodeURIComponent(
        document.cookie
            .split('; ')
            .find((c) => c.startsWith('autologin='))
            ?.split('=')[1]
    ).match(/s:7:"user_id";s:\d+:"(\d+)"/)?.[1]
}"
LIVE_SUFFIX_ID="${decodeURIComponent(
    document.cookie
        .split('; ')
        .find((c) => c.startsWith('ci_session='))
        ?.split('=')[1]
)
    .split('}')
    .pop()}"
LIVE_SESSION_ID="${
    decodeURIComponent(
        document.cookie
            .split('; ')
            .find((c) => c.startsWith('ci_session='))
            ?.split('=')[1]
    ).match(/s:32:"([a-f0-9]{32})"/)?.[1]
}"
DEMO_SESSION_ID="${AppData.demoSessionId}"
`);
```

#### add proxy (optional)

-   modify `PROXY="USERNAME:PASSWORD@IP_ADDRESS:PORT"` in .env

#### premium models (optional)

-   place your custom models inside `external` (see `random.py`)

## development

#### watch changes

-   `python3 watcher.py`

#### show log

-   `tail -f -n 10 ./tmp/log.txt`
