# Polymarket CLOB API Setup Guide

This guide walks you through obtaining and configuring the credentials
required to trade on the Polymarket CLOB (Central Limit Order Book) using
**polymarket-glm** in live mode.

You will need four secrets:

| Variable                  | Source                     |
|---------------------------|----------------------------|
| `PGLM_CLOB_API_KEY`       | Polymarket Settings page   |
| `PGLM_CLOB_API_SECRET`    | Polymarket Settings page   |
| `PGLM_CLOB_API_PASSPHRASE`| Polymarket Settings page   |
| `PGLM_PRIVATE_KEY`        | MetaMask wallet export     |

---

## Step 1 — Create a Polymarket Account

1. Go to [https://polymarket.com](https://polymarket.com) and click **Sign Up**.
2. Connect your Ethereum wallet (MetaMask recommended — see Step 3 for
   setting up MetaMask).
3. Complete any identity verification prompts that Polymarket requires.
4. Deposit USDC into your Polymarket account. You will need a balance
   before you can place live orders.

> **Note:** Polymarket operates on **Polygon (Matic) mainnet** (chain ID
> `137`). Make sure your MetaMask wallet is connected to the Polygon
> network and holds MATIC for gas, and USDC for trading.

---

## Step 2 — Generate Your CLOB API Keys

Polymarket provides three API credentials — **API Key**, **API Secret**,
and **API Passphrase** — that authenticate your requests to the CLOB.

1. Log in to [https://polymarket.com](https://polymarket.com).
2. Navigate to **Settings → API Keys** (or visit
   [https://polymarket.com/settings/api-keys](https://polymarket.com/settings/api-keys)
   directly).
3. Click **Create API Key**.
4. A dialog will display three values:
   - **API Key** — a short identifier (e.g. `a1b2c3d4-e5f6-...`)
   - **API Secret** — a longer secret string
   - **API Passphrase** — an additional passphrase
5. **Copy all three values immediately** and store them somewhere secure.
   The secret and passphrase may not be shown again after you close the
   dialog.
6. If you lose your secret or passphrase, delete the old key and generate
   a new one.

---

## Step 3 — Get Your Ethereum Private Key from MetaMask

The CLOB client needs your wallet's private key to sign orders on-chain.

> ⚠️ **WARNING:** Your private key controls your funds. Never share it,
> never paste it into untrusted websites, and never commit it to version
> control.

### Exporting from MetaMask (browser extension)

1. Open the MetaMask extension in your browser.
2. Click the **three-dot menu** (⋮) next to your account name.
3. Select **Account details**.
4. Click **Show private key**.
5. Enter your MetaMask password when prompted.
6. Click **Hold to reveal private key** — a 64-character hex string
   starting with `0x` will appear.
7. Copy the key and store it securely (e.g. in a password manager).

### Exporting from MetaMask (mobile)

1. Open MetaMask on your phone.
2. Tap your account name at the top.
3. Tap the **three-dot menu** (⋮) → **Export private key**.
4. Enter your PIN.
5. Copy the revealed key.

---

## Step 4 — Configure Your `.env` File

The project reads credentials from a `.env` file via
**pydantic-settings** (prefix `PGLM_`).

1. Copy the example file:

   ```bash
   cp .env.example .env
   ```

2. Open `.env` in your editor and fill in the four credential variables:

   ```bash
   # ── Polymarket API Keys (required for LIVE mode) ────
   PGLM_CLOB_API_KEY=your_api_key_here
   PGLM_CLOB_API_SECRET=your_api_secret_here
   PGLM_CLOB_API_PASSPHRASE=your_api_passphrase_here
   PGLM_PRIVATE_KEY=0x_your_ethereum_private_key_here

   # ── Switch to live mode ─────────────────────────────
   PGLM_MODE=live
   ```

3. **Set strict file permissions** so only your user can read the file:

   ```bash
   chmod 600 .env
   ```

### How the project loads these values

The `Settings` class in `polymarket_glm/config.py` uses
`pydantic-settings` with the `PGLM_` prefix and `.env` file:

- `PGLM_CLOB_API_KEY` → `Settings.clob_api_key` → merged into `ClobConfig.api_key`
- `PGLM_CLOB_API_SECRET` → `Settings.clob_api_secret` → merged into `ClobConfig.api_secret`
- `PGLM_CLOB_API_PASSPHRASE` → `Settings.clob_api_passphrase` → merged into `ClobConfig.api_passphrase`
- `PGLM_PRIVATE_KEY` → `Settings.private_key` → merged into `ClobConfig.private_key`

The `LiveExecutor` validates that all four values are present before
initialising the `ClobClient`. If any value is missing, it raises a
`ValueError` listing the missing keys.

---

## Step 5 — Validate Your Keys with py-clob-client

Before running the full bot, verify that your credentials authenticate
successfully against the CLOB.

### 5a. Install py-clob-client

```bash
pip install py-clob-client==0.34.6
```

### 5b. Quick validation script

Run this from the project root (where `.env` lives):

```python
import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

load_dotenv()  # loads .env into os.environ

host = "https://clob.polymarket.com"
chain_id = 137
key = os.environ["PGLM_PRIVATE_KEY"]

creds = ApiCreds(
    api_key=os.environ["PGLM_CLOB_API_KEY"],
    api_secret=os.environ["PGLM_CLOB_API_SECRET"],
    api_passphrase=os.environ["PGLM_CLOB_API_PASSPHRASE"],
)

client = ClobClient(host, chain_id=chain_id, key=key, creds=creds, signature_type=2)

# If this does not raise, your keys are valid
print("✅ API credentials accepted by CLOB")

# Optionally, check your balance or open orders
try:
    orders = client.get_orders()
    print(f"📂 Open orders: {len(orders)}")
except Exception as e:
    print(f"⚠️ Could not fetch orders (non-fatal): {e}")
```

### 5c. Expected output

```
✅ API credentials accepted by CLOB
📂 Open orders: 0
```

If you see an **authentication error** (HTTP 401 / "Invalid API key"),
double-check that you copied all three values correctly from the
Polymarket Settings page and that the key has not been revoked.

---

## Step 6 — Security Notes

### Never commit secrets to version control

The `.env` file contains your private key and API secrets. It **must not**
be committed to Git.

**Add `.env` to `.gitignore`:**

```gitignore
# Secrets — NEVER commit
.env
.env.*
!.env.example
```

> The current `.gitignore` in this repository does **not** include `.env`
> by default. Add the lines above immediately after creating your `.env`
> file.

### Additional security practices

| Practice | Why |
|---|---|
| `chmod 600 .env` | Prevents other users on the host from reading your keys |
| Use a **dedicated trading wallet** | Limits exposure — never use your main holdings wallet |
| Rotate API keys periodically | Reduces impact of a leaked key; delete old keys in Polymarket Settings |
| Never share your private key | Anyone with it can drain your wallet — no recovery possible |
| Avoid hardcoding keys in code | Always use `.env` or environment variables; the code reads them via pydantic-settings |
| Review `.env.example` before committing | Ensure no real secrets slipped into the example template |

### What to do if a key is compromised

1. **Private key leaked** — immediately transfer all assets to a new
   wallet. There is no way to "rotate" a private key.
2. **API key/secret/passphrase leaked** — delete the compromised key in
   Polymarket Settings → API Keys, then generate a new one. Update your
   `.env` file with the new values.
3. **Git history contains a secret** — use `git filter-branch` or
   `BFG Repo-Cleaner` to rewrite history, then force-push. Rotate the
   compromised credential regardless.

---

## Quick-Start Checklist

- [ ] Polymarket account created and funded (USDC on Polygon)
- [ ] API Key, Secret, and Passphrase generated at Settings → API Keys
- [ ] Ethereum private key exported from MetaMask
- [ ] `.env` file created from `.env.example` with all four values
- [ ] `.env` added to `.gitignore` and `chmod 600 .env` applied
- [ ] Validation script runs without authentication errors
- [ ] `PGLM_MODE=live` set in `.env` (only when ready for real orders)

---

## References

- **Polymarket** — [https://polymarket.com](https://polymarket.com)
- **py-clob-client SDK** — [https://github.com/Polymarket/py-clob-client](https://github.com/Polymarket/py-clob-client) (v0.34.6)
- **CLOB API docs** — [https://docs.polymarket.com](https://docs.polymarket.com)
- **Project config** — `polymarket_glm/config.py` (`ClobConfig`, `Settings`)
- **LiveExecutor** — `polymarket_glm/execution/live_executor.py`
