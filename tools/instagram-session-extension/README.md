# Saver: Instagram session extension

Instagram blocks password logins of the service account from the server IP
(`feedback_required`). This Chrome extension takes the session from a browser
where a person is logged in and sends it to the bot, the same way session-based
automation services work.

## Server

Set a random token in the environment of each server:

```
SAVER_BACKEND_INSTAGRAM_SESSION_TOKEN=<random string>
```

With the token set the bot stops logging in with the password. It checks the
received session every 6 hours and, when Instagram drops it, sends
"Instagram session needs attention" to the admin chat once a day.

## Browser

1. Use a separate Chrome profile for the service account only.
2. `chrome://extensions` -> Developer mode -> Load unpacked -> this folder.
3. Log in to the service account on instagram.com.
4. Open the extension, enter the token for each server, press "Save" and then
   "Send now". Every server should answer `ok, <account>`.

After that the extension sends the session whenever Instagram renews it and
every 6 hours while Chrome is open. If the bot reports that the session is
gone, open instagram.com in this profile and log in again.
