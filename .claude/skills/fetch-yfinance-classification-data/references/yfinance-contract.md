# Yfinance contract

- `identity`: name, quote type, exchange, listing currency, financial currency.
- `equity-classification`: identity plus sector, industry, dividend yield, market capitalization.
- `etf-classification`: identity plus family, category, yield, assets, expense ratio, NAV, holdings summary, sector weights.

Modes are selected from database security type. Symbols must be verified database mappings. Responses remain in memory and are normalized to JSON-safe values.
