# Manual Testing Plan: Saver Bot

## How to Test

**Target Bot:** `@saver_dev_bot`

For each test case below:
1.  Copy the provided **Link**.
2.  Send it to the target bot in a private message.
3.  Observe the bot's response.
4.  Fill out the **Verification Steps** checklist by placing an `x` inside the brackets (`[x]`).
5.  Update the **Status** field (`✅ Pass`, `❌ Fail`).
6.  Add any relevant details or error messages to the **Notes** section.

---

## 1. Instagram

### Test Case 1.1: Reel Video (Type 1)

-   **Objective:** Verify successful and fast download of an Instagram Reel.
-   **Link:** `https://www.instagram.com/reel/DNBCJoiOp9J/`
-   **Status:** `✅ Pass`

-   **Verification Steps:**
    -   `[x]` The bot responds with a video.
    -   `[x]` The response is fast (uses direct URL).
    -   `[x]` The video has a thumbnail and correct metadata.
    -   `[x]` The caption is present and correct.

-   **Notes:**
    -   Works as expected.

---

### Test Case 1.2: Reel Video (Type 2)

-   **Objective:** Verify successful download of another common Reel link format.
-   **Link:** `https://www.instagram.com/reels/DLiK6FZoBnS/`
-   **Status:** `✅ Pass`

-   **Verification Steps:**
    -   `[x]` The bot responds with a video.
    -   `[x]` The response is fast (uses direct URL).
    -   `[x]` The video has a thumbnail and correct metadata.
    -   `[x]` The caption is present and correct.

-   **Notes:**
    -   Works as expected.

---

### Test Case 1.3: Post with Photos (Negative Test)

-   **Objective:** Verify how the bot handles a post containing only photos.
-   **Link:** `https://www.instagram.com/p/DQBmDNrDPLG/`
-   **Status:** `❌ Fail`

-   **Verification Steps:**
    -   `[ ]` The bot should respond with the photo(s) in an album.
    -   `[ ]` The caption should be applied to the album.

---

## 2. TikTok

### Test Case 2.1: Standard Video

-   **Objective:** Verify successful download of a standard TikTok video.
-   **Link:** `https://vt.tiktok.com/ZSkdKYMyL/`
-   **Status:** `❌ Fail`

-   **Verification Steps:**
    -   `[ ]` The bot responds with a video.
    -   `[ ]` The video has a thumbnail and correct metadata.
    -   `[ ]` The caption is present and correct.

-   **Notes:**
    -   **Issue:** The bot does not respond to this link. Logs should be checked for `yt-dlp` errors.
    -   *Developer Note:* `DIRECT_URL_DOWNLOAD` flag has been manually set to `False` for TikTok due to issues with Telegram's URL fetcher. The bot should be using the download-and-upload method.

---

### Test Case 2.2: Slideshow / Photo (Negative Test)

-   **Objective:** Verify the bot correctly identifies and handles unsupported TikTok slideshows by sending a specific error message.
-   **Link:** `https://vt.tiktok.com/ZSyNfEMJ1/`
-   **Status:** `✅ Pass`

-   **Verification Steps:**
    -   `[x]` The bot responds with a text message (not a video).
    -   `[x]` The message correctly states that photo/slideshow downloads are not currently supported.
    -   `[x]` The bot does not crash or time out.

-   **Notes:**
    -   The bot correctly identified this as a slideshow and sent the "not supported" message as expected. This is the correct behavior for this negative test case.
---

## 3. YouTube

### Test Case 3.1: YouTube Short

-   **Objective:** Verify the download of a standard YouTube Short.
-   **Link:** `https://www.youtube.com/shorts/9ltKewlM50Y`
-   **Status:** `✅ Pass`

-   **Verification Steps:**
    -   `[x]` The bot responds with a video.
    -   `[x]` The video has a thumbnail and correct duration.
    -   `[x]` The caption is present and correct.

-   **Notes:**
    -   Works as expected.

---

### Test Case 3.2: Standard Video (`youtu.be` format)

-   **Objective:** Verify the quality selection and download for a standard `youtu.be` link.
-   **Link:** `https://youtu.be/UgQFcvYg9Kk`
-   **Status:** `✅ Pass`

-   **Verification Steps:**
    -   `[x]` The bot responds with a message/photo and an inline keyboard for quality selection.
    -   `[x]` Quality selection buttons have the "📹" emoji.
    -   `[x]` After selecting a quality, the bot sends the video.
    -   `[x]` The sent video has the correct metadata and thumbnail.

-   **Notes:**
    -   Works as expected.

---

### Test Case 3.3: Standard Video (`youtube.com` format)

-   **Objective:** Verify the quality selection and download for a standard `youtube.com/watch` link.
-   **Link:** `https://www.youtube.com/watch?v=1uPGgS8qSDY`
-   **Status:** `✅ Pass`

-   **Verification Steps:**
    -   `[x]` The bot responds with a message/photo and an inline keyboard for quality selection.
    -   `[x]` Quality selection buttons have the "📹" emoji.
    -   `[x]` After selecting a quality, the bot sends the video.
    -   `[x]` The sent video has the correct metadata and thumbnail.

-   **Notes:**
    -   Works as expected.

---

### Test Case 3.4: Long Video (>10 minutes)

-   **Objective:** Verify the bot can handle long videos without timeouts and show correct progress.
-   **Link:** `https://www.youtube.com/watch?v=TDv56whosPQ`
-   **Status:** `✅ Pass`

-   **Verification Steps:**
    -   `[x]` The bot shows the quality selection keyboard.
    -   `[x]` After selecting a quality, the bot starts the download and shows progress updates.
    -   `[x]` The full video is successfully sent to the chat.
    -   `[x]` The video duration is correct (~36 minutes).

-   **Notes:**
    -   Successfully downloaded and sent. The process works correctly for long-form content.

---

### Test Case 3.5: Video with Multiple Audio Tracks

-   **Objective:** Verify that the bot offers a language selection after quality selection for multi-language videos.
-   **Link:** `https://www.youtube.com/watch?v=pe_ejTiIcSs`
-   **Status:** `✅ Pass`

-   **Verification Steps:**
    -   `[x]` The bot shows the quality selection keyboard.
    -   `[x]` After selecting a quality (e.g., 1080p), the bot shows a language selection keyboard.
    -   `[x]` After selecting a language, the bot sends the video with the correct audio track.

-   **Notes:**
    -   The language selection flow is working as intended.
