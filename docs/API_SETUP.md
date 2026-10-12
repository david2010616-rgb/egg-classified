# Egg classified🥚 — API setup guide

**An API key and explicitly selected model ID are required before starting classification in the GUI.** The app ships with neither a key nor a preselected model. We recommend checking Gemini's free API option, subject to your account and the provider's current terms.

Only Gemini is implemented today. Once session configuration is entered, manual organization and the local learned classifier can operate with networking off. The underlying local algorithm does not call an API. Automatic general-purpose recognition and scheme suggestions use Google Gemini online.

## Supported providers

| Provider | Implementation | Configuration |
| --- | --- | --- |
| Google Gemini Developer API | REST Interactions adapter, JSON-structured responses and optional Google Search | Enter API key, Model, Allow network, Allow search |
| OpenAI, Anthropic, DeepSeek, OpenRouter and custom OpenAI-compatible endpoints | Not implemented | Do not enter these providers' keys; a different model name cannot add provider support |
| Local learned classifier | Runs in-process; no cloud provider | Offline suggestions, Train local classifier, Roll back model |

No provider dropdown, API base URL field, Ollama integration or downloaded language-model runtime is implemented. Live authenticated Gemini requests have not been verified in this audit.

## Obtain a Gemini key

1. Visit [Google AI Studio's API Keys page](https://aistudio.google.com/apikey) and sign in to your own Google account.
2. Create/select a project and create a Gemini API key. If an existing project is missing, import it through AI Studio's Projects page. Follow the current [official key instructions](https://ai.google.dev/gemini-api/docs/api-key); account eligibility and permissions apply.
3. Keep the key private. Do not paste it into this repository, a GitHub issue or a screenshot. Revoke/replace a key in the provider console if it was exposed.
4. Start the app and click **Enter API key**. The masked dialog says **Gemini API key (not saved to files or logs)**. Paste it there. The app keeps it only for that running session. Fill in **Model** with a supported model ID you explicitly choose; classification stays locked until both fields are configured. A nonempty key is not a successful authentication test: the provider validates it on the first actual request.

Developer backend/probe tools accept `GEMINI_API_KEY` through a private environment configured outside source control. The GUI requires **Enter API key** each session. The app does not read `.env` automatically. Avoid typing a literal key into a command that your shell saves in history. `GEMINI_MODEL` optionally chooses the initial model; it is not a credential.

## Match the interface

| UI control | Meaning |
| --- | --- |
| Enter API key | Masked, session-only Gemini credential; not a connection test |
| Model | Gemini model ID; blank initially; explicitly enter a supported ID, such as `gemini-3.8-flash`, without changing code |
| Allow network | Permits new Gemini API requests; off by default |
| Allow search | Separately permits Google Search; also requires Allow network |
| Reuse 7-day cache | Reuses matching cached responses, including offline; clearing it does not erase batch evidence |
| Request limit | Maximum request attempts for this batch, including retries; not a spending cap |
| Search limit | Maximum search request attempts within that batch; one request may involve multiple queries |

The example model `gemini-3.8-flash` is listed in the [official model catalog](https://ai.google.dev/gemini-api/docs/models), checked October 11, 2026. Choose a model supported by your account and the [Interactions API](https://ai.google.dev/api/interactions-api). Entering an unsupported ID can cause an API error; this app does not provision access.

## Try it carefully

Use a few invented filenames or non-sensitive sample documents first. Enable **Allow network**, leave **Allow search** off, then run **1. Recognize / resume**. Enter classification instructions and click **2. Suggest schemes**. Review a scheme, click **3. Use scheme**, then **4. Classify / resume**. Inspect the results, confirm categories and refresh the preview before organizing files. Model output remains untrusted advice.

For web verification, first check search availability and charges in your account. Enable **Allow search** only when you want names, queries and summaries sent for Google Search. Switching networking off prevents new calls; a request already sent may still finish or incur usage.

## Free options and billing

For a supported free API option, start by checking Gemini's free tier in [Google AI Studio](https://aistudio.google.com/) and the [current pricing page](https://ai.google.dev/gemini-api/docs/pricing). Availability depends on the model, region, account and current provider terms; no fixed numerical quotas are promised here. The example model's pricing page currently lists free text generation, but Google Search availability is separate and may require paid access. Keep search disabled while evaluating free text generation.

Check current [rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) and your account's billing controls rather than relying on copied limits. A request-count limit in the app does not guarantee a free bill. Provider data-use terms may differ between free and paid tiers; use non-sensitive examples when testing. After entering the required session configuration, keep networking disabled to use manual schemes/local learning without API calls. Importing files is local; it does not authenticate your API key.

## Data and errors

Gemini receives filenames, extensions, text excerpts, recognition summaries, instructions and related confirmed examples. The request sets `store=false`; that is not a guarantee that the provider keeps no operational logs. Review the provider's terms before sending private content. Local databases and exports may contain sensitive data even though API keys are not saved there.

- **Key not configured:** enter a Gemini key in the current session; the app does not invent a test result.
- **401/403:** check key validity, restrictions, account/project access and region eligibility.
- **404/model unavailable:** compare Model with the current supported model IDs and your account's access.
- **429 or request budget reached:** check provider usage/billing and the app's batch limits; avoid repeated retries or blindly increasing the budget.
- **Network/timeout:** confirm internet access, resume the batch when ready, or use a manual scheme offline.
- **Structured response failed validation:** no result from that response is applied; retry with a supported model or classify manually.

Optional developer probe: explicitly set `GEMINI_MODEL` first, then run `python -m ai_classifier.verify_live --names /path/to/non-sensitive-test-names.txt`. It sends only the names explicitly selected, performs no moves and creates no trusted labels. Without a key it exits 77 with `skipped_no_key`; this is not a successful live API test.

## A personal note

This is a small project developed by a high school student with help from Codex. Thank you for trying it and for your constructive feedback.
