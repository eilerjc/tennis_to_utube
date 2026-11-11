# Setting up YouTube uploads

The uploader talks to YouTube through Google's official YouTube Data API. Google requires
every app that does this to identify itself with an **OAuth client id** from a Google Cloud
project. This app does not ship one: you create your own, once, for free. It takes about
ten minutes. The app never sees your Google password; you sign in on Google's own page in
your browser.

## 1. Create the Google Cloud project

1. Go to <https://console.cloud.google.com/> and sign in with the Google account that owns
   the YouTube channel.
2. Top bar → project picker → **New project**. Name it anything (e.g. "Tennis uploads").
3. With the project selected: **APIs & Services → Library**, search for
   **YouTube Data API v3**, open it and press **Enable**.

## 2. Set up the consent screen

1. **APIs & Services → OAuth consent screen** (also called "Google Auth Platform").
2. User type **External**. Fill in the app name and your e-mail address where asked.
3. Under **Data access / Scopes**, add:
   - `https://www.googleapis.com/auth/youtube.upload`
   - `https://www.googleapis.com/auth/youtube`
4. Under **Audience**, either:
   - add your own Google account as a **Test user** (simplest; but in "Testing" mode
     Google makes you sign in again every 7 days), or
   - press **Publish app** ("In production"). The sign-in then lasts; Google shows an
     "unverified app" warning once when you connect — choose *Advanced → Go to …*.

## 3. Create the client id

1. **APIs & Services → Credentials → Create credentials → OAuth client ID**.
2. Application type: **Desktop app**. Create.
3. Copy the **Client ID** and **Client secret**.

## 4. Tell the app

Create `%APPDATA%\tennis_to_utube\upload.toml` (see `docs\upload.example.toml`) with:

```toml
[google]
client_id = "1234567890-abc….apps.googleusercontent.com"
client_secret = "GOCSPX-…"
```

Start the app, open the **Upload** step and press **Connect to YouTube…** (or run
`upload.bat --connect`). Your browser opens Google's sign-in page; allow access, and the
Upload step shows your channel's name. **Disconnect** forgets the sign-in on this PC and
asks Google to withdraw it.

The sign-in is stored in `%APPDATA%\tennis_to_utube\youtube_token.bin`, encrypted so that
only your Windows user on this PC can read it.

## Two things Google decides

**Videos may be locked to Private.** Google restricts videos uploaded by API projects it
has not audited: they are set to Private whatever privacy you chose, and (from what is
documented) cannot be switched to Unlisted or Public afterwards. The Upload step tells you
when this happened to a video ("YouTube made this video private, not unlisted"). To lift
it, submit the **YouTube API Services – Audit and Quota Extension form** for your project
(linked from the YouTube Data API's page in the Cloud console). It is free; approval takes
days to weeks. Do a small test upload first to see whether it applies to you.

**There is a daily limit** on uploads through the API (shown under *APIs & Services →
YouTube Data API v3 → Quotas* for your project). When it is reached the upload stops with
a clear message, keeps its place, and **Retry** the next day continues it.

## Where things are kept

| What | Where |
|---|---|
| Settings | `%APPDATA%\tennis_to_utube\upload.toml` |
| The queue (one file per upload, with its checkpoint) | `%APPDATA%\tennis_to_utube\uploads\*.json` |
| Log | `%APPDATA%\tennis_to_utube\uploads\upload.log` |
| Sign-in (encrypted) | `%APPDATA%\tennis_to_utube\youtube_token.bin` |
