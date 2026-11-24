"""Job boards worth reading even when the tracker does not point at them. Each was checked to exist in November 2026."""

BOARDS = {(a, s) for a, names in {
    "greenhouse": "agoda airbnb akunacapital asana bitgo bybit coinbase consensys databricks govtech jumptrading monzo okx ripple "
                  "squarepointcapital stripe thunes towerresearchcapital wise worldquant xendit",
    "ashby": "airwallex elevenlabs openai supabase",
    "lever": "binance crypto ninjavan nium",
}.items() for s in names.split()}
