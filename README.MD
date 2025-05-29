## setup

-   `git clone git@github.com:vielhuber/hurz.git .`
-   `cp pre-commit .git/hooks/pre-commit`
-   `chmod +x .git/hooks/pre-commit`
-   `python -m venv venv`
-   `pip install -r requirements.txt`
-   `cp .env.example .env`

## usage

-   `python script.py`
-   `python watcher.py`

## create .env

-   log in to https://pocketoption.com
-   go to https://pocketoption.com/cabinet/demo-quick-high-low
-   run in console

```js
console.log(`IP_ADDRESS=${
    decodeURIComponent(
        document.cookie
            .split('; ')
            .find((c) => c.startsWith('ci_session='))
            ?.split('=')[1]
    ).match(/s:10:"ip_address";s:\d+:"([^"]+)"/)?.[1]
}
USER_ID=${
    decodeURIComponent(
        document.cookie
            .split('; ')
            .find((c) => c.startsWith('autologin='))
            ?.split('=')[1]
    ).match(/s:7:"user_id";s:\d+:"(\d+)"/)?.[1]
}
LIVE_SUFFIX_ID=${decodeURIComponent(
    document.cookie
        .split('; ')
        .find((c) => c.startsWith('ci_session='))
        ?.split('=')[1]
)
    .split('}')
    .pop()}
LIVE_SESSION_ID=${
    decodeURIComponent(
        document.cookie
            .split('; ')
            .find((c) => c.startsWith('ci_session='))
            ?.split('=')[1]
    ).match(/s:32:"([a-f0-9]{32})"/)?.[1]
}
DEMO_SESSION_ID=${AppData.demoSessionId}
`);
```
