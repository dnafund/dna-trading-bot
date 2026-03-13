"""Prompt templates for LLM reflection."""

SYSTEM_PROMPT = """You are a quantitative trading analyst reviewing the performance of an automated EMA-based cryptocurrency trading bot. Your role is to provide actionable insights based on statistical analysis.

Key context about the bot:
- Trades crypto futures (USDT-margined) with leverage
- Entry types: standard_m15, standard_h1, standard_h4, ema610_h1, ema610_h4
- Exit mechanisms: TP1 (partial), TP2 (full), Hard SL, Chandelier Exit, Manual
- Runs 24/7 on multiple symbols

Be concise, data-driven, and specific. Focus on actionable improvements."""


def build_reflection_prompt(
    period_stats: str,
    lifetime_stats: str,
    patterns: str,
    current_config: str,
) -> str:
    """Build the weekly reflection prompt with structured data."""
    return f"""Analyze this trading bot's performance and provide reflection.

## Recent Period Stats
{period_stats}

## Lifetime Stats (for context)
{lifetime_stats}

## Detected Patterns
{patterns}

## Current Config Parameters
{current_config}

Please answer these 5 questions concisely:

### 1. What Worked Well?
Identify the strongest performing areas (entry types, symbols, time windows, sides) and explain WHY they may be working.

### 2. What Failed?
Identify the weakest areas with specific numbers. What's causing the losses?

### 3. Underperforming Areas
Which entry types or symbols are underperforming relative to their potential? Are there areas where the bot takes too many low-quality trades?

### 4. Parameter Suggestions
Based on the data, suggest specific parameter adjustments:
- TP targets (too aggressive? too conservative?)
- Stop loss levels (too tight? too wide?)
- Chandelier Exit settings (period, multiplier)
- Any symbols to blacklist or whitelist
Keep suggestions conservative (max 20% change from current values).

### 5. Risk Concerns
Flag any concerning patterns:
- Excessive loss streaks
- Concentration risk (too many trades on one symbol)
- Deteriorating win rate over time
- Correlation with specific market conditions

Format each answer with bullet points. Be specific with numbers."""
