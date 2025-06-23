## setup

-   `sudo apt install -y git unzip python3 python3-pip python3-venv`
-   install nvidia cuda according to https://developer.nvidia.com/cuda-downloads
-   `git clone https://github.com/vielhuber/hurz.git .`
-   `python3 -m venv venv`
-   `pip3 install -r requirements.txt`
-   `cp .env.example .env`

## usage

-   `python3 script.py`

## watch changes

-   `python3 watcher.py`

## models

-   place your custom models inside `external` (see `random.py`)

## create .env

-   log into https://pocketoption.com
-   go to https://pocketoption.com/cabinet/demo-quick-high-low
-   run in console

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
ZIP_PASSWORD="${Array.from(crypto.getRandomValues(new Uint8Array(32)), (x) =>
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()_+-=[]{}|;:,.<>?'.charAt(x % 89)
).join('')}"
`);
```
