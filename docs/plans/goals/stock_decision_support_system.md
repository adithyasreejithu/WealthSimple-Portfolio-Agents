# Stock Decision-Support System

## Vision

Build an LLM-based stock research and portfolio decision-support system that helps me make better-informed buy, sell, hold, trim, and add decisions for my investment portfolio. The system should think like a combination of a fund manager, investment banker, equity research analyst, and portfolio analyst. It should not execute trades. Its purpose is to help me research stocks, write investment theses, monitor risks, understand portfolio fit, and store all useful research inside a Karpathy-style LLM wiki knowledge base.

## Core Idea

The system should use a Karpathy-style LLM wiki as the long-term knowledge base for my investing research.

The wiki should store:

* Stock investment theses
* Company research pages
* Buy, sell, hold, trim, and add decisions
* Portfolio classification rules
* Portfolio holdings context
* Financial analysis
* Valuation notes
* Earnings notes
* Insider activity notes
* Options and sentiment notes
* Dividend analysis
* Sector exposure analysis
* CAD vs USD exposure notes
* Risk logs
* Decision history
* Watchlist ideas
* Research sources
* Follow-up questions
* Updated thesis revisions over time

The goal is not just to answer one-off stock questions. The goal is to continuously build a structured investment knowledge base that improves over time.

## Main Objective

Build an LLM-based stock research and portfolio decision-support system that can:

* Research individual stocks
* Write full investment theses
* Store each thesis in the LLM wiki
* Update existing thesis pages when new information appears
* Compare new research against previous assumptions
* Track why a stock was bought, sold, held, trimmed, or avoided
* Analyze how each stock fits into my portfolio
* Create a reusable record of my investing decisions
* Help me make disciplined decisions instead of emotional trades

The system should help answer:

* Should I buy this stock?
* Should I sell this stock?
* Should I hold this stock?
* Should I trim this position?
* Should I add more to this position?
* Does this stock still fit my portfolio thesis?
* Has the original thesis changed?
* What assumptions need to be monitored?
* What risks are increasing?
* What catalysts are coming up?
* Is this stock overvalued, undervalued, or fairly valued?
* What role does this stock play in my portfolio?

## Knowledge Base Structure

The Karpathy-style LLM wiki should be organized so the agent can search, retrieve, update, and create pages.

Suggested wiki structure:

```text
knowledge-base/
├── portfolio/
│   ├── portfolio-overview.md
│   ├── holdings.md
│   ├── allocation-policy.md
│   ├── sector-exposure.md
│   ├── cad-usd-exposure.md
│   ├── dividend-income.md
│   └── risk-log.md
│
├── stocks/
│   ├── AAPL.md
│   ├── NVDA.md
│   ├── ENB.TO.md
│   └── TICKER.md
│
├── theses/
│   ├── active/
│   ├── closed/
│   ├── watchlist/
│   └── rejected/
│
├── earnings/
│   ├── earnings-calendar.md
│   └── earnings-notes/
│
├── dividends/
│   ├── dividend-calendar.md
│   ├── monthly-income.md
│   └── dividend-safety.md
│
├── market-research/
│   ├── sector-notes/
│   ├── macro-notes/
│   ├── sentiment-notes/
│   └── options-notes/
│
├── taxonomy/
│   ├── portfolio-groups.yml
│   ├── sector-map.yml
│   ├── risk-levels.yml
│   └── decision-framework.yml
│
├── templates/
│   ├── stock-thesis-template.md
│   ├── earnings-note-template.md
│   ├── sell-decision-template.md
│   ├── dividend-analysis-template.md
│   └── portfolio-review-template.md
│
└── logs/
    ├── decision-log.md
    ├── research-log.md
    └── update-log.md
```

## Stock Thesis Page

Each stock should have its own page in the wiki.

Each stock page should include:

* Ticker
* Company name
* Sector
* Industry
* Country
* Currency
* Portfolio role
* Current position status
* Buy price, if available
* Current price, if available
* Target role in portfolio
* Original thesis
* Updated thesis
* Bull case
* Bear case
* Key risks
* Valuation view
* Technical view
* Options and sentiment view
* Insider activity
* Earnings notes
* Dividend notes, if applicable
* Portfolio fit
* Decision history
* Open questions
* Monitoring checklist
* Sources

## Required Research Areas

For each stock, the system should collect and analyze the following.

### 1. Company Overview

The system should understand:

* What the company does
* How it makes money
* Main business segments
* Revenue drivers
* Geographic exposure
* Customer base
* Industry and sector
* Competitive position
* Key competitors
* Growth opportunities
* Business risks
* Whether the business model is durable

### 2. Valuation Analysis

The system should review:

* P/E ratio
* Forward P/E
* PEG ratio
* Price-to-sales
* Price-to-book
* EV/EBITDA
* Free cash flow yield
* Dividend yield, if applicable
* Historical valuation range
* Peer valuation comparison
* Whether the stock appears cheap, expensive, or fairly valued

The system should explain whether the valuation makes sense based on growth, profitability, risk, and market conditions.

### 3. Financial Statement Analysis

The system should analyze:

* Revenue growth
* Gross margin
* Operating margin
* Net income
* EPS growth
* Operating cash flow
* Free cash flow
* Capital expenditures
* Debt levels
* Cash balance
* Share buybacks
* Dividend sustainability
* Return on equity
* Return on invested capital

The system should be able to summarize cash flow statement quality and explain whether the company generates enough cash to support growth, dividends, buybacks, and debt obligations.

### 4. Earnings Calendar and Event Tracking

The system should track:

* Upcoming earnings dates
* Previous earnings results
* EPS beats or misses
* Revenue beats or misses
* Guidance changes
* Analyst expectations
* Management commentary
* Key upcoming catalysts
* Conference calls
* Investor days
* Product launches
* Regulatory events

There should be an earnings calendar agent or module that pulls upcoming earnings dates and flags portfolio holdings that are reporting soon.

### 5. Options and Market Positioning

The system should analyze:

* Call volume
* Put volume
* Put/call ratio
* Open interest
* Implied volatility
* Unusual options activity
* Large call buying
* Large put buying
* Options expiration dates
* Market expectations implied by options pricing

The system should explain whether options activity looks bullish, bearish, or neutral.

### 6. Market Sentiment

The system should track:

* Analyst ratings
* Price target changes
* News headlines
* Earnings reactions
* Social sentiment, if available
* Institutional sentiment
* Sector sentiment
* Market-wide risk appetite

The system should summarize whether sentiment around the stock is improving, worsening, or neutral.

### 7. Technical Analysis

The system should include basic technical analysis such as:

* Current price trend
* Moving averages
* Support levels
* Resistance levels
* Gap fills
* Recent breakout or breakdown
* Relative strength
* Volume trends
* RSI
* MACD, if useful

The system should specifically check whether there are open gaps that may fill and whether the stock is near important technical levels.

### 8. Insider Activity

The system should track:

* Insider buying
* Insider selling
* Size of insider transactions
* Whether transactions are routine or meaningful
* Which executives or directors are buying or selling
* Recent trends in insider ownership

The system should explain whether insider activity supports or weakens the investment thesis.

## Portfolio-Level Analysis

The system should not only analyze individual stocks. It should also analyze how each position fits into my overall portfolio.

Portfolio analysis should include:

* Sector exposure
* Industry exposure
* CAD vs USD exposure
* Growth vs income exposure
* Quality vs speculative exposure
* Dividend income contribution
* Monthly dividend income
* Dividend calendar
* Position concentration
* Portfolio risk exposure
* Portfolio overlap
* Geographic exposure
* Currency exposure
* Correlation between holdings
* Whether a position improves or worsens diversification

The system should determine whether a stock deserves a place in the portfolio based on its role, risk, expected return, and overlap with current holdings.

## Dividend and Income Analysis

For dividend-paying stocks, the system should analyze:

* Dividend yield
* Dividend growth rate
* Payout ratio
* Free cash flow payout ratio
* Dividend safety
* Dividend history
* Monthly, quarterly, or annual payment schedule
* Expected annual income from the position
* Expected monthly income from the total portfolio
* Upcoming ex-dividend dates
* Dividend payment dates

The system should determine whether the stock belongs in the income sleeve of the portfolio.

## Buy / Sell / Hold Decision Framework

For each stock, the system should produce a structured decision.

The output should include:

* Recommendation: Buy, Hold, Sell, Trim, Add, Watchlist, or Avoid
* Confidence level: Low, Medium, or High
* Time horizon: Short-term, medium-term, or long-term
* Portfolio role
* Bull case
* Bear case
* Key risks
* Key catalysts
* Valuation view
* Technical view
* Sentiment view
* Options activity view
* Insider activity view
* Portfolio fit
* Final thesis
* Action plan

The system should clearly explain why a stock should be bought, sold, held, trimmed, added to, or avoided.

## Thesis Update Logic

When researching a stock, the system should first check whether a wiki page already exists.

If a stock thesis already exists:

1. Read the existing thesis.
2. Identify the original assumptions.
3. Pull updated market, financial, valuation, sentiment, insider, technical, and earnings data.
4. Compare new information against the old thesis.
5. Identify what has changed.
6. Update the thesis if needed.
7. Add a dated note to the decision log.
8. Preserve the old thesis history instead of overwriting important reasoning.
9. Clearly state whether the thesis is stronger, weaker, unchanged, or broken.

If a stock thesis does not exist:

1. Create a new stock research page.
2. Create a new investment thesis.
3. Classify the stock into the correct portfolio role.
4. Add the stock to the watchlist, active holdings, rejected list, or research queue.
5. Add a decision entry to the decision log.

## Suggested Agent Structure

The system should be split into multiple agents or modules.

### 1. Discovery Agent

Searches the LLM wiki knowledge base to find existing pages, previous theses, related notes, portfolio rules, and relevant context before new research begins.

### 2. Stock Research Agent

Researches individual companies and produces a full stock report.

### 3. Financial Analysis Agent

Analyzes income statements, balance sheets, cash flow statements, margins, debt, profitability, and free cash flow quality.

### 4. Valuation Agent

Analyzes valuation ratios, peer comparisons, historical ranges, and whether the current price is justified.

### 5. Options and Sentiment Agent

Reviews call/put activity, implied volatility, analyst sentiment, market positioning, and news sentiment.

### 6. Technical Analysis Agent

Reviews price trends, moving averages, support, resistance, RSI, MACD, volume, breakouts, breakdowns, and gap fills.

### 7. Insider Activity Agent

Tracks insider buying and selling and determines whether the activity is meaningful.

### 8. Earnings Calendar Agent

Tracks upcoming earnings dates and flags holdings or watchlist names that are reporting soon.

### 9. Dividend Income Agent

Tracks dividend income, monthly income, dividend safety, ex-dividend dates, and payment schedules.

### 10. Portfolio Analyst Agent

Reviews the overall portfolio and analyzes exposure, concentration, diversification, income, and CAD/USD risk.

### 11. Thesis Writer Agent

Combines all research into a clear investment thesis and final decision.

### 12. Wiki Update Agent

Creates, updates, and maintains the Karpathy-style LLM wiki pages, templates, logs, and indexes.

## Required Tools and Data Sources

The system should be designed with access to tools or data sources for:

* Stock price data
* Historical price data
* Financial statements
* Cash flow statements
* Balance sheets
* Income statements
* Valuation ratios
* Analyst ratings
* Analyst price targets
* Earnings calendar
* Options chain data
* Insider transaction data
* Dividend history
* Ex-dividend dates
* News and sentiment data
* Sector and industry classification
* Currency exposure data
* Portfolio holdings data
* FX rates for CAD/USD analysis
* Local markdown file search
* Markdown file creation and editing
* YAML taxonomy files
* Decision logs
* Research logs
* Source citation tracking

## Expected Output When I Ask About a Stock

When I ask about a stock, the system should return a structured report with:

1. Executive Summary
2. Recommendation
3. Company Overview
4. Financial Analysis
5. Valuation Analysis
6. Technical Analysis
7. Options Activity
8. Market Sentiment
9. Insider Activity
10. Earnings and Catalysts
11. Dividend Analysis, if applicable
12. Portfolio Fit
13. Risks
14. Bull Case
15. Bear Case
16. Final Investment Thesis
17. Action Plan
18. Wiki Update Summary

The action plan should clearly state whether I should buy, sell, hold, trim, add, watch, or avoid the stock.

The wiki update summary should state:

* Which page was created or updated
* What changed in the thesis
* What was added to the decision log
* What needs to be monitored next

## Important Constraints

The system should support investment research and decision-making only. It should not execute trades.

The system should clearly separate:

* Facts
* Assumptions
* Opinions
* Estimates
* Risks
* Unknowns

The system should cite sources where possible and avoid unsupported claims.

The system should preserve historical reasoning so I can understand why I made a decision at the time.

The system should help me become a more disciplined investor by turning research into a reusable knowledge base instead of scattered one-off notes.
