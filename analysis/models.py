"""
Pricing models for Polymarket BTC 5-minute binary options.

Core model: Binary (digital) call option under Black-Scholes.
  Up token price = N(d₂)
  where d₂ = [ln(S/K) + (r - σ²/2)T] / (σ√T)

Key functions:
  - fair_value()    : given vol, what should the option be worth?
  - implied_vol()   : given market price, what vol is implied?
  - add_iv()        : vectorized — adds IV columns to a trades DataFrame
"""

import numpy as np
from scipy.stats import norm

# Seconds in a year — used to annualize the 5-minute windows.
# Our time inputs are in seconds, but Black-Scholes wants annualized T.
SECONDS_PER_YEAR = 365.25 * 24 * 3600


def fair_value(S: float, K: float, T_secs: float, sigma: float, r: float = 0.0) -> float:
    """
    Binary call fair value under Black-Scholes.

    Parameters
    ----------
    S       : current BTC price
    K       : strike price (price_to_beat)
    T_secs  : time remaining in seconds (NOT annualized)
    sigma   : annualized volatility (e.g. 0.35 = 35%)
    r       : risk-free rate (default 0, irrelevant for 5 min)

    Returns
    -------
    float : theoretical Up token price, between 0 and 1
    """
    if T_secs <= 0 or sigma <= 0:
        # At expiry: binary payoff
        return 1.0 if S >= K else 0.0

    T = T_secs / SECONDS_PER_YEAR
    sqrt_T = np.sqrt(T)
    d2 = (np.log(S / K) + (r - sigma**2 / 2) * T) / (sigma * sqrt_T)
    return float(norm.cdf(d2))


def implied_vol(
    market_price: float,
    S: float,
    K: float,
    T_secs: float,
    r: float = 0.0,
    initial_guess: float = 0.5,
    max_iter: int = 100,
    tol: float = 1e-8,
) -> float | None:
    """
    Solve for implied volatility using Newton-Raphson.

    Given an observed market price for the Up token, find the σ that makes
    the Black-Scholes binary call price equal to that market price.

    Newton-Raphson works by:
      1. Start with a guess for σ
      2. Compute model price at that σ
      3. Compute how sensitive the price is to σ (vega)
      4. Adjust: σ_new = σ - (model_price - market_price) / vega
      5. Repeat until convergence

    Returns None if the solver fails to converge (happens when the option
    is deep ITM/OTM or very close to expiry — vega is near zero).
    """
    # Reject unsolvable inputs
    if T_secs <= 0:
        return None
    if market_price <= 0.01 or market_price >= 0.99:
        # Too close to 0 or 1 — price is almost pure intrinsic value,
        # there's no meaningful vol to extract
        return None

    T = T_secs / SECONDS_PER_YEAR
    sqrt_T = np.sqrt(T)
    sigma = initial_guess

    for _ in range(max_iter):
        d2 = (np.log(S / K) + (r - sigma**2 / 2) * T) / (sigma * sqrt_T)
        model_price = norm.cdf(d2)

        # Vega for a binary call: d(N(d₂))/dσ
        # Using chain rule: N'(d₂) * dd₂/dσ
        # dd₂/dσ = -d₂/σ - sqrt_T  (from differentiating d₂ w.r.t. σ)
        pdf_d2 = norm.pdf(d2)
        dd2_dsigma = -d2 / sigma - sqrt_T
        vega = pdf_d2 * dd2_dsigma

        if abs(vega) < 1e-12:
            # Flat region — can't solve. This happens when:
            #   - option is deep ITM/OTM (d2 far from 0, pdf ≈ 0)
            #   - very little time left (sqrt_T ≈ 0)
            return None

        diff = model_price - market_price
        if abs(diff) < tol:
            return sigma  # converged

        sigma -= diff / vega

        # Bounds check — vol can't be negative or absurdly high
        if sigma <= 0.001 or sigma > 50:
            return None

    return None  # didn't converge


def add_iv(trades, price_to_beat: float) -> None:
    """
    Add implied volatility columns to a trades DataFrame (in-place).

    Computes IV from three price points:
      - iv_mid  : from the mid-price (best single estimate)
      - iv_bid  : from up_bid (lower bound of IV band)
      - iv_ask  : from up_ask (upper bound of IV band)

    The IV band (iv_bid to iv_ask) represents the market's uncertainty
    about true vol. A market maker would quote within this band.

    Also adds a 'reliable_iv' boolean column that flags rows where IV
    is trustworthy (not in the noisy early period, not extreme values).
    """
    iv_mid_vals = []
    iv_bid_vals = []
    iv_ask_vals = []

    for _, row in trades.iterrows():
        S = row["btc_price"]
        K = price_to_beat
        T = row["time_remaining"]

        iv_mid_vals.append(implied_vol(row["mid"], S, K, T))
        iv_bid_vals.append(implied_vol(row["up_bid"], S, K, T))
        iv_ask_vals.append(implied_vol(row["up_ask"], S, K, T))

    trades["iv_mid"] = iv_mid_vals
    trades["iv_bid"] = iv_bid_vals
    trades["iv_ask"] = iv_ask_vals

    # Flag reliable IV: solver converged, reasonable range, past the
    # chaotic opening (first ~15 seconds are price discovery noise)
    trades["reliable_iv"] = (
        trades["iv_mid"].notna()
        & (trades["iv_mid"] > 0.05)
        & (trades["iv_mid"] < 2.0)
        & (trades["secs"] > 15)
    )


def add_fair_value(trades, price_to_beat: float, sigma: float) -> None:
    """
    Add model fair value column to a trades DataFrame (in-place).

    Given a fixed volatility assumption, computes what Black-Scholes says
    the Up token should be worth at each point in time. Comparing this to
    the actual market mid reveals where the market is "rich" or "cheap"
    relative to the model.

    Parameters
    ----------
    trades         : DataFrame from db.get_window_data()['trades']
    price_to_beat  : strike price
    sigma          : annualized vol to use (e.g. median IV from add_iv)
    """
    trades["model_fv"] = trades.apply(
        lambda row: fair_value(row["btc_price"], price_to_beat, row["time_remaining"], sigma),
        axis=1,
    )
    # Mispricing: positive means market is above model (rich),
    # negative means market is below model (cheap)
    trades["mispricing"] = trades["mid"] - trades["model_fv"]