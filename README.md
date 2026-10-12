# Egg classified🥚

An English Python desktop app for reviewing and organizing files. Import documents or a list of names, choose a classification scheme, review suggested folders, and confirm before copying or moving anything.

> **API setup required before classification.** The app starts without an API key or selected model. Enter your own API key and a supported model ID before starting classification. Only the Gemini adapter is currently implemented. We suggest checking Gemini's free API option; availability and search charges depend on your account and current terms. See the [API setup guide](docs/API_SETUP.md). No free quota or successful live API test is promised.

**Version 0.2.2 — public preview, macOS/Linux.** See the [release announcement](https://github.com/david2010616-rgb/egg-classified/releases/tag/v0.2.2) for verification results. Live Gemini requests and Windows support are **not verified**. Windows file operations are not supported. No general-purpose language model is bundled.

**[Download English Python ZIP](https://github.com/david2010616-rgb/egg-classified/releases/download/v0.2.2/egg-classified-0.2.2-English-Python.zip)** · **[Release and checksums](https://github.com/david2010616-rgb/egg-classified/releases/tag/v0.2.2)** · **[API setup guide](docs/API_SETUP.md)**

There is one English edition in this release. A separate Simplified Chinese download is not published.

## What works offline after session setup?

- Read supported documents locally. After entering an API key and model ID for the session, manual schemes, confirmation, preview, copy/move and undo can run without network calls.
- Reuse exact matching user-confirmed examples under the same scheme.
- Train a small local character-trigram Naive Bayes classifier from enough user-confirmed examples, and reload its saved model without an internet connection. The underlying local engine does not use an API key, but this edition requires session API configuration before the GUI classification workflow is unlocked.

A fresh installation has **no learned examples or pretrained general-purpose model**. Offline suggestions may therefore be empty. Local learning is not foundation-model fine-tuning and does not guarantee accuracy. General semantic recognition, automatic scheme suggestions and web search use the **Google Gemini API** and require internet access and a valid key.

## Install and launch

1. Install Python 3.11 or newer **with Tkinter**. Python and a desktop graphical session are required. The local audit used Python 3.14.7 on macOS; see the release announcement for environments actually tested.
2. Download the ZIP above and extract it into a normal writable folder. Do not run it inside an archive viewer.
3. Open Terminal in the extracted `egg-classified-0.2.2` folder and run:

```sh
python3 -m tkinter
python3 -m venv .venv
.venv/bin/python -m pip install ".[ai]"
.venv/bin/python app.py
```

The first command opens a small Tk test window; close it before continuing. On macOS, Python's official installer includes Tkinter. Linux distributions may require their `python3-tk` and `python3-venv` packages. The initial dependency installation normally needs internet access. `pypdf` and `jsonschema` are declared in the package and `requirements-ai.txt`; third-party wheels and Python itself are not bundled.

After setup, start again with `.venv/bin/python app.py`. On macOS, `sh Start.command` also works (double-click may depend on permissions and macOS policy). Installed-package entry points are `.venv/bin/python -m ai_classifier` and `.venv/bin/egg-classified`. No source editing is needed.

**Windows:** do not use this release for organizing files. Safe file operations depend on POSIX directory descriptors, hard links and no-follow flags. The GUI launcher reports this limitation rather than attempting unsafe fallback operations.

## First run and API setup

1. Click **Enter API key**, enter your own Gemini key, and fill **Model** with a supported ID you choose (for example, `gemini-3.8-flash`). There is no preselected model. This saves configuration only; the provider validates credentials on the first online request. Keep **Allow network** and **Allow search** unchecked when using the manual/local workflow below.
2. Select **Import folder**, **Import files**, or **Import names (TXT/CSV)**.
3. Enter a requirement such as “Group by research topic”, then click **Manual scheme**.
4. Select items, type a category in **Category** (use `/` for nested folders), and click **Confirm selected**. This confirmation creates trusted learning examples; it does not move anything.
5. Select an output folder, click **5. Refresh preview**, and inspect every destination.
6. Choose **Copy (keep originals)** or **Move (undo available)**, then click **6. Organize / resume** and review the confirmation dialog. **Undo batch moves** restores moved files if original paths are still free.

Name-list entries do not refer to real local files; use **Export CSV** for those. After collecting examples, reuse the saved scheme in a new batch and select **Offline suggestions**. Suggestions still require your confirmation.

## Online AI classification

Follow the separate **[API setup guide](docs/API_SETUP.md)** to obtain a key and match the interface fields. Only Google Gemini is implemented; there is no provider selector or custom API base URL.

Enter a key with **Enter API key**, enable **Allow network**, import a small non-sensitive batch, and use **1. Recognize / resume → 2. Suggest schemes → 3. Use scheme → 4. Classify / resume**. Confirm results and refresh the preview before organizing files. Enable **Allow search** only after checking your account's search availability and billing. Free text generation does not imply free search.

Network requests send filenames, extensions, text excerpts (up to 6000 characters), recognition summaries, scheme instructions and related confirmed examples. Original file bytes and full local paths are not sent by the request builder. Search can send names, queries and summaries. Treat excerpts as sensitive even though original files are not uploaded. In-flight requests cannot be recalled when networking is disabled.

## Reading, learning and safety

- TXT, Markdown, CSV/TSV, DOCX main text and PDF text layers are supported. Extraction is limited to 32 MiB files, 6000 characters and the first 20 PDF pages. Unsupported formats, images and scanned PDFs show **filename only** unless a developer supplies an OCR adapter. OCR is not bundled.
- AI predictions and user-confirmed labels are stored separately. Only confirmed labels enter trusted learning data. Local training needs at least 30 examples plus independent training/evaluation groups and per-class coverage. Grouped evaluation and model acceptance thresholds can reject a candidate; a rollback is available.
- Classification, network requests and file operations have checkpoints. Completed work can be resumed. Request attempts and search attempts have separate per-batch limits, including retries; these are not monetary limits.
- Destinations are previewed, unsafe paths and symbolic links are rejected, existing files are not overwritten, and moved files have an undo journal. This is not a backup tool: ACLs, extended attributes and Finder tags are not fully preserved. Use one app process and avoid concurrent edits to source/target files.

## Private local data

The English app stores its database, cached responses, excerpts, confirmed examples, models and search evidence **outside the source folder**:

- macOS: `~/Library/Application Support/LocalAIFileClassifier/`
- Linux: `$XDG_DATA_HOME/local-ai-file-classifier/`, or `~/.local/share/local-ai-file-classifier/`
- Optional override: `LAFC_DATA_DIR` points to a private writable directory.

**Enter API key** holds a key in memory for the current session. The GUI requires **Enter API key** for each session. Developer backend/probe tools also accept `GEMINI_API_KEY` from the environment; the app does not automatically load `.env`. Never put a real key in source, screenshots, shell history, reports or issues. The local database is not encrypted. **Clear response cache** removes reusable responses, not saved batch evidence or confirmations.

The private data directory retains its existing name for compatibility with earlier releases. Existing data is not automatically migrated. Close both apps and back up your data before changing `LAFC_DATA_DIR`; never run two processes against the same database.

`.gitignore` excludes common secrets, databases, logs and generated data. Published archives contain only the reviewed public source and user documentation. These controls do not make arbitrary personal files safe to commit: review staged files before publishing, and keep input/output documents outside the repository.

## About this project

This is a small project developed by David Li with help from Codex. Thank you for trying it and for sharing constructive feedback. Updates considered!!!!

Apache-2.0. See [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_LICENSES.md).
