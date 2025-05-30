## setup

-   `git clone git@github.com:vielhuber/hurz.git .`
-   `python -m venv venv`
-   `pip install -r requirements.txt`
-   `cp .env.example .env`

## usage

-   `python script.py`
-   `python watcher.py`

## models

-   place your custom models inside `external` (see `random.py`)
-   premium models can be decrypted via `unzip -o -P PREMIUMPASSWORD external/models.zip`
-   to automatically push changed models encrypted use `cp post-commit .git/hooks/post-commit && chmod +x .git/hooks/post-commit`

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
